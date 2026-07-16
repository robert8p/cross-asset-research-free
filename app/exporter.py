from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import ProjectConfig
from .db import Database
from .quality import evaluate_bars, evaluate_yields


@dataclass(frozen=True)
class SplitDefinition:
    dataset_start: datetime
    discovery_start: datetime
    discovery_end: datetime
    untouched_start: datetime
    untouched_end: datetime


@dataclass(frozen=True)
class ExportResult:
    discovery_archive: Path
    untouched_archive: Path
    full_archive: Path | None
    split: SplitDefinition


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def determine_split(min_time: datetime, max_time: datetime, settings: dict[str, Any], explicit_untouched_start: datetime | None = None) -> SplitDefinition:
    if min_time.tzinfo is None or max_time.tzinfo is None:
        raise ValueError("Split timestamps must be timezone-aware")
    if max_time <= min_time:
        raise ValueError("Dataset end must follow dataset start")
    total = max_time - min_time
    minimum_fraction = float(settings["project"].get("minimum_untouched_fraction", 0.20))
    preferred_days = int(settings["project"].get("untouched_test_days", 30))
    if explicit_untouched_start:
        untouched_start = explicit_untouched_start
    else:
        by_days = max_time - timedelta(days=preferred_days)
        by_fraction = min_time + total * (1.0 - minimum_fraction)
        # Use the larger test period: earliest permissible test start.
        untouched_start = min(by_days, by_fraction)
    untouched_start = untouched_start.replace(second=0, microsecond=0)
    untouched_start -= timedelta(minutes=untouched_start.minute % 5)
    if untouched_start <= min_time or untouched_start >= max_time:
        raise ValueError("Untouched split leaves an empty discovery or test period")
    return SplitDefinition(
        dataset_start=min_time,
        discovery_start=min_time,
        discovery_end=untouched_start,
        untouched_start=untouched_start,
        untouched_end=max_time,
    )


def align_bars_without_fill(bars: pd.DataFrame, frequency: str = "5min") -> pd.DataFrame:
    """Create a compact timestamp/instrument alignment grid with nulls; never forward-fill.

    The raw export already retains every source and catalogue field. Repeating those wide
    string columns across every missing alignment slot is both redundant and memory-heavy.
    The aligned file therefore contains only the fields needed for time-series research.
    """
    if bars.empty:
        return bars.copy()
    required = {"canonical_symbol", "bar_open_timestamp_utc", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Cannot align bars; missing columns: {sorted(missing)}")

    research_columns = [
        "bar_open_timestamp_utc", "canonical_symbol", "close", "volume", "vwap",
        "trade_count", "session_type", "exchange_trading_date",
        "is_regular_session", "is_extended_session",
        "minutes_since_session_open", "minutes_until_session_close",
        "day_of_week", "is_holiday", "is_shortened_session",
    ]
    selected = [column for column in research_columns if column in bars.columns]
    raw = bars[selected].copy()
    raw["bar_open_timestamp_utc"] = pd.to_datetime(raw["bar_open_timestamp_utc"], utc=True)

    # PostgreSQL NUMERIC columns are returned by psycopg as decimal.Decimal objects.
    # Pandas preserves those as object dtype, which cannot safely be mixed with Python
    # float literals during return calculations. Convert only the compact research
    # dataset's numeric fields to float64; the raw export remains source-faithful.
    numeric_columns = [
        "close", "volume", "vwap", "trade_count",
        "minutes_since_session_open", "minutes_until_session_close",
    ]
    for column in numeric_columns:
        if column in raw.columns:
            raw[column] = pd.to_numeric(raw[column], errors="coerce").astype("float64")

    symbols = sorted(raw["canonical_symbol"].dropna().astype(str).unique())
    grid_start = raw["bar_open_timestamp_utc"].min().floor(frequency)
    grid_end = raw["bar_open_timestamp_utc"].max().floor(frequency) + pd.Timedelta(frequency)
    grid = pd.date_range(grid_start, grid_end, freq=frequency, tz="UTC", inclusive="left")
    index = pd.MultiIndex.from_product([grid, symbols], names=["bar_open_timestamp_utc", "canonical_symbol"])
    deduped = raw.sort_values("bar_open_timestamp_utc").drop_duplicates(
        ["bar_open_timestamp_utc", "canonical_symbol"], keep="last"
    )
    aligned = deduped.set_index(["bar_open_timestamp_utc", "canonical_symbol"]).reindex(index).reset_index()
    aligned["canonical_symbol"] = aligned["canonical_symbol"].astype("category")
    if "session_type" in aligned.columns:
        aligned["session_type"] = aligned["session_type"].astype("category")
    aligned["is_observed"] = aligned["close"].notna()
    aligned["is_missing_slot"] = ~aligned["is_observed"]
    prior_timestamp = aligned.groupby("canonical_symbol", observed=True)["bar_open_timestamp_utc"].shift()
    prior_close = aligned.groupby("canonical_symbol", observed=True)["close"].shift()
    exactly_adjacent = (aligned["bar_open_timestamp_utc"] - prior_timestamp) == pd.Timedelta(frequency)
    both_observed = aligned["close"].notna() & prior_close.notna()
    valid_return = exactly_adjacent & both_observed & prior_close.ne(0)
    aligned["return_5m"] = np.nan
    aligned.loc[valid_return, "return_5m"] = (
        aligned.loc[valid_return, "close"] / prior_close.loc[valid_return] - 1.0
    )
    return aligned


def build_curve_features(yields: pd.DataFrame) -> pd.DataFrame:
    if yields.empty:
        return pd.DataFrame(columns=["availability_timestamp_utc"])
    df = yields.copy()
    df["observation_timestamp_utc"] = pd.to_datetime(df["observation_timestamp_utc"], utc=True)
    # Yield values are also stored as PostgreSQL NUMERIC and may arrive as Decimal.
    # Normalise them before curve arithmetic while preserving the raw yields export.
    df["yield_value"] = pd.to_numeric(df["yield_value"], errors="coerce").astype("float64")
    wide = df.pivot_table(index="observation_timestamp_utc", columns="canonical_symbol", values="yield_value", aggfunc="last").sort_index()
    # Carry official observations forward only after their conservative availability timestamp.
    wide = wide.ffill()
    pairs = {
        "US_2S10S_BP": ("US10Y_YIELD", "US2Y_YIELD"),
        "US_5S30S_BP": ("US30Y_YIELD", "US5Y_YIELD"),
        "US_2S5S_BP": ("US5Y_YIELD", "US2Y_YIELD"),
        "US_5S10S_BP": ("US10Y_YIELD", "US5Y_YIELD"),
        "UK_2S10S_BP": ("UK10Y_YIELD", "UK2Y_YIELD"),
        "DE_2S10S_BP": ("DE10Y_YIELD", "DE2Y_YIELD"),
    }
    out = pd.DataFrame(index=wide.index)
    for name, (long_col, short_col) in pairs.items():
        if long_col in wide and short_col in wide:
            out[name] = (wide[long_col] - wide[short_col]) * 100.0
            out[f"{name}_CHANGE_BP"] = out[name].diff()
            out[f"{name}_DIRECTION"] = np.select(
                [out[f"{name}_CHANGE_BP"] > 0, out[f"{name}_CHANGE_BP"] < 0],
                ["steepening", "flattening"], default="unchanged",
            )
    out.index.name = "availability_timestamp_utc"
    return out.reset_index()


class Exporter:
    def __init__(self, config: ProjectConfig, database: Database):
        self.config = config
        self.db = database
        self.output_root = config.root / config.settings["exports"].get("output_directory", "exports")
        self.output_root.mkdir(parents=True, exist_ok=True)

    def _bounds(self) -> tuple[datetime, datetime]:
        configured_start = os.getenv("RESEARCH_DATASET_START_UTC")
        configured_end = os.getenv("RESEARCH_DATASET_END_UTC")
        if configured_start and configured_end:
            start = pd.Timestamp(configured_start).tz_convert("UTC") if pd.Timestamp(configured_start).tzinfo else pd.Timestamp(configured_start, tz="UTC")
            end = pd.Timestamp(configured_end).tz_convert("UTC") if pd.Timestamp(configured_end).tzinfo else pd.Timestamp(configured_end, tz="UTC")
            frame = self.db.read_dataframe("select min(bar_open_timestamp_utc) as min_ts, max(bar_close_timestamp_utc) as max_ts from market_bars where bar_open_timestamp_utc >= %s and bar_open_timestamp_utc < %s", (start.to_pydatetime(), end.to_pydatetime()))
            if frame.empty or pd.isna(frame.loc[0, "min_ts"]) or pd.isna(frame.loc[0, "max_ts"]):
                raise RuntimeError("No market bars exist inside the configured RESEARCH_DATASET_START_UTC/END_UTC window")
            return start.to_pydatetime(), end.to_pydatetime()
        frame = self.db.read_dataframe("select min(bar_open_timestamp_utc) as min_ts, max(bar_close_timestamp_utc) as max_ts from market_bars")
        if frame.empty or pd.isna(frame.loc[0, "min_ts"]) or pd.isna(frame.loc[0, "max_ts"]):
            raise RuntimeError("No market bars are available to export")
        return pd.Timestamp(frame.loc[0, "min_ts"]).to_pydatetime(), pd.Timestamp(frame.loc[0, "max_ts"]).to_pydatetime()

    def _query_bars(self, start: datetime, end: datetime) -> pd.DataFrame:
        return self.db.read_dataframe("""
            select b.*, i.canonical_symbol, i.canonical_name, i.asset_class, i.subcategory,
                   i.instrument_type, i.economic_exposure, i.exchange, i.exchange_timezone,
                   i.currency, i.volume_type, i.data_frequency, i.normal_session,
                   i.methodological_limitations
            from market_bars b join instruments i using (instrument_id)
            where b.bar_open_timestamp_utc >= %s and b.bar_open_timestamp_utc < %s
            order by b.bar_open_timestamp_utc, i.canonical_symbol
        """, (start, end))

    def _query_yields(self, start: datetime, end: datetime) -> pd.DataFrame:
        return self.db.read_dataframe("""
            select y.*, i.canonical_symbol, i.canonical_name, i.asset_class, i.exchange_timezone,
                   i.currency, i.methodological_limitations
            from yield_observations y join instruments i using (instrument_id)
            where y.observation_timestamp_utc >= %s and y.observation_timestamp_utc < %s
            order by y.observation_timestamp_utc, i.canonical_symbol
        """, (start, end))

    def _query_instruments(self) -> pd.DataFrame:
        return self.db.read_dataframe("select * from instruments order by canonical_symbol")

    def _write_parquet(self, frame: pd.DataFrame, path: Path) -> None:
        try:
            frame.to_parquet(path, index=False, compression=self.config.settings["exports"].get("parquet_compression", "zstd"))
        except ImportError as exc:
            raise RuntimeError("Parquet export requires pyarrow. Install requirements.lock.") from exc

    def _write_csv_chunks(self, frame: pd.DataFrame, directory: Path, stem: str) -> list[Path]:
        chunk = int(self.config.settings["exports"].get("csv_chunk_rows", 250000))
        paths: list[Path] = []
        if frame.empty:
            path = directory / f"{stem}_part_001.csv.gz"
            frame.to_csv(path, index=False, compression="gzip")
            return [path]
        for number, start in enumerate(range(0, len(frame), chunk), start=1):
            path = directory / f"{stem}_part_{number:03d}.csv.gz"
            frame.iloc[start:start+chunk].to_csv(path, index=False, compression="gzip")
            paths.append(path)
        return paths

    def _copy_docs(self, directory: Path) -> None:
        mapping = {
            "docs/data_dictionary.md": "data_dictionary.md",
            "docs/methodology.md": "methodology.md",
            "docs/analysis_prompt.md": "analysis_prompt.md",
            "README.md": "README.md",
        }
        for source, target in mapping.items():
            path = self.config.root / source
            if path.exists():
                shutil.copy2(path, directory / target)

    def _source_manifest(self, instruments: pd.DataFrame) -> dict[str, Any]:
        records = []
        for _, row in instruments.iterrows():
            records.append({
                "canonical_symbol": row.get("canonical_symbol"), "provider": row.get("provider"),
                "provider_symbol": row.get("provider_symbol"), "instrument_type": row.get("instrument_type"),
                "data_frequency": row.get("data_frequency"), "volume_type": row.get("volume_type"),
                "licence": row.get("data_licence"), "redistribution_restrictions": row.get("redistribution_restrictions"),
                "methodological_limitations": row.get("methodological_limitations"),
            })
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "sources": records}

    def _dependency_versions(self) -> dict[str, str]:
        names = ["pandas", "numpy", "pyarrow", "psycopg", "databento", "requests", "PyYAML", "openpyxl", "pandas_market_calendars"]
        result = {}
        for name in names:
            try:
                result[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                result[name] = "not-installed-in-export-runtime"
        return result

    def _file_inventory(self, directory: Path, excluded_names: set[str] | None = None) -> list[dict[str, Any]]:
        excluded = excluded_names or set()
        return [
            {
                "path": path.relative_to(directory).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.name not in excluded
        ]

    def _write_hashes(self, directory: Path) -> None:
        files = sorted(p for p in directory.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")
        lines = [f"{sha256_file(p)}  {p.relative_to(directory).as_posix()}" for p in files]
        (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _zip(self, source_dir: Path, destination: Path, password: str | None = None) -> None:
        if password:
            try:
                import pyzipper
                with pyzipper.AESZipFile(destination, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
                    zf.setpassword(password.encode("utf-8"))
                    for path in sorted(source_dir.rglob("*")):
                        if path.is_file():
                            zf.write(path, path.relative_to(source_dir))
                return
            except ImportError as exc:
                raise RuntimeError("UNTOUCHED_ARCHIVE_PASSWORD is set but pyzipper is not installed") from exc
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(source_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(source_dir))

    def _render_quality_report(self, result, yield_issues: pd.DataFrame) -> str:
        severity = result.issues["severity"].value_counts().to_dict() if not result.issues.empty else {}
        return "\n".join([
            "# Data-quality report — discovery period only",
            "",
            "The quarantined untouched period was not queried by this quality process.",
            "",
            f"- Discovery bar rows checked: {result.checked_rows:,}",
            f"- Bar issues recorded: {len(result.issues):,}",
            f"- Yield issues recorded: {len(yield_issues):,}",
            f"- Severity counts: `{json.dumps(severity, sort_keys=True)}`",
            "",
            "Extreme observations are retained and flagged. They require source and adjacent-bar verification; they are never deleted solely for being extreme.",
            "",
            "Coverage uses the configured, version-locked exchange calendar where available, removes scheduled breaks and reports missing session-edge bars without creating or filling synthetic prices. Instruments lacking a verified calendar are explicitly limited to an observed-session envelope.",
        ])

    def create(self, explicit_untouched_start: datetime | None = None, include_full_archive: bool | None = None) -> ExportResult:
        min_time, max_time = self._bounds()
        configured_untouched = os.getenv("UNTOUCHED_START_UTC")
        if explicit_untouched_start is None and configured_untouched:
            explicit_untouched_start = pd.Timestamp(configured_untouched).to_pydatetime()
            if explicit_untouched_start.tzinfo is None:
                explicit_untouched_start = explicit_untouched_start.replace(tzinfo=timezone.utc)
        split = determine_split(min_time, max_time, self.config.settings, explicit_untouched_start)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
        discovery_zip = self.output_root / f"cross_asset_discovery_export_{stamp}.zip"
        untouched_zip = self.output_root / f"cross_asset_UNTOUCHED_TEST_{stamp}.zip"
        full_zip = self.output_root / f"cross_asset_FULL_RESTRICTED_ARCHIVE_{stamp}.zip"
        include_full = self.config.settings["exports"].get("create_full_archive", True) if include_full_archive is None else include_full_archive

        print("EXPORT: preparing temporary workspace", flush=True)
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            discovery_dir = temp / "discovery"
            untouched_dir = temp / "untouched"
            full_dir = temp / "full"
            discovery_dir.mkdir(); untouched_dir.mkdir();
            if include_full: full_dir.mkdir()

            print("EXPORT: loading discovery-period data", flush=True)
            instruments = self._query_instruments()
            discovery_bars = self._query_bars(split.discovery_start, split.discovery_end)
            discovery_yields = self._query_yields(split.discovery_start, split.discovery_end)
            rolls = self.db.read_dataframe("""select r.*, i.canonical_symbol from futures_rolls r
                join instruments i on i.instrument_id=r.continuous_instrument_id
                where r.roll_timestamp >= %s and r.roll_timestamp < %s order by r.roll_timestamp""",
                (split.discovery_start, split.discovery_end))
            sessions = self.db.read_dataframe("""select * from market_sessions
                where regular_open_utc >= %s and regular_open_utc < %s order by regular_open_utc""",
                (split.discovery_start, split.discovery_end))
            if sessions.empty and not discovery_bars.empty:
                sessions = discovery_bars.groupby(["canonical_symbol", "exchange_trading_date"], dropna=False).agg(
                    regular_open_utc=("bar_open_timestamp_utc", "min"),
                    regular_close_utc=("bar_close_timestamp_utc", "max"),
                    observed_bars=("bar_open_timestamp_utc", "count"),
                ).reset_index()
                sessions["source"] = "observed_session_envelope_not_official_calendar"

            print(f"EXPORT: discovery bars loaded ({len(discovery_bars):,} rows); running discovery-only checks", flush=True)
            bar_quality = evaluate_bars(discovery_bars, instruments, self.config.settings, coverage_start=split.discovery_start, coverage_end_exclusive=split.discovery_end)
            yield_issues = evaluate_yields(discovery_yields)
            quality_results = pd.concat([bar_quality.issues, yield_issues], ignore_index=True, sort=False)
            print("EXPORT: building compact aligned research dataset", flush=True)
            aligned = align_bars_without_fill(discovery_bars, self.config.settings["alignment"].get("grid_frequency", "5min"))
            print(f"EXPORT: aligned dataset built ({len(aligned):,} rows)", flush=True)
            curve_features = build_curve_features(discovery_yields)

            print("EXPORT: writing discovery files", flush=True)
            self._write_parquet(discovery_bars, discovery_dir / "bars_raw.parquet")
            self._write_parquet(aligned, discovery_dir / "bars_research_aligned.parquet")
            self._write_parquet(discovery_yields, discovery_dir / "yields.parquet")
            self._write_parquet(curve_features, discovery_dir / "yield_curve_features.parquet")
            instruments.to_csv(discovery_dir / "instruments.csv", index=False)
            rolls.to_csv(discovery_dir / "futures_rolls.csv", index=False)
            sessions.to_csv(discovery_dir / "market_sessions.csv", index=False)
            quality_results.to_csv(discovery_dir / "data_quality_results.csv", index=False)
            bar_quality.coverage.to_csv(discovery_dir / "coverage_summary.csv", index=False)
            heatmap = discovery_bars.pivot_table(index="exchange_trading_date", columns="canonical_symbol", values="bar_id", aggfunc="count", fill_value=0)
            heatmap.to_csv(discovery_dir / "coverage_heatmap.csv")
            self._write_csv_chunks(discovery_bars, discovery_dir, "bars_raw")
            self._write_csv_chunks(aligned, discovery_dir, "bars_research_aligned")
            self._write_csv_chunks(discovery_yields, discovery_dir, "yields")
            (discovery_dir / "data_quality_report.md").write_text(self._render_quality_report(bar_quality, yield_issues), encoding="utf-8")
            (discovery_dir / "source_manifest.json").write_text(json.dumps(self._source_manifest(instruments), indent=2, default=str), encoding="utf-8")
            self._copy_docs(discovery_dir)

            discovery_counts = discovery_bars.groupby("canonical_symbol").size().astype(int).to_dict()
            manifest = {
                "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
                "dataset_start": split.dataset_start.isoformat(), "dataset_end": split.untouched_end.isoformat(),
                "discovery_start": split.discovery_start.isoformat(), "discovery_end_exclusive": split.discovery_end.isoformat(),
                "untouched_start": split.untouched_start.isoformat(), "untouched_end_exclusive": split.untouched_end.isoformat(),
                "untouched_test_policy": "Not queried for quality, coverage, returns, charts, descriptive statistics or instrument counts during discovery export generation.",
                "discovery_row_counts_by_instrument": discovery_counts,
                "source_api_versions": {
                    "alpaca": "v2 historical stock bars; timeframe=5Min; feed=iex; adjustment=raw",
                    "coinbase_exchange": "public product candles; granularity_seconds=300",
                    "fred": "series observations with real-time vintages",
                },
                "collector_git_commit": os.getenv("RENDER_GIT_COMMIT"),
                "python_version": sys.version, "platform": platform.platform(),
                "dependency_versions": self._dependency_versions(),
                "configuration_sha256": self.config.hash(),
                "roll_methodology": "Not applicable to the active free-data catalogue. No futures contracts or continuous futures are collected; futures_rolls should be empty.",
                "timestamp_convention": "UTC bar-open timestamps; close timestamp is open + 5 minutes.",
                "known_limitations": sorted(set(str(x) for x in instruments["methodological_limitations"].dropna().tolist())),
                "exclusions": {
                    "structurally_rejected_discovery_rows": int((quality_results.get("disposition", pd.Series(dtype=str)) == "excluded").sum()),
                    "automatic_outlier_deletions": 0,
                    "untouched_test_rows": "excluded from this package by timestamp boundary",
                },
                "corrections": [
                    "No source price was silently overwritten.",
                    "Alpaca bars are native five-minute raw IEX observations; missing intervals are not filled.",
                    "Alpaca volume and trade count are single-venue IEX measures, not consolidated US-market totals.",
                    "ETF proxies remain explicitly distinct from their requested native exposures.",
                    "Any future manually corrected value must preserve the original and be recorded in data_quality_issues.",
                ],
                "file_inventory_excluding_self_and_SHA256SUMS": self._file_inventory(discovery_dir, {"export_manifest.json", "SHA256SUMS.txt"}),
                "hash_note": "The manifest inventories every pre-existing payload file. SHA256SUMS.txt additionally hashes export_manifest.json. A file cannot contain its own final SHA-256 without self-reference.",
            }
            (discovery_dir / "export_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
            self._write_hashes(discovery_dir)
            print("EXPORT: compressing discovery archive", flush=True)
            self._zip(discovery_dir, discovery_zip)
            print(f"EXPORT: discovery archive ready ({discovery_zip.stat().st_size:,} bytes)", flush=True)

            del discovery_bars, discovery_yields, rolls, sessions, bar_quality, yield_issues
            del quality_results, aligned, curve_features, heatmap, discovery_counts
            gc.collect()

            # The untouched query is isolated and receives structural serialization only.
            print("EXPORT: serialising untouched period without profiling", flush=True)
            untouched_bars = self._query_bars(split.untouched_start, split.untouched_end)
            untouched_yields = self._query_yields(split.untouched_start, split.untouched_end)
            self._write_parquet(untouched_bars, untouched_dir / "bars_raw_UNTOUCHED.parquet")
            self._write_parquet(untouched_yields, untouched_dir / "yields_UNTOUCHED.parquet")
            instruments.to_csv(untouched_dir / "instruments_schema_reference.csv", index=False)
            untouched_readme = f"""# {self.config.settings['exports'].get('untouched_label')}

Do not open this archive during discovery. It contains only timestamps from
`{split.untouched_start.isoformat()}` (inclusive) to `{split.untouched_end.isoformat()}` (exclusive).

The exporter performed only schema-preserving serialization. It did not run quality checks,
coverage calculations, return calculations, charts, descriptive statistics, comparisons,
or per-instrument counts on these observations.
"""
            (untouched_dir / "README_DO_NOT_OPEN.md").write_text(untouched_readme, encoding="utf-8")
            untouched_manifest = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "classification": self.config.settings["exports"].get("untouched_label"),
                "period_start_inclusive": split.untouched_start.isoformat(),
                "period_end_exclusive": split.untouched_end.isoformat(),
                "permitted_checks_performed": ["file serialization", "schema-preserving write", "file-size capture", "SHA-256 hashing"],
                "prohibited_checks_not_performed": ["row counts", "coverage", "descriptive statistics", "returns", "charts", "cross-instrument comparison", "data-quality profiling"],
                "file_inventory_excluding_self_and_SHA256SUMS": self._file_inventory(untouched_dir, {"structural_manifest.json", "SHA256SUMS.txt"}),
                "hash_note": "SHA256SUMS.txt also hashes structural_manifest.json; self-hashing is intentionally excluded.",
            }
            (untouched_dir / "structural_manifest.json").write_text(json.dumps(untouched_manifest, indent=2), encoding="utf-8")
            self._write_hashes(untouched_dir)
            password = os.getenv(self.config.settings["exports"].get("untouched_password_env", "UNTOUCHED_ARCHIVE_PASSWORD"))
            self._zip(untouched_dir, untouched_zip, password=password)
            print(f"EXPORT: untouched archive ready ({untouched_zip.stat().st_size:,} bytes)", flush=True)
            del untouched_bars, untouched_yields
            gc.collect()

            if include_full:
                print("EXPORT: creating optional full restricted archive", flush=True)
                full_bars = self._query_bars(split.dataset_start, split.untouched_end)
                full_yields = self._query_yields(split.dataset_start, split.untouched_end)
                full_rolls = self.db.read_dataframe("""select r.*, i.canonical_symbol from futures_rolls r
                    join instruments i on i.instrument_id=r.continuous_instrument_id
                    where r.roll_timestamp >= %s and r.roll_timestamp < %s order by r.roll_timestamp""",
                    (split.dataset_start, split.untouched_end))
                full_sessions = self.db.read_dataframe("""select * from market_sessions
                    where regular_open_utc < %s and regular_close_utc > %s order by regular_open_utc""",
                    (split.untouched_end, split.dataset_start))
                self._write_parquet(full_bars, full_dir / "bars_raw_FULL_RESTRICTED.parquet")
                self._write_parquet(full_yields, full_dir / "yields_FULL_RESTRICTED.parquet")
                instruments.to_csv(full_dir / "instruments.csv", index=False)
                full_rolls.to_csv(full_dir / "futures_rolls.csv", index=False)
                full_sessions.to_csv(full_dir / "market_sessions.csv", index=False)
                (full_dir / "README_RESTRICTED.md").write_text("Restricted backup only. Do not use during discovery because it contains the untouched period.\n", encoding="utf-8")
                self._write_hashes(full_dir)
                self._zip(full_dir, full_zip, password=password)
                print(f"EXPORT: full restricted archive ready ({full_zip.stat().st_size:,} bytes)", flush=True)
                del full_bars, full_yields, full_rolls, full_sessions
                gc.collect()

        return ExportResult(discovery_zip, untouched_zip, full_zip if include_full else None, split)
