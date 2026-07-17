from datetime import datetime, timezone

import pandas as pd

from app.round2 import ROUND2_BAR_SYMBOLS, build_15m_observed_bars, build_decision_time_reference


def test_round2_primary_universe_is_frozen_and_liquid():
    assert ROUND2_BAR_SYMBOLS == [
        "BTC_USD_SPOT", "DIA_DJIA_PROXY", "EWJ_JAPAN_EQUITY_PROXY", "GLD_GOLD_PROXY",
        "IEF_US10Y_RATE_PROXY", "IWM_RUSSELL2000_PROXY", "QQQ_NASDAQ100_PROXY",
        "SLV_SILVER_PROXY", "SPY_SP500_PROXY", "TLT_US30Y_RATE_PROXY",
    ]


def test_15m_aggregation_never_fills_missing_five_minute_bars():
    bars = pd.DataFrame({
        "canonical_symbol": ["SPY_SP500_PROXY", "SPY_SP500_PROXY"],
        "bar_open_timestamp_utc": ["2026-01-05T14:30:00Z", "2026-01-05T14:40:00Z"],
        "bar_close_timestamp_utc": ["2026-01-05T14:35:00Z", "2026-01-05T14:45:00Z"],
        "open": [100, 101], "high": [101, 102], "low": [99, 100], "close": [100.5, 101.5],
        "volume": [10, 20], "vwap": [100.2, 101.2], "trade_count": [2, 3],
        "exchange_trading_date": ["2026-01-05", "2026-01-05"],
        "is_regular_session": [True, True], "is_extended_session": [False, False],
    })
    out = build_15m_observed_bars(bars)
    assert len(out) == 1
    assert int(out.iloc[0]["observed_5m_bars"]) == 2
    assert bool(out.iloc[0]["complete_15m"]) is False
    assert pd.isna(out.iloc[0]["return_15m"])


def test_decision_times_use_london_dst_and_us_session_reference():
    bars = pd.DataFrame({
        "canonical_symbol": ["SPY_SP500_PROXY", "SPY_SP500_PROXY"],
        "bar_open_timestamp_utc": ["2025-07-01T13:30:00Z", "2025-07-01T19:55:00Z"],
        "bar_close_timestamp_utc": ["2025-07-01T13:35:00Z", "2025-07-01T20:00:00Z"],
        "exchange_trading_date": ["2025-07-01", "2025-07-01"],
        "is_regular_session": [True, True],
    })
    out = build_decision_time_reference(bars)
    row = out[out["decision_time_label"].eq("14:00 BST/London")].iloc[0]
    assert pd.Timestamp(row["decision_timestamp_utc"]) == pd.Timestamp("2025-07-01T13:00:00Z")
    assert bool(row["is_before_us_open"]) is True
