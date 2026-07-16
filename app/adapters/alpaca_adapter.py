from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from .base import FetchBatch, SourceAdapter, normalize_bar_frame
from ..http_utils import request_json


class AlpacaAdapter(SourceAdapter):
    """Collect unadjusted five-minute US equity/ETF bars from Alpaca.

    The free-data configuration deliberately requests the IEX feed. IEX is a real
    exchange, but it is only one venue, so its volume and trade counts must never be
    interpreted as consolidated US-market activity.
    """

    provider = "alpaca"
    base_url = "https://data.alpaca.markets/v2/stocks"

    def __init__(self, settings: dict[str, Any], dry_run: bool = False):
        super().__init__(settings, dry_run)
        self.api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        if (not self.api_key or not self.secret_key) and not dry_run:
            raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
        self.feed = os.getenv("ALPACA_FEED", "iex").strip().lower()
        if self.feed != "iex" and os.getenv("ALLOW_NONFREE_ALPACA_FEED", "false").lower() not in {"1", "true", "yes"}:
            raise RuntimeError(
                "The free-data edition requires ALPACA_FEED=iex. Set "
                "ALLOW_NONFREE_ALPACA_FEED=true only after knowingly obtaining another entitlement."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.secret_key or "",
            "User-Agent": "cross-asset-research-collector-free/1.1",
        })

    def preflight(self, instrument: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
        if self.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "provider": self.provider,
                "symbol": instrument["provider_symbol"],
                "feed": self.feed,
            }
        probe_start = max(start, end - timedelta(days=35))
        batch = self.fetch(instrument, probe_start, end)
        bars = batch.bars
        valid = (
            not bars.empty
            and bars["bar_open_timestamp_utc"].notna().all()
            and bars[["open", "high", "low", "close"]].notna().all().all()
        )
        interval_ok = valid and (
            (bars["bar_close_timestamp_utc"] - bars["bar_open_timestamp_utc"])
            == pd.Timedelta(minutes=5)
        ).all()
        return {
            "ok": bool(valid and interval_ok),
            "provider": self.provider,
            "symbol": instrument["provider_symbol"],
            "real_rows_received": int(len(bars)),
            "feed": self.feed,
            "adjustment": "raw",
            "timestamp_convention": "bar_open_utc",
            "native_interval": "5Min",
            "volume_scope": "IEX venue only; not consolidated US market volume" if self.feed == "iex" else self.feed,
            "error": None if valid and interval_ok else "No valid real five-minute bars returned in the probe window",
        }

    def fetch(self, instrument: dict[str, Any], start: datetime, end: datetime) -> FetchBatch:
        if self.dry_run:
            return FetchBatch(
                instrument["canonical_symbol"], self.provider,
                metadata={"dry_run": True, "feed": self.feed, "adjustment": "raw"},
            )

        symbol = instrument["provider_symbol"]
        page_token: str | None = None
        rows: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()

        while True:
            params: dict[str, Any] = {
                "timeframe": "5Min",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 10000,
                "adjustment": "raw",
                "feed": self.feed,
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            payload = request_json(
                "GET",
                f"{self.base_url}/{symbol}/bars",
                params=params,
                session=self.session,
                timeout=int(self.settings["collection"].get("request_timeout_seconds", 45)),
                max_retries=int(self.settings["collection"].get("max_retries", 5)),
            )
            self.api_calls += 1
            if not isinstance(payload, dict):
                raise RuntimeError(f"Unexpected Alpaca response type for {symbol}: {type(payload).__name__}")
            page_rows = payload.get("bars") or []
            if isinstance(page_rows, dict):
                page_rows = page_rows.get(symbol, [])
            if not isinstance(page_rows, list):
                raise RuntimeError(f"Unexpected Alpaca bars payload for {symbol}")
            rows.extend(page_rows)

            next_token = payload.get("next_page_token")
            if not next_token:
                break
            if next_token in seen_tokens:
                raise RuntimeError(f"Alpaca repeated a pagination token for {symbol}")
            seen_tokens.add(next_token)
            page_token = next_token

        if not rows:
            return FetchBatch(
                instrument["canonical_symbol"], self.provider,
                metadata={
                    "api_calls": self.api_calls,
                    "feed": self.feed,
                    "adjustment": "raw",
                    "volume_scope": "IEX venue only" if self.feed == "iex" else self.feed,
                },
            )

        df = pd.DataFrame(rows)
        required = {"t", "o", "h", "l", "c"}
        missing = required - set(df.columns)
        if missing:
            raise RuntimeError(f"Alpaca response for {symbol} omitted fields: {sorted(missing)}")
        df = df.rename(columns={
            "t": "bar_open_timestamp_utc",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
            "vw": "vwap",
            "n": "trade_count",
        })
        df["provider_timestamp"] = df["bar_open_timestamp_utc"].astype(str)
        df["bar_open_timestamp_utc"] = pd.to_datetime(df["bar_open_timestamp_utc"], utc=True, errors="coerce")
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize("UTC")
        else:
            start_ts = start_ts.tz_convert("UTC")
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")
        df = df[(df["bar_open_timestamp_utc"] >= start_ts) & (df["bar_open_timestamp_utc"] < end_ts)]
        df = df.drop_duplicates("bar_open_timestamp_utc", keep="last")
        df["contract_code"] = None
        bars = normalize_bar_frame(df, instrument, source=self.provider, interval_minutes=5)
        return FetchBatch(
            instrument["canonical_symbol"], self.provider, bars=bars,
            metadata={
                "api_calls": self.api_calls,
                "feed": self.feed,
                "adjustment": "raw",
                "volume_scope": "IEX venue only; not consolidated US market volume" if self.feed == "iex" else self.feed,
            },
        )
