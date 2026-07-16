from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
import requests

from .base import FetchBatch, SourceAdapter
from ..http_utils import request


class BundesbankAdapter(SourceAdapter):
    provider = "bundesbank"
    base_url = "https://api.statistiken.bundesbank.de/rest/data"

    def __init__(self, settings: dict, dry_run: bool = False):
        super().__init__(settings, dry_run)
        self.session = requests.Session()

    def preflight(self, instrument: dict, start: datetime, end: datetime) -> dict:
        if self.dry_run:
            return {"ok": True, "dry_run": True, "provider": self.provider, "symbol": instrument["provider_symbol"]}
        probe_start = max(start, end - timedelta(days=45))
        batch = self.fetch(instrument, probe_start, end)
        frame = batch.yields
        valid = not frame.empty and frame["yield_value"].notna().all()
        return {
            "ok": bool(valid),
            "provider": self.provider,
            "symbol": instrument["provider_symbol"],
            "real_rows_received": int(len(frame)),
            "frequency": "daily",
            "point_in_time_history_available": False,
            "error": None if valid else "No usable official observations returned for the series code",
        }

    def fetch(self, instrument: dict, start: datetime, end: datetime) -> FetchBatch:
        if self.dry_run:
            return FetchBatch(instrument["canonical_symbol"], self.provider, metadata={"dry_run": True})
        full_code = instrument["provider_symbol"]
        flow, key = full_code.split(".", 1)
        response = request(
            "GET", f"{self.base_url}/{flow}/{key}",
            params={"startPeriod": (start.date() - timedelta(days=14)).isoformat(), "endPeriod": end.date().isoformat()},
            headers={"Accept": "text/csv"}, session=self.session,
            timeout=int(self.settings["collection"].get("request_timeout_seconds", 45)),
            max_retries=int(self.settings["collection"].get("max_retries", 5)),
        )
        self.api_calls += 1
        df = pd.read_csv(StringIO(response.text), sep=None, engine="python")
        date_col = next((c for c in df.columns if c.upper() in {"TIME_PERIOD", "DATE", "TIME"}), None)
        value_col = next((c for c in df.columns if c.upper() in {"OBS_VALUE", "VALUE"}), None)
        if not date_col or not value_col:
            raise RuntimeError(f"Unexpected Bundesbank CSV columns: {list(df.columns)}")
        rows = []
        maturity = instrument.get("maturity", instrument["canonical_symbol"].split("_")[-1])
        retrieved_at = datetime.now(timezone.utc)
        retrieval_vintage = retrieved_at.date()
        for _, item in df.iterrows():
            if pd.isna(item[value_col]):
                continue
            observation_date = pd.Timestamp(item[date_col]).date()
            # The public series response is a current historical snapshot and does not provide a
            # complete vintage history. Assign retrieval-time availability to prevent revised values
            # from leaking backwards into a historical research period.
            rows.append({
                "observation_timestamp_utc": retrieved_at, "observation_date": observation_date,
                "maturity": str(maturity), "yield_value": float(item[value_col]),
                "yield_type": instrument.get("yield_type", "estimated_zero_coupon"),
                "source": self.provider, "source_series": full_code,
                "published_at": retrieved_at, "vintage_date": retrieval_vintage,
                "is_revised": retrieval_vintage > observation_date, "original_value": float(item[value_col]),
            })
        return FetchBatch(
            instrument["canonical_symbol"], self.provider, yields=pd.DataFrame(rows),
            metadata={
                "api_calls": self.api_calls,
                "point_in_time_history_available": False,
                "availability_policy": "retrieval timestamp; historical values are not backdated",
            },
        )
