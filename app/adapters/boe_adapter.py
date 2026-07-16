from __future__ import annotations

import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .base import FetchBatch, SourceAdapter
from ..http_utils import request


class BoEYieldCurveAdapter(SourceAdapter):
    provider = "boe_yield_curve"
    latest_zip = "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip"
    archive_zip = "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/glcnominalddata.zip"

    def __init__(self, settings: dict, dry_run: bool = False):
        super().__init__(settings, dry_run)
        self.session = requests.Session()
        self._cache: pd.DataFrame | None = None

    @staticmethod
    def _maturity_from_column(column: object) -> float | None:
        text = str(column).strip().lower().replace("years", "").replace("year", "").replace("yrs", "")
        text = re.sub(r"[^0-9.]", "", text)
        try:
            return float(text)
        except ValueError:
            return None

    def _read_archive(self) -> pd.DataFrame:
        if self._cache is not None:
            return self._cache
        response = request(
            "GET", self.archive_zip, session=self.session,
            timeout=int(self.settings["collection"].get("request_timeout_seconds", 45)),
            max_retries=int(self.settings["collection"].get("max_retries", 5)),
        )
        self.api_calls += 1
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "boe.zip"
            archive.write_bytes(response.content)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmp)
            candidates = [p for p in Path(tmp).rglob("*") if p.suffix.lower() in {".xls", ".xlsx", ".csv"}]
            if not candidates:
                raise RuntimeError("BoE archive contained no readable spreadsheet or CSV")
            # Prefer files whose names indicate nominal daily spot curves.
            candidates.sort(key=lambda p: ("nominal" not in p.name.lower(), "spot" not in p.name.lower(), len(p.name)))
            parsed: list[pd.DataFrame] = []
            for path in candidates:
                try:
                    if path.suffix.lower() == ".csv":
                        frames = {path.name: pd.read_csv(path)}
                    else:
                        book = pd.ExcelFile(path)
                        sheets = [s for s in book.sheet_names if "spot" in s.lower() or "nominal" in s.lower()] or book.sheet_names
                        frames = {s: pd.read_excel(path, sheet_name=s) for s in sheets}
                    for _, df in frames.items():
                        if len(df.columns) >= 3:
                            parsed.append(df)
                except Exception:
                    continue
            if not parsed:
                raise RuntimeError("Could not parse the BoE nominal yield-curve archive")
            # Pick the frame with the greatest number of numeric maturity-looking columns.
            parsed.sort(key=lambda d: sum(self._maturity_from_column(c) is not None for c in d.columns), reverse=True)
            self._cache = parsed[0]
            return self._cache

    def preflight(self, instrument: dict, start: datetime, end: datetime) -> dict:
        if self.dry_run:
            return {"ok": True, "dry_run": True, "provider": self.provider, "symbol": instrument["provider_symbol"]}
        batch = self.fetch(instrument, start, end)
        frame = batch.yields
        valid = not frame.empty and frame["yield_value"].notna().all()
        return {
            "ok": bool(valid),
            "provider": self.provider,
            "symbol": instrument["provider_symbol"],
            "real_rows_received": int(len(frame)),
            "frequency": "daily",
            "point_in_time_history_available": False,
            "error": None if valid else "No matching real maturity observations were parsed from the official archive",
        }

    def fetch(self, instrument: dict, start: datetime, end: datetime) -> FetchBatch:
        if self.dry_run:
            return FetchBatch(instrument["canonical_symbol"], self.provider, metadata={"dry_run": True})
        frame = self._read_archive().copy()
        date_col = next((c for c in frame.columns if "date" in str(c).lower()), frame.columns[0])
        frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
        target = float(str(instrument.get("maturity", "10Y")).upper().replace("Y", ""))
        maturity_columns = {c: self._maturity_from_column(c) for c in frame.columns if c != date_col}
        valid = {c: v for c, v in maturity_columns.items() if v is not None}
        if not valid:
            raise RuntimeError(f"No maturity columns found in BoE data: {list(frame.columns)}")
        value_col = min(valid, key=lambda c: abs(valid[c] - target))
        if abs(valid[value_col] - target) > 0.26:
            raise RuntimeError(f"BoE data did not contain a close match for {target}Y; nearest was {valid[value_col]}Y")
        rows = []
        retrieved_at = datetime.now(timezone.utc)
        retrieval_vintage = retrieved_at.date()
        for _, item in frame.iterrows():
            if pd.isna(item[date_col]) or pd.isna(item[value_col]):
                continue
            observation_date = item[date_col].date()
            if not (start.date() - timedelta(days=14) <= observation_date <= end.date()):
                continue
            # This bulk archive exposes the latest historical curve, not a reliable sequence of
            # point-in-time vintages. The value therefore enters the information set only when this
            # collector actually retrieved it. It must never be backdated to the observation date.
            rows.append({
                "observation_timestamp_utc": retrieved_at, "observation_date": observation_date,
                "maturity": instrument.get("maturity", f"{target:g}Y"), "yield_value": float(item[value_col]),
                "yield_type": instrument.get("yield_type", "fitted_nominal_zero_coupon"),
                "source": self.provider, "source_series": instrument["provider_symbol"],
                "published_at": retrieved_at, "vintage_date": retrieval_vintage,
                "is_revised": retrieval_vintage > observation_date, "original_value": float(item[value_col]),
            })
        return FetchBatch(
            instrument["canonical_symbol"], self.provider, yields=pd.DataFrame(rows),
            metadata={
                "api_calls": self.api_calls,
                "source_column": str(value_col),
                "point_in_time_history_available": False,
                "availability_policy": "retrieval timestamp; historical values are not backdated",
            },
        )
