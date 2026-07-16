from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .session_utils import build_market_sessions


@dataclass
class QualityResult:
    issues: pd.DataFrame
    coverage: pd.DataFrame
    checked_rows: int


def _issue(instrument_id: Any, timestamp: Any, issue_type: str, severity: str, observed: Any, expected: str, resolution: str = "review", disposition: str = "retained") -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "issue_timestamp": timestamp,
        "issue_type": issue_type,
        "severity": severity,
        "observed_value": str(observed),
        "expected_condition": expected,
        "resolution": resolution,
        "disposition": disposition,
        "original_value_json": None,
        "corrected_value_json": None,
    }


def _empty_issues() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "instrument_id", "issue_timestamp", "issue_type", "severity", "observed_value",
        "expected_condition", "resolution", "disposition", "original_value_json",
        "corrected_value_json",
    ])


def preinsert_bar_checks(bars: pd.DataFrame, instrument: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reject only structurally unusable rows; unusual market observations are retained and flagged."""
    if bars.empty:
        return bars.copy(), _empty_issues()
    df = bars.copy()
    issues: list[dict[str, Any]] = []

    for col in ("bar_open_timestamp_utc", "bar_close_timestamp_utc"):
        if col not in df:
            df[col] = pd.NaT
        else:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    if len(df) > 1 and df["bar_open_timestamp_utc"].dropna().is_monotonic_decreasing:
        issues.append(_issue(None, df["bar_open_timestamp_utc"].dropna().iloc[0], "provider_rows_reverse_order", "warning", "descending", "Provider rows should be normalised to ascending order", "sort before validation and preserve audit issue"))

    required = ["bar_open_timestamp_utc", "bar_close_timestamp_utc", "open", "high", "low", "close"]
    for col in required:
        if col not in df:
            df[col] = np.nan
    if "volume" not in df:
        df["volume"] = np.nan

    malformed = df[required].isna().any(axis=1)
    for idx in df.index[malformed]:
        issues.append(_issue(None, df.at[idx, "bar_open_timestamp_utc"], "missing_or_malformed_required_field", "error", df.loc[idx, required].to_dict(), "All required fields present and timestamps parseable", "exclude structurally unusable row", "excluded"))

    numeric = ["open", "high", "low", "close", "volume"]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    malformed = malformed | df[["open", "high", "low", "close"]].isna().any(axis=1)

    invalid_ohlc = (~malformed) & ((df["high"] < df[["open", "close", "low"]].max(axis=1)) | (df["low"] > df[["open", "close", "high"]].min(axis=1)))
    for idx in df.index[invalid_ohlc]:
        issues.append(_issue(None, df.at[idx, "bar_open_timestamp_utc"], "invalid_ohlc", "error", df.loc[idx, ["open", "high", "low", "close"]].to_dict(), "high >= max(open,low,close) and low <= min(open,high,close)", "exclude pending source verification", "excluded"))

    negative_price = (~malformed) & (df[["open", "high", "low", "close"]] < 0).any(axis=1)
    for idx in df.index[negative_price]:
        issues.append(_issue(None, df.at[idx, "bar_open_timestamp_utc"], "negative_price", "warning", df.loc[idx, ["open", "high", "low", "close"]].to_dict(), "Verify whether negative values are economically valid for this contract", "source/adjacent-bar review; do not auto-delete"))

    negative_volume = df["volume"].notna() & (df["volume"] < 0)
    for idx in df.index[negative_volume]:
        issues.append(_issue(None, df.at[idx, "bar_open_timestamp_utc"], "negative_volume", "error", df.at[idx, "volume"], "volume >= 0", "exclude malformed row", "excluded"))

    wrong_interval = (~malformed) & ((df["bar_close_timestamp_utc"] - df["bar_open_timestamp_utc"]) != pd.Timedelta(minutes=5))
    for idx in df.index[wrong_interval]:
        issues.append(_issue(None, df.at[idx, "bar_open_timestamp_utc"], "unexpected_interval_length", "error", df.at[idx, "bar_close_timestamp_utc"] - df.at[idx, "bar_open_timestamp_utc"], "Exactly five minutes", "exclude malformed bar", "excluded"))

    off_boundary = (~malformed) & (
        df["bar_open_timestamp_utc"].dt.minute.mod(5).ne(0)
        | df["bar_open_timestamp_utc"].dt.second.ne(0)
        | df["bar_open_timestamp_utc"].dt.microsecond.ne(0)
    )
    for idx in df.index[off_boundary]:
        issues.append(_issue(None, df.at[idx, "bar_open_timestamp_utc"], "inconsistent_bar_boundary", "error", df.at[idx, "bar_open_timestamp_utc"], "UTC open timestamp aligned to a five-minute boundary", "exclude until provider convention is reconciled", "excluded"))

    identity = [c for c in ["bar_open_timestamp_utc", "contract_code", "is_continuous"] if c in df]
    duplicate = df.duplicated(identity, keep=False) if identity else pd.Series(False, index=df.index)
    for idx in df.index[duplicate]:
        issues.append(_issue(None, df.at[idx, "bar_open_timestamp_utc"], "duplicated_provider_page_or_bar", "error", {c: df.at[idx, c] for c in identity}, "Unique bar identity within provider batch", "retain one conflict-safe row and audit duplicate", "deduplicated"))

    reject = malformed | invalid_ohlc | negative_volume | wrong_interval | off_boundary
    clean = df.loc[~reject].copy()
    if identity:
        clean = clean.drop_duplicates(identity, keep="last")
    clean = clean.sort_values("bar_open_timestamp_utc").reset_index(drop=True)
    clean["quality_status"] = np.where(clean[["open", "high", "low", "close"]].lt(0).any(axis=1), "warning", "preinsert_pass")
    return clean, pd.DataFrame(issues) if issues else _empty_issues()


def _merged_instrument_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(meta)
    raw = merged.get("metadata_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


def _calendar_expected(
    meta: dict[str, Any],
    start: pd.Timestamp,
    end_exclusive: pd.Timestamp,
) -> tuple[int, dict[Any, set[pd.Timestamp]], pd.DataFrame]:
    """Return expected regular-session bar opens without creating synthetic market rows."""
    if not meta.get("session_calendar") and str(meta.get("normal_session", "")).strip() != "24/7":
        return 0, {}, pd.DataFrame()
    sessions = build_market_sessions(
        meta,
        start.to_pydatetime(),
        end_exclusive.to_pydatetime(),
    )
    expected_by_date: dict[Any, set[pd.Timestamp]] = {}
    total = 0
    for _, session in sessions.iterrows():
        open_ts = max(pd.Timestamp(session["regular_open_utc"]), start)
        close_ts = min(pd.Timestamp(session["regular_close_utc"]), end_exclusive)
        if close_ts <= open_ts:
            continue
        expected = pd.date_range(open_ts.ceil("5min"), close_ts, freq="5min", inclusive="left", tz="UTC")
        metadata = session.get("metadata_json") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        break_start = pd.to_datetime(metadata.get("break_start_utc"), utc=True, errors="coerce")
        break_end = pd.to_datetime(metadata.get("break_end_utc"), utc=True, errors="coerce")
        if pd.notna(break_start) and pd.notna(break_end):
            expected = expected[(expected < break_start) | (expected >= break_end)]
        values = set(pd.Timestamp(x) for x in expected)
        expected_by_date[session["exchange_trading_date"]] = values
        total += len(values)
    return total, expected_by_date, sessions


def evaluate_bars(
    bars: pd.DataFrame,
    instruments: pd.DataFrame,
    settings: dict[str, Any],
    coverage_start: datetime | pd.Timestamp | None = None,
    coverage_end_exclusive: datetime | pd.Timestamp | None = None,
) -> QualityResult:
    if bars.empty:
        return QualityResult(_empty_issues(), pd.DataFrame(), 0)
    df = bars.copy()
    df["bar_open_timestamp_utc"] = pd.to_datetime(df["bar_open_timestamp_utc"], utc=True, errors="coerce")
    df["bar_close_timestamp_utc"] = pd.to_datetime(df["bar_close_timestamp_utc"], utc=True, errors="coerce")
    df = df.sort_values(["instrument_id", "bar_open_timestamp_utc", "contract_code"], na_position="last")
    instrument_map = instruments.set_index("instrument_id").to_dict("index") if not instruments.empty else {}
    issues: list[dict[str, Any]] = []
    stale_n = int(settings["quality"].get("stale_run_bars", 12))
    zero_volume_n = int(settings["quality"].get("zero_volume_run_bars", 24))
    extreme = float(settings["quality"].get("extreme_return_abs_threshold", 0.10))

    required = ["bar_open_timestamp_utc", "bar_close_timestamp_utc", "open", "high", "low", "close"]
    malformed = df[required].isna().any(axis=1)
    for _, r in df[malformed].iterrows():
        issues.append(_issue(r.get("instrument_id"), r.get("bar_open_timestamp_utc"), "missing_or_malformed_required_field", "error", r[required].to_dict(), "Required values present and parseable"))

    duplicate = df.duplicated(["instrument_id", "interval", "bar_open_timestamp_utc", "contract_code", "is_continuous"], keep=False)
    for _, r in df[duplicate].iterrows():
        issues.append(_issue(r.instrument_id, r.bar_open_timestamp_utc, "duplicate_identity", "error", r.get("contract_code"), "Unique instrument/interval/timestamp/contract/continuous identity", "deduplicate at source"))

    valid_numeric = ~malformed
    invalid = valid_numeric & ((df["high"] < df[["open", "low", "close"]].max(axis=1)) | (df["low"] > df[["open", "high", "close"]].min(axis=1)))
    zero_price = valid_numeric & (df[["open", "high", "low", "close"]] == 0).any(axis=1)
    negative_price = valid_numeric & (df[["open", "high", "low", "close"]] < 0).any(axis=1)
    negative_volume = df.get("volume", pd.Series(np.nan, index=df.index)).notna() & (df.get("volume", 0) < 0)
    wrong_interval = valid_numeric & ((df["bar_close_timestamp_utc"] - df["bar_open_timestamp_utc"]) != pd.Timedelta(minutes=5))
    off_boundary = df["bar_open_timestamp_utc"].notna() & (
        df["bar_open_timestamp_utc"].dt.minute.mod(5).ne(0)
        | df["bar_open_timestamp_utc"].dt.second.ne(0)
        | df["bar_open_timestamp_utc"].dt.microsecond.ne(0)
    )
    for mask, kind, severity, expected in [
        (invalid, "invalid_ohlc", "error", "Valid OHLC ordering"),
        (zero_price, "zero_price", "error", "Non-zero traded price"),
        (negative_price, "negative_price", "warning", "Economically valid sign and verified source value"),
        (negative_volume, "negative_volume", "error", "volume >= 0"),
        (wrong_interval, "unexpected_interval_length", "error", "Exactly five minutes"),
        (off_boundary, "inconsistent_bar_boundary", "error", "Five-minute UTC boundary"),
    ]:
        for _, r in df[mask].iterrows():
            observed = {k: r.get(k) for k in ("open", "high", "low", "close", "volume")}
            issues.append(_issue(r.instrument_id, r.bar_open_timestamp_utc, kind, severity, observed, expected, "source and adjacent-bar verification; do not auto-delete extremes"))

    coverage_rows: list[dict[str, Any]] = []
    for instrument_id, group in df.groupby("instrument_id", sort=False):
        group = group.sort_values("bar_open_timestamp_utc").copy()
        meta = _merged_instrument_metadata(instrument_map.get(instrument_id, {}))
        is_247 = str(meta.get("normal_session", "")) == "24/7"
        symbol = meta.get("canonical_symbol")

        returns = group["close"].pct_change(fill_method=None)
        for idx in group.index[(returns.abs() > extreme).fillna(False)]:
            r = group.loc[idx]
            issue_type = "contract_roll_jump" if bool(r.get("is_roll_affected", False)) else "extreme_return"
            issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, issue_type, "warning", float(returns.loc[idx]), f"Absolute one-bar return <= {extreme:.1%} unless verified", "check source, adjacent bars, units and roll; preserve original"))

        if settings["quality"].get("flag_weekends_for_non_247", True) and not is_247:
            for _, r in group[group["bar_open_timestamp_utc"].dt.dayofweek >= 5].iterrows():
                issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, "unexpected_weekend_observation", "warning", r.bar_open_timestamp_utc.day_name(), "Verify exchange session and timezone"))

        timezone_name = meta.get("exchange_timezone")
        if timezone_name and "exchange_trading_date" in group:
            try:
                local_dates = group["bar_open_timestamp_utc"].dt.tz_convert(ZoneInfo(str(timezone_name))).dt.date
                stated_dates = pd.to_datetime(group["exchange_trading_date"], errors="coerce").dt.date
                mismatch = stated_dates.notna() & pd.Series(local_dates, index=group.index).ne(stated_dates)
                for _, r in group[mismatch].iterrows():
                    issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, "timezone_or_trading_date_mismatch", "warning", r.get("exchange_trading_date"), f"Local calendar date in {timezone_name}", "verify overnight-session trading-date convention before correction"))
            except Exception as exc:
                issues.append(_issue(instrument_id, group["bar_open_timestamp_utc"].min(), "invalid_exchange_timezone", "error", timezone_name, "Valid IANA timezone", f"configuration review: {exc}"))

        if "session_type" in group:
            for _, r in group[group["session_type"].eq("scheduled_break_observation")].iterrows():
                issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, "timestamp_inside_scheduled_break", "error", r.get("session_type"), "No bars during a calendar-defined market break", "verify provider timestamp convention or exchange interruption"))
            for _, r in group[group["session_type"].eq("outside_scheduled_market")].iterrows():
                issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, "timestamp_outside_expected_session", "warning", r.get("exchange_trading_date"), "Bar belongs to a configured regular or extended trading date", "verify calendar version, trading-date roll and provider timestamp"))

        if "is_partial_bar" in group:
            for _, r in group[group["is_partial_bar"].fillna(False)].iterrows():
                issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, "partial_bar", "warning", True, "Complete five-minute source interval", "exclude from research features unless explicitly handled"))

        run_id = group["close"].ne(group["close"].shift()).cumsum()
        run_sizes = group.groupby(run_id)["close"].transform("size")
        for _, r in group[run_sizes >= stale_n].iterrows():
            issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, "stale_repeated_price", "warning", r.close, f"Fewer than {stale_n} identical consecutive closes", "verify whether market was active"))

        if "volume" in group and group["volume"].notna().any():
            zero = group["volume"].eq(0)
            zero_runs = zero.ne(zero.shift()).cumsum()
            zero_sizes = group.groupby(zero_runs)["volume"].transform("size")
            for _, r in group[zero & (zero_sizes >= zero_volume_n)].iterrows():
                issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, "excessive_zero_volume_run", "warning", r.volume, f"Fewer than {zero_volume_n} consecutive zero-volume observed bars", "verify active session"))

        if "is_roll_affected" in group:
            for _, r in group[group["is_roll_affected"].fillna(False)].iterrows():
                issues.append(_issue(instrument_id, r.bar_open_timestamp_utc, "contract_roll_boundary", "info", r.get("contract_code"), "Roll boundary explicitly flagged", "exclude or handle roll-aware returns"))

        # Use the version-locked exchange calendar when present. This avoids treating
        # scheduled breaks, weekends and holidays as missing market data. No prices are filled.
        group["_trading_date"] = group.get("exchange_trading_date", group["bar_open_timestamp_utc"].dt.date)
        observed = len(group)
        dates = group["_trading_date"].nunique()
        observed_start = group["bar_open_timestamp_utc"].min()
        observed_end = group["bar_open_timestamp_utc"].max()
        requested_start = pd.Timestamp(coverage_start or observed_start)
        requested_end = pd.Timestamp(coverage_end_exclusive or (observed_end + pd.Timedelta(minutes=5)))
        requested_start = requested_start.tz_localize("UTC") if requested_start.tzinfo is None else requested_start.tz_convert("UTC")
        requested_end = requested_end.tz_localize("UTC") if requested_end.tzinfo is None else requested_end.tz_convert("UTC")

        expected, expected_by_date, session_rows = _calendar_expected(meta, requested_start, requested_end)
        missing_inside = 0
        missing_sessions = 0
        if expected_by_date:
            regular = group[group.get("is_regular_session", pd.Series(True, index=group.index)).fillna(False)]
            observed_by_date = {
                label: set(pd.Timestamp(x) for x in day["bar_open_timestamp_utc"])
                for label, day in regular.groupby("_trading_date", dropna=False)
            }
            for label, expected_timestamps in expected_by_date.items():
                absent = sorted(expected_timestamps - observed_by_date.get(label, set()))
                if not absent:
                    continue
                missing_inside += len(absent)
                missing_sessions += 1
                issues.append(_issue(
                    instrument_id, absent[0], "missing_bars_in_scheduled_session", "warning",
                    {"exchange_trading_date": str(label), "missing_count": len(absent),
                     "first_missing": absent[0].isoformat(), "last_missing": absent[-1].isoformat()},
                    "All provider bars expected within the configured exchange schedule",
                    "investigate API truncation, rate limiting, genuine halt or calendar mismatch; do not forward-fill",
                ))
            basis = f"configured exchange calendar {meta.get('session_calendar')} with scheduled breaks removed"
            observed_for_coverage = int(regular["bar_open_timestamp_utc"].isin(set().union(*expected_by_date.values())).sum()) if expected_by_date else 0
        else:
            # Conservative fallback for an instrument without a verified calendar.
            for _, day in group.groupby("_trading_date", dropna=False):
                day = day.sort_values("bar_open_timestamp_utc")
                delta = day["bar_open_timestamp_utc"].diff()
                for idx in day.index[(delta > pd.Timedelta(minutes=5)).fillna(False)]:
                    missing = max(0, int(delta.loc[idx] / pd.Timedelta(minutes=5)) - 1)
                    missing_inside += missing
                    issues.append(_issue(instrument_id, day.loc[idx, "bar_open_timestamp_utc"], "missing_bars_inside_observed_session", "warning", {"gap": str(delta.loc[idx]), "estimated_missing_bars": missing}, "Five-minute continuity inside observed daily envelope", "investigate API truncation, rate limiting or genuine halt; do not forward-fill"))
            if is_247 and pd.notna(observed_start) and pd.notna(observed_end):
                expected = int(((observed_end + pd.Timedelta(minutes=5) - observed_start) / pd.Timedelta(minutes=5)))
                basis = "24x7 observed-boundary grid; no verified calendar metadata"
            else:
                per_day = group.groupby("_trading_date")["bar_open_timestamp_utc"].agg(["min", "max"])
                expected = int((((per_day["max"] - per_day["min"]) / pd.Timedelta(minutes=5)) + 1).sum())
                basis = "observed daily session envelope; cannot infer missing edge bars"
            observed_for_coverage = observed

        coverage_rows.append({
            "instrument_id": instrument_id,
            "canonical_symbol": symbol,
            "calendar_start": requested_start,
            "calendar_end_exclusive": requested_end,
            "trading_days_observed": dates,
            "scheduled_sessions": len(session_rows) if not session_rows.empty else np.nan,
            "valid_5m_observations_total": observed,
            "valid_5m_observations_in_expected_session": observed_for_coverage,
            "expected_observations": expected,
            "missing_expected_observations": max(0, expected - observed_for_coverage) if expected else np.nan,
            "sessions_with_missing_bars": missing_sessions if expected_by_date else np.nan,
            "missing_inside_observed_envelope": missing_inside,
            "coverage_percentage": (100.0 * observed_for_coverage / expected) if expected else np.nan,
            "expected_basis": basis,
        })

    return QualityResult(pd.DataFrame(issues) if issues else _empty_issues(), pd.DataFrame(coverage_rows), len(df))


def evaluate_yields(yields: pd.DataFrame) -> pd.DataFrame:
    if yields.empty:
        return _empty_issues()
    df = yields.copy()
    issues: list[dict[str, Any]] = []
    df["observation_timestamp_utc"] = pd.to_datetime(df["observation_timestamp_utc"], utc=True, errors="coerce")
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    malformed = df["observation_timestamp_utc"].isna() | df["published_at"].isna() | pd.to_numeric(df["yield_value"], errors="coerce").isna()
    for _, r in df[malformed].iterrows():
        issues.append(_issue(r.get("instrument_id"), r.get("observation_timestamp_utc"), "malformed_yield_observation", "error", r.to_dict(), "Valid availability timestamp, publication timestamp and numeric yield"))
    future_info = (~malformed) & (df["observation_timestamp_utc"] < df["published_at"])
    for _, r in df[future_info].iterrows():
        issues.append(_issue(r.instrument_id, r.observation_timestamp_utc, "future_information_timestamp", "error", r.published_at, "Analysis availability timestamp must be at or after publication timestamp"))
    identity = [c for c in ["instrument_id", "observation_date", "maturity", "vintage_date"] if c in df]
    duplicate = df.duplicated(identity, keep=False) if identity else pd.Series(False, index=df.index)
    for _, r in df[duplicate].iterrows():
        issues.append(_issue(r.get("instrument_id"), r.get("observation_timestamp_utc"), "duplicate_yield_vintage", "error", {c: r.get(c) for c in identity}, "Unique point-in-time yield vintage"))
    revised = df.get("is_revised", pd.Series(False, index=df.index)).fillna(False)
    for _, r in df[revised].iterrows():
        issues.append(_issue(r.instrument_id, r.observation_timestamp_utc, "revised_yield_observation", "info", r.get("vintage_date"), "Revision/vintage explicitly retained", "use point-in-time vintage"))
    return pd.DataFrame(issues) if issues else _empty_issues()
