from datetime import datetime, timezone

import pandas as pd

from app.adapters.base import resample_ohlcv_1m_to_5m


def instrument():
    return {
        "provider_symbol": "ES.v.0", "exchange_timezone": "UTC", "is_continuous": True,
        "continuous_method": "volume_based_unadjusted", "normal_session": "exchange",
    }


def test_resample_never_fills_missing_minutes_and_flags_partial():
    times = pd.to_datetime([
        "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "2026-01-01T00:04:00Z",
        "2026-01-01T00:05:00Z", "2026-01-01T00:06:00Z", "2026-01-01T00:07:00Z",
        "2026-01-01T00:08:00Z", "2026-01-01T00:09:00Z",
    ])
    frame = pd.DataFrame({
        "bar_open_timestamp_utc": times,
        "open": [1,2,3,4,5,6,7,8], "high": [2,3,4,5,6,7,8,9],
        "low": [0,1,2,3,4,5,6,7], "close": [1.5,2.5,3.5,4.5,5.5,6.5,7.5,8.5],
        "volume": [10]*8, "contract_code": ["ESH6"]*8,
    })
    out = resample_ohlcv_1m_to_5m(frame, instrument(), "databento")
    assert len(out) == 2
    assert bool(out.iloc[0].is_partial_bar) is True
    assert bool(out.iloc[1].is_partial_bar) is False
    assert out.iloc[0].volume == 30
    assert out.iloc[0].open == 1
    assert out.iloc[0].close == 3.5


def test_contracts_are_never_mixed_inside_a_bucket():
    frame = pd.DataFrame({
        "bar_open_timestamp_utc": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"]),
        "open": [100, 200], "high": [101,201], "low": [99,199], "close": [100.5,200.5],
        "volume": [10,20], "contract_code": ["ESH6","ESM6"],
    })
    out = resample_ohlcv_1m_to_5m(frame, instrument(), "databento")
    assert len(out) == 2
    assert set(out.contract_code) == {"ESH6", "ESM6"}
