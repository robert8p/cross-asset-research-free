from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import ProjectConfig, load_config, parse_utc, select_instruments
from .db import Database
from .exporter import sha256_file
from .pipeline import Collector
from .quality import evaluate_bars, evaluate_yields
from .storage import SupabaseStorageUploader

ROUND2_BAR_SYMBOLS = [
    "BTC_USD_SPOT",
    "DIA_DJIA_PROXY",
    "EWJ_JAPAN_EQUITY_PROXY",
    "GLD_GOLD_PROXY",
    "IEF_US10Y_RATE_PROXY",
    "IWM_RUSSELL2000_PROXY",
    "QQQ_NASDAQ100_PROXY",
    "SLV_SILVER_PROXY",
    "SPY_SP500_PROXY",
    "TLT_US30Y_RATE_PROXY",
]
ROUND2_YIELD_SYMBOLS = ["US2Y_YIELD", "US5Y_YIELD", "US10Y_YIELD", "US30Y_YIELD"]
ROUND2_ALL_SYMBOLS = ROUND2_BAR_SYMBOLS + ROUND2_YIELD_SYMBOLS
ROUND2_DAYS = 365


@dataclass(frozen=True)
class Round2Bounds:
    start: datetime
    end_exclusive: datetime


def bounds() -> Round2Bounds:
    """Use the year immediately before Round 1; never touch the sealed test period."""
    end_text = os.getenv("RESEARCH_DATASET_START_UTC")
    if not end_text:
        raise RuntimeError("RESEARCH_DATASET_START_UTC is required to anchor Round 2 safely")
    end = parse_utc(end_text)
    return Round2Bounds(start=end - timedelta(days=ROUND2_DAYS), end_exclusive=end)


def selected_instruments(config: ProjectConfig) -> list[dict]:
    return select_instruments(config, symbols=ROUND2_ALL_SYMBOLS)


def backfill(database: Database) -> dict:
    config = load_config()
    selected = selected_instruments(config)
    window = bounds()
    print(
        f"ROUND2: backfilling {window.start.isoformat()} to {window.end_exclusive.isoformat()} "
        f"for {len(selected)} predeclared instruments",
        flush=True,
    )
    summary = Collector(config, database, dry_run=False).run(
        selected,
        window.start,
        window.end_exclusive,
        "round2_historical_corroboration_backfill",
    )
    result = {
        "status": "succeeded" if not summary.failed else "completed_with_failures",
        "round2_start": window.start.isoformat(),
        "round2_end_exclusive": window.end_exclusive.isoformat(),
        **summary.__dict__,
    }
    return result


def _query_instruments(db: Database) -> pd.DataFrame:
    return db.read_dataframe(
        """select * from instruments where canonical_symbol = any(%s) order by canonical_symbol""",
        (ROUND2_ALL_SYMBOLS,),
    )


def _query_bars(db: Database, window: Round2Bounds) -> pd.DataFrame:
    return db.read_dataframe(
        """
        select b.*, i.canonical_symbol, i.canonical_name, i.asset_class, i.subcategory,
               i.instrument_type, i.economic_exposure, i.exchange, i.exchange_timezone,
               i.currency, i.volume_type, i.data_frequency, i.normal_session,
               i.methodological_limitations
        from market_bars b
        join instruments i on i.instrument_id=b.instrument_id
        where i.canonical_symbol = any(%s)
          and b.bar_open_timestamp_utc >= %s
          and b.bar_open_timestamp_utc < %s
        order by i.canonical_symbol, b.bar_open_timestamp_utc
        """,
        (ROUND2_BAR_SYMBOLS, window.start, window.end_exclusive),
    )


def _query_yields(db: Database, window: Round2Bounds) -> pd.DataFrame:
    return db.read_dataframe(
        """
        select y.*, i.canonical_symbol, i.canonical_name, i.asset_class,
               i.instrument_type, i.economic_exposure, i.currency,
               i.methodological_limitations
        from yield_observations y
        join instruments i on i.instrument_id=y.instrument_id
        where i.canonical_symbol = any(%s)
          and y.observation_timestamp_utc >= %s
          and y.observation_timestamp_utc < %s
        order by i.canonical_symbol, y.observation_timestamp_utc
        """,
        (ROUND2_YIELD_SYMBOLS, window.start, window.end_exclusive),
    )


def build_15m_observed_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Aggregate only observed five-minute bars; never synthesize or forward-fill a bar."""
    if bars.empty:
        return pd.DataFrame()
    frame = bars.copy()
    frame["bar_open_timestamp_utc"] = pd.to_datetime(frame["bar_open_timestamp_utc"], utc=True)
    for column in ["open", "high", "low", "close", "volume", "vwap", "trade_count"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    frame["bar_15m_timestamp_utc"] = frame["bar_open_timestamp_utc"].dt.floor("15min")
    grouped = frame.groupby(["canonical_symbol", "bar_15m_timestamp_utc"], observed=True, sort=True)
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        trade_count=("trade_count", "sum"),
        observed_5m_bars=("bar_open_timestamp_utc", "count"),
        first_source_bar_utc=("bar_open_timestamp_utc", "min"),
        last_source_bar_utc=("bar_open_timestamp_utc", "max"),
        exchange_trading_date=("exchange_trading_date", "last"),
        is_regular_session=("is_regular_session", "max"),
        is_extended_session=("is_extended_session", "max"),
    ).reset_index()
    out["complete_15m"] = out["observed_5m_bars"].eq(3)
    out["return_15m"] = np.nan
    prior_close = out.groupby("canonical_symbol", observed=True)["close"].shift()
    prior_time = out.groupby("canonical_symbol", observed=True)["bar_15m_timestamp_utc"].shift()
    prior_complete = out.groupby("canonical_symbol", observed=True)["complete_15m"].shift().astype("boolean").fillna(False)
    valid = (
        out["complete_15m"]
        & prior_complete.astype(bool)
        & ((out["bar_15m_timestamp_utc"] - prior_time) == pd.Timedelta("15min"))
        & prior_close.notna()
        & prior_close.ne(0)
    )
    out.loc[valid, "return_15m"] = out.loc[valid, "close"] / prior_close.loc[valid] - 1.0
    return out


def build_decision_time_reference(bars: pd.DataFrame) -> pd.DataFrame:
    """Create DST-aware reference timestamps for Rob's fixed decision times."""
    if bars.empty:
        return pd.DataFrame()
    spy = bars[bars["canonical_symbol"].eq("SPY_SP500_PROXY")].copy()
    if spy.empty:
        return pd.DataFrame()
    spy["bar_open_timestamp_utc"] = pd.to_datetime(spy["bar_open_timestamp_utc"], utc=True)
    dates = sorted(pd.to_datetime(spy["exchange_trading_date"], errors="coerce").dropna().dt.date.unique())
    london = ZoneInfo("Europe/London")
    rows: list[dict] = []
    for trading_date in dates:
        day = spy[pd.to_datetime(spy["exchange_trading_date"], errors="coerce").dt.date.eq(trading_date)]
        regular = day[day["is_regular_session"].fillna(False).astype(bool)]
        if regular.empty:
            continue
        open_utc = regular["bar_open_timestamp_utc"].min()
        close_utc = pd.to_datetime(regular["bar_close_timestamp_utc"], utc=True).max()
        for label, hour in [("14:00 BST/London", 14), ("17:00 BST/London", 17), ("19:00 BST/London", 19)]:
            local_dt = datetime(trading_date.year, trading_date.month, trading_date.day, hour, 0, tzinfo=london)
            decision_utc = pd.Timestamp(local_dt).tz_convert("UTC")
            rows.append({
                "exchange_trading_date": trading_date.isoformat(),
                "decision_time_label": label,
                "decision_timestamp_utc": decision_utc,
                "us_regular_open_utc": open_utc,
                "us_regular_close_utc": close_utc,
                "minutes_from_us_open": (decision_utc - open_utc).total_seconds() / 60.0,
                "minutes_until_us_close": (close_utc - decision_utc).total_seconds() / 60.0,
                "is_before_us_open": bool(decision_utc < open_utc),
                "is_during_us_regular_session": bool(open_utc <= decision_utc < close_utc),
            })
    return pd.DataFrame(rows)


def _write_hashes(directory: Path) -> None:
    lines = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{sha256_file(path)}  {path.name}")
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _zip(directory: Path, output: Path) -> None:
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(directory.iterdir()):
            if path.is_file():
                archive.write(path, arcname=path.name)


def _write_csv_chunks(frame: pd.DataFrame, directory: Path, stem: str, rows_per_file: int = 250_000) -> None:
    if frame.empty:
        frame.to_csv(directory / f"{stem}_part_001.csv", index=False)
        return
    for part, start in enumerate(range(0, len(frame), rows_per_file), start=1):
        frame.iloc[start:start + rows_per_file].to_csv(directory / f"{stem}_part_{part:03d}.csv", index=False)


def _research_plan() -> str:
    return """# Round 2 — Fixed-Time Historical Corroboration Plan

## Purpose

This round is deliberately different from Round 1. It tests whether economically larger, lower-frequency cross-asset states are useful at the user's actual decision times: **14:00, 17:00 and 19:00 Europe/London**.

## Data boundary

- Uses only the 365 calendar days immediately before Round 1 began.
- Does not query, inspect or summarise the existing untouched test period.
- The existing sealed archive remains the only external test.

## Predeclared scope

Primary bar instruments: BTC-USD, SPY, QQQ, IWM, DIA, GLD, SLV, IEF, TLT and EWJ.
Official daily information: US 2Y, 5Y, 10Y and 30Y yields.

## What changes from Round 1

1. Fixed decision times only; no all-day search for arbitrary entry moments.
2. Horizons of 30, 60, 120 minutes and session close; no reliance on tiny next-five-minute effects.
3. Entry at the next genuinely tradable bar, or the US open for a 14:00 London signal.
4. No IEX-volume or missingness rule may graduate.
5. Candidate generation is capped and predeclared rather than another broad 11,000-variant sweep.
6. A rule needs at least 60 independent trading days, positive net expectancy after 5 bp costs, stability under 10 bp costs, and neighbouring-parameter consistency.

## Hypothesis families

- 14:00 London: overnight BTC return/volatility and prior US-session state predicting the next US open and opening hour.
- 17:00 London: opening-range trend/reversal, equity breadth and equity-duration/gold divergence predicting the next 30/60/120 minutes and close.
- 19:00 London: afternoon trend persistence/reversal and cross-asset risk-state divergence predicting the final two hours and close.
- Daily yield and curve changes as slow-moving regime conditioners, never as intraday observations before their conservative availability time.

## Evidence status

This is retrospective historical corroboration. Even a strong result remains **candidate pending untouched test**. The untouched archive must not be opened unless the complete rule is frozen first.
"""


def _analysis_prompt() -> str:
    return """# Round 2 Cross-Asset Fixed-Time Research Prompt

Analyse only this Round 2 historical-corroboration package. Do not request, open or infer anything from the existing untouched-test archive.

## Objective

Determine whether observable cross-asset states available at exactly 14:00, 17:00 or 19:00 Europe/London predict economically meaningful subsequent moves in the liquid proxy universe.

## Non-negotiable integrity rules

- Use Python for every quantitative calculation.
- Use only information available at or before the decision timestamp.
- Convert Europe/London times with actual daylight-saving rules using `decision_time_reference.csv`.
- For 14:00 signals, freeze the signal at 14:00 and enter US ETFs no earlier than the first eligible US regular-session bar.
- For 17:00 and 19:00 signals, enter at the next available bar after the decision time.
- Never forward-fill a missing bar or convert a closure/no IEX trade into a zero return.
- Do not use IEX volume, trade count or missingness as a candidate predictor; these may be described only as data-quality states.
- Treat every ETF as a labelled proxy, not the native market exposure.
- Do not reuse Round 1 thresholds. Define the Round 2 search space before inspecting outcomes.

## Predeclared targets and horizons

Targets: SPY, QQQ, IWM, DIA, GLD, SLV, IEF, TLT and EWJ.

At 14:00 London evaluate:
- US open to +30 minutes
- US open to +60 minutes
- US open to +120 minutes
- US open to session close

At 17:00 and 19:00 London evaluate:
- 30 minutes
- 60 minutes
- 120 minutes where the session permits
- session close

For every target/horizon calculate signed return, direction, economically meaningful threshold exceedance, MFE, MAE and net return after costs.

## Predeclared predictor families

1. Overnight BTC state: 4h, 8h and 12h return; realised volatility; acceleration; drawdown from trailing high.
2. US opening state: opening gap versus prior close; first 30/60/120-minute return; opening-range width; cross-sectional breadth among SPY/QQQ/IWM/DIA.
3. Cross-asset divergence: equities versus TLT/IEF; equities versus GLD/SLV; large-cap versus small-cap; BTC versus equities.
4. Persistence/reversal: whether aligned instruments agree on direction, whether dispersion is widening, and whether the current move is unusually large relative to trailing same-time history.
5. Slow regimes: trailing-only official-yield and curve changes available by the timestamp; prior-session realised volatility; day-of-week and session position.

## Search discipline

- Cap the effective search at 500 variants.
- Use an initial chronological exploration block, a 10-session embargo, and a final chronological validation block containing at least 60 sessions when available.
- Fit thresholds, normalisation and models only on exploration data.
- Use day/session-level resampling, block bootstrap and false-discovery-rate control.
- Treat neighbouring thresholds/horizons as related tests.
- Use next-bar/open execution and 5 bp round-trip costs as the baseline; stress at 10 bp.
- Do not graduate a rule with fewer than 60 independent days, fewer than 80 events, gross mean below 10 bp, non-positive net expectancy after 5 bp, or material sign instability.

## Models

Prefer transparent conditional tables, event studies, regularised logistic/linear models and shallow trees. Machine-learning feature importance is not evidence. Do not perform an unrestricted indicator sweep.

## Required outputs

1. Integrity and coverage verdict
2. Exact chronological split
3. Predeclared search-space table and effective variant count
4. Results by 14:00, 17:00 and 19:00 decision time
5. Net expectancy after 5 and 10 bp costs
6. Day-level confidence intervals and FDR controls
7. Parameter-neighbour and regime stability
8. Rejected relationships and why they failed
9. Candidate rules, if any, with fully frozen entry, exit, thresholds, exclusions and costs
10. `candidate_rules_round2.json`
11. `analysis_audit_round2.json`
12. Reproducible Python script
13. Plain-English report
14. Explicit decision on whether any rule justifies opening the existing untouched archive

A valid result is that no rule qualifies. Do not rescue weak findings by changing thresholds after validation.
"""


def create_export(database: Database) -> dict:
    config = load_config()
    window = bounds()
    output_root = config.root / config.settings["exports"].get("output_directory", "exports")
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    output_zip = output_root / f"cross_asset_ROUND2_historical_corroboration_{stamp}.zip"

    print("ROUND2 EXPORT: loading disjoint pre-Round-1 history", flush=True)
    instruments = _query_instruments(database)
    bars = _query_bars(database, window)
    yields = _query_yields(database, window)
    if bars.empty:
        raise RuntimeError("Round 2 contains no market bars; run round2-backfill first")
    print(f"ROUND2 EXPORT: loaded {len(bars):,} bars and {len(yields):,} yield rows", flush=True)

    bar_quality = evaluate_bars(bars, instruments, config.settings, coverage_start=window.start, coverage_end_exclusive=window.end_exclusive)
    yield_issues = evaluate_yields(yields)
    quality = pd.concat([bar_quality.issues, yield_issues], ignore_index=True, sort=False)
    bars_15m = build_15m_observed_bars(bars)
    decision_times = build_decision_time_reference(bars)

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary) / "round2"
        directory.mkdir()
        bars.to_parquet(directory / "bars_raw.parquet", index=False, compression="zstd")
        bars_15m.to_parquet(directory / "bars_observed_15m.parquet", index=False, compression="zstd")
        yields.to_parquet(directory / "yields.parquet", index=False, compression="zstd")
        instruments.to_csv(directory / "instruments.csv", index=False)
        decision_times.to_csv(directory / "decision_time_reference.csv", index=False)
        bar_quality.coverage.to_csv(directory / "coverage_summary.csv", index=False)
        quality.to_csv(directory / "data_quality_results.csv", index=False)
        _write_csv_chunks(bars, directory, "bars_raw")
        _write_csv_chunks(bars_15m, directory, "bars_observed_15m")
        _write_csv_chunks(yields, directory, "yields")
        (directory / "ROUND2_RESEARCH_PLAN.md").write_text(_research_plan(), encoding="utf-8")
        (directory / "analysis_prompt_round2.md").write_text(_analysis_prompt(), encoding="utf-8")
        (directory / "README.md").write_text(
            "# Round 2 historical corroboration package\n\n"
            "This archive uses only the 365 days immediately before Round 1 began. "
            "It does not contain or query the existing untouched-test period. Start with "
            "`analysis_prompt_round2.md`.\n",
            encoding="utf-8",
        )
        lessons = {
            "round1_candidate_count": 0,
            "round1_effective_variants": 11432,
            "round1_validation_sessions": 13,
            "lessons": [
                "Tiny next-five-minute effects were not economically executable.",
                "The free IEX feed is sparse for lower-liquidity proxies.",
                "IEX volume and missingness are venue artefacts and are excluded as Round 2 candidate predictors.",
                "Research must be aligned to 14:00, 17:00 and 19:00 Europe/London.",
                "The sealed 2026-06-16 to 2026-07-16 test period remains unopened.",
            ],
        }
        (directory / "round1_lessons.json").write_text(json.dumps(lessons, indent=2), encoding="utf-8")
        manifest = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "classification": "ROUND2_HISTORICAL_CORROBORATION_NOT_EXTERNAL_TEST",
            "round2_start": window.start.isoformat(),
            "round2_end_exclusive": window.end_exclusive.isoformat(),
            "relationship_to_round1": "Disjoint earlier history ending exactly when Round 1 begins.",
            "existing_untouched_test_queried": False,
            "existing_untouched_test_start": os.getenv("UNTOUCHED_START_UTC"),
            "symbols": ROUND2_ALL_SYMBOLS,
            "raw_bar_rows": int(len(bars)),
            "observed_15m_rows": int(len(bars_15m)),
            "yield_rows": int(len(yields)),
            "decision_time_rows": int(len(decision_times)),
            "timestamp_convention": "UTC bar-open; decision times generated with Europe/London DST rules.",
            "missingness_policy": "No forward fill; 15-minute bars aggregate observed five-minute bars only and carry a completeness flag.",
            "volume_warning": "Alpaca free-feed volume and trade count are IEX-only and may not generate a candidate rule.",
            "python_version": sys.version,
            "platform": platform.platform(),
            "configuration_sha256": config.hash(),
            "files": {},
        }
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name not in {"export_manifest.json", "SHA256SUMS.txt"}:
                manifest["files"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        (directory / "export_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _write_hashes(directory)
        _zip(directory, output_zip)

    print(f"ROUND2 EXPORT: archive ready ({output_zip.stat().st_size:,} bytes)", flush=True)
    uploads = []
    if os.getenv("SUPABASE_STORAGE_UPLOAD", "false").lower() in {"1", "true", "yes"}:
        print("ROUND2 UPLOAD: sending archive to private Supabase Storage", flush=True)
        uploads.append(SupabaseStorageUploader().upload(output_zip))
        print("ROUND2 UPLOAD: complete", flush=True)
    return {
        "status": "succeeded",
        "round2_archive": str(output_zip),
        "round2_start": window.start.isoformat(),
        "round2_end_exclusive": window.end_exclusive.isoformat(),
        "raw_bar_rows": int(len(bars)),
        "observed_15m_rows": int(len(bars_15m)),
        "yield_rows": int(len(yields)),
        "storage_uploads": uploads,
        "existing_untouched_test_queried": False,
    }
