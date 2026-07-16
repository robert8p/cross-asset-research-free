from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from .base import FetchBatch, SourceAdapter
from ..http_utils import request_json


class FredAdapter(SourceAdapter):
    provider = "fred"
    endpoint = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, settings: dict, dry_run: bool = False):
        super().__init__(settings, dry_run)
        self.api_key = os.getenv("FRED_API_KEY")
        if not self.api_key and not dry_run:
            raise RuntimeError("FRED_API_KEY is required")
        self.session = requests.Session()

    def _request(self, instrument: dict, start: datetime, end: datetime, output_type: int) -> dict:
        payload = request_json(
            "GET", self.endpoint,
            params={
                "series_id": instrument["provider_symbol"],
                "api_key": self.api_key,
                "file_type": "json",
                "observation_start": (start.date() - timedelta(days=14)).isoformat(),
                "observation_end": end.date().isoformat(),
                "realtime_start": (start.date() - timedelta(days=14)).isoformat(),
                "realtime_end": end.date().isoformat(),
                "output_type": output_type,
            },
            session=self.session,
            timeout=int(self.settings["collection"].get("request_timeout_seconds", 45)),
            max_retries=int(self.settings["collection"].get("max_retries", 5)),
        )
        self.api_calls += 1
        return payload

    @staticmethod
    def _normalise_items(payload: dict, instrument: dict, maturity: str, revision_kind: str) -> list[dict]:
        rows: list[dict] = []
        for item in payload.get("observations", []):
            if item.get("value") in (None, "."):
                continue
            observation_date = pd.Timestamp(item["date"]).date()
            vintage_date = pd.Timestamp(item.get("realtime_start", item["date"])).date()
            # The endpoint does not supply a precise intraday publication time for these daily series.
            # Next-day 00:00 UTC is deliberately conservative: a value cannot enter the research
            # information set before its stated vintage day has completed.
            available = datetime.combine(vintage_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            value = float(item["value"])
            rows.append({
                "observation_timestamp_utc": available,
                "observation_date": observation_date,
                "maturity": maturity,
                "yield_value": value,
                "yield_type": instrument.get("yield_type", "constant_maturity"),
                "source": "fred",
                "source_series": instrument["provider_symbol"],
                "published_at": available,
                "vintage_date": vintage_date,
                "is_revised": revision_kind == "revision",
                "original_value": value,
                "_revision_kind": revision_kind,
            })
        return rows

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
            "point_in_time_vintages_requested": True,
            "error": None if valid else "No usable point-in-time observations returned in the probe window",
        }

    def fetch(self, instrument: dict, start: datetime, end: datetime) -> FetchBatch:
        if self.dry_run:
            return FetchBatch(instrument["canonical_symbol"], self.provider, metadata={"dry_run": True})

        # Output type 4 supplies initial releases. Output type 3 supplies observations that were
        # subsequently new or revised during the requested real-time window. Retaining both makes
        # point-in-time reconstruction possible instead of silently replacing history with latest values.
        initial_payload = self._request(instrument, start, end, output_type=4)
        revision_payload = self._request(instrument, start, end, output_type=3)
        maturity = str(instrument.get("maturity", instrument["canonical_symbol"].split("_")[-1]))
        rows = self._normalise_items(initial_payload, instrument, maturity, "initial")
        rows.extend(self._normalise_items(revision_payload, instrument, maturity, "revision"))

        yields = pd.DataFrame(rows)
        if not yields.empty:
            yields = yields.sort_values(["observation_date", "vintage_date", "_revision_kind"])
            yields = yields.drop_duplicates(["observation_date", "maturity", "vintage_date"], keep="last")
            first_vintage = yields.groupby(["observation_date", "maturity"])["vintage_date"].transform("min")
            yields["is_revised"] = yields["vintage_date"] > first_vintage
            yields = yields.drop(columns=["_revision_kind"]).reset_index(drop=True)
            available = pd.to_datetime(yields["observation_timestamp_utc"], utc=True)
            start_ts = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
            end_ts = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
            yields = yields[(available >= start_ts) & (available < end_ts)].reset_index(drop=True)

        return FetchBatch(
            instrument["canonical_symbol"],
            self.provider,
            yields=yields,
            metadata={
                "api_calls": self.api_calls,
                "fred_output_types": [4, 3],
                "vintage_policy": "initial releases plus new/revised observations; conservative next-day UTC availability",
            },
        )
