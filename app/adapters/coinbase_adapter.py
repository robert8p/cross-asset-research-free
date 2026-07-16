from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import requests

from .base import FetchBatch, SourceAdapter, chunk_time_range, normalize_bar_frame
from ..http_utils import request_json


class CoinbaseAdapter(SourceAdapter):
    provider = "coinbase"
    base_url = "https://api.exchange.coinbase.com"

    def __init__(self, settings: dict, dry_run: bool = False):
        super().__init__(settings, dry_run)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "cross-asset-research-collector/1.0"})

    def preflight(self, instrument: dict, start: datetime, end: datetime) -> dict:
        if self.dry_run:
            return {"ok": True, "dry_run": True, "provider": self.provider, "symbol": instrument["provider_symbol"]}
        probe_start = max(start, end - timedelta(hours=24))
        batch = self.fetch(instrument, probe_start, end)
        bars = batch.bars
        valid = not bars.empty and bars["bar_open_timestamp_utc"].notna().all()
        interval_ok = valid and ((bars["bar_close_timestamp_utc"] - bars["bar_open_timestamp_utc"]) == pd.Timedelta(minutes=5)).all()
        return {
            "ok": bool(valid and interval_ok),
            "provider": self.provider,
            "symbol": instrument["provider_symbol"],
            "real_rows_received": int(len(bars)),
            "timestamp_convention": "bar_open_utc",
            "native_interval_seconds": 300,
            "error": None if valid and interval_ok else "No valid real five-minute bars returned in the probe window",
        }

    def fetch(self, instrument: dict, start: datetime, end: datetime) -> FetchBatch:
        rows: list[list[float]] = []
        # 300 five-minute candles = 25 hours. Use 24 hours to avoid boundary surprises.
        for chunk_start, chunk_end in chunk_time_range(start, end, timedelta(hours=24)):
            if self.dry_run:
                continue
            payload = request_json(
                "GET",
                f"{self.base_url}/products/{instrument['provider_symbol']}/candles",
                params={
                    "granularity": 300,
                    "start": chunk_start.isoformat(),
                    "end": chunk_end.isoformat(),
                },
                session=self.session,
                timeout=int(self.settings["collection"].get("request_timeout_seconds", 45)),
                max_retries=int(self.settings["collection"].get("max_retries", 5)),
            )
            self.api_calls += 1
            if isinstance(payload, dict) and payload.get("message"):
                raise RuntimeError(f"Coinbase error: {payload['message']}")
            rows.extend(payload)
        if not rows:
            return FetchBatch(instrument["canonical_symbol"], self.provider, metadata={"api_calls": self.api_calls})
        df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
        df["bar_open_timestamp_utc"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df[(df["bar_open_timestamp_utc"] >= start) & (df["bar_open_timestamp_utc"] < end)]
        df = df.drop_duplicates("bar_open_timestamp_utc", keep="last")
        df["contract_code"] = None
        bars = normalize_bar_frame(df, instrument, source=self.provider)
        return FetchBatch(instrument["canonical_symbol"], self.provider, bars=bars, metadata={"api_calls": self.api_calls})
