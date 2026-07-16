from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
import numpy as np
import pandas as pd

from ..session_utils import enrich_bar_sessions

BAR_COLUMNS = [
    "provider_timestamp", "bar_open_timestamp_utc", "bar_close_timestamp_utc",
    "open", "high", "low", "close", "volume", "vwap", "trade_count",
    "bid", "ask", "mid", "open_interest", "source", "source_symbol",
    "contract_code", "expiry_date", "is_continuous", "continuous_method",
    "is_roll_affected", "session_type", "exchange_trading_date",
    "is_regular_session", "is_extended_session", "minutes_since_session_open",
    "minutes_until_session_close", "day_of_week", "is_holiday",
    "is_shortened_session", "is_partial_bar", "is_stale", "quality_status",
    "raw_payload_json",
]

YIELD_COLUMNS = [
    "observation_timestamp_utc", "observation_date", "maturity", "yield_value",
    "yield_type", "source", "source_series", "published_at", "vintage_date",
    "is_revised", "original_value",
]


@dataclass
class FetchBatch:
    canonical_symbol: str
    provider: str
    bars: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=BAR_COLUMNS))
    yields: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=YIELD_COLUMNS))
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    provider: str

    def __init__(self, settings: dict[str, Any], dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self.api_calls = 0
        self.retries = 0

    @abstractmethod
    def fetch(self, instrument: dict[str, Any], start: datetime, end: datetime) -> FetchBatch:
        raise NotImplementedError

    def preflight(self, instrument: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
        return {"ok": True, "provider": self.provider, "symbol": instrument["provider_symbol"]}


def utc_timestamp(series: pd.Series | pd.Index | list[Any]) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLUMNS)


def normalize_bar_frame(
    frame: pd.DataFrame,
    instrument: dict[str, Any],
    *,
    source: str,
    interval_minutes: int = 5,
) -> pd.DataFrame:
    if frame.empty:
        return empty_bars()
    df = frame.copy()
    df["bar_open_timestamp_utc"] = utc_timestamp(df["bar_open_timestamp_utc"])
    df = df[df["bar_open_timestamp_utc"].notna()].copy()
    df["bar_close_timestamp_utc"] = df["bar_open_timestamp_utc"] + pd.Timedelta(minutes=interval_minutes)
    for col in ("open", "high", "low", "close", "volume", "vwap", "bid", "ask", "mid", "open_interest"):
        if col not in df:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "trade_count" not in df:
        df["trade_count"] = pd.Series(pd.NA, index=df.index, dtype="Int64")
    else:
        df["trade_count"] = pd.to_numeric(df["trade_count"], errors="coerce").astype("Int64")
    df["provider_timestamp"] = df.get("provider_timestamp", df["bar_open_timestamp_utc"].astype(str))
    df["source"] = source
    df["source_symbol"] = instrument["provider_symbol"]
    if "contract_code" not in df:
        df["contract_code"] = instrument.get("contract_code")
    df["expiry_date"] = df.get("expiry_date", instrument.get("expiry_date"))
    df["is_continuous"] = bool(instrument.get("is_continuous", False))
    df["continuous_method"] = instrument.get("continuous_method")
    if "is_roll_affected" not in df:
        df["is_roll_affected"] = False
    else:
        df["is_roll_affected"] = df["is_roll_affected"].fillna(False).astype(bool)
    df = enrich_bar_sessions(df, instrument)
    if "is_partial_bar" not in df:
        df["is_partial_bar"] = False
    else:
        df["is_partial_bar"] = df["is_partial_bar"].fillna(False).astype(bool)
    if "is_stale" not in df:
        df["is_stale"] = False
    else:
        df["is_stale"] = df["is_stale"].fillna(False).astype(bool)
    df["quality_status"] = df.get("quality_status", "unchecked")
    df["raw_payload_json"] = df.get("raw_payload_json", None)
    return df.reindex(columns=BAR_COLUMNS).sort_values(["bar_open_timestamp_utc", "contract_code"], na_position="last").reset_index(drop=True)


def resample_ohlcv_1m_to_5m(frame: pd.DataFrame, instrument: dict[str, Any], source: str) -> pd.DataFrame:
    """Aggregate observed one-minute bars only. It never fills missing minutes."""
    if frame.empty:
        return empty_bars()
    df = frame.copy()
    df["bar_open_timestamp_utc"] = utc_timestamp(df["bar_open_timestamp_utc"])
    df = df.dropna(subset=["bar_open_timestamp_utc", "open", "high", "low", "close"])
    if "contract_code" not in df:
        df["contract_code"] = instrument.get("contract_code")
    df["bucket"] = df["bar_open_timestamp_utc"].dt.floor("5min")
    keys = ["contract_code", "bucket"]
    agg = {
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": lambda s: s.sum(min_count=1),
    }
    for optional, method in (("vwap", "mean"), ("trade_count", "sum"), ("open_interest", "last"), ("expiry_date", "last")):
        if optional in df.columns:
            agg[optional] = method
    result = df.sort_values("bar_open_timestamp_utc").groupby(keys, dropna=False, sort=True).agg(agg).reset_index()
    counts = df.groupby(keys, dropna=False).size().rename("observed_minutes").reset_index()
    result = result.merge(counts, on=keys, how="left")
    result = result.rename(columns={"bucket": "bar_open_timestamp_utc"})
    result["is_partial_bar"] = result["observed_minutes"] < 5
    result.drop(columns=["observed_minutes"], inplace=True)
    return normalize_bar_frame(result, instrument, source=source, interval_minutes=5)


def chunk_time_range(start: datetime, end: datetime, delta: timedelta) -> Iterator[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        chunk_end = min(end, cursor + delta)
        yield cursor, chunk_end
        cursor = chunk_end
