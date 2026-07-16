import pandas as pd

from app.adapters.base import normalize_bar_frame
from app.session_utils import build_market_sessions


def _frame(timestamps):
    n = len(timestamps)
    return pd.DataFrame({
        "bar_open_timestamp_utc": pd.to_datetime(timestamps, utc=True),
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.5] * n,
        "volume": [10.0] * n,
    })


def test_cme_overnight_bar_gets_next_trading_date():
    instrument = {
        "canonical_symbol": "SP500_FUT_CONT",
        "provider_symbol": "ES.v.0",
        "exchange_timezone": "America/Chicago",
        "normal_session": "Globex",
        "session_calendar": "CME_Equity",
        "is_continuous": True,
    }
    bars = normalize_bar_frame(_frame(["2026-07-12T22:00:00Z"]), instrument, source="test")
    assert str(bars.loc[0, "exchange_trading_date"]) == "2026-07-13"
    assert bool(bars.loc[0, "is_regular_session"])
    assert bars.loc[0, "minutes_since_session_open"] == 0


def test_hkex_lunch_break_is_not_treated_as_open():
    instrument = {
        "canonical_symbol": "HANGSENG_CASH",
        "provider_symbol": "HSI",
        "exchange_timezone": "Asia/Hong_Kong",
        "normal_session": "cash",
        "session_calendar": "HKEX",
        "is_continuous": False,
    }
    bars = normalize_bar_frame(_frame(["2026-07-13T04:30:00Z"]), instrument, source="test")
    assert bars.loc[0, "session_type"] == "scheduled_break_observation"
    assert not bool(bars.loc[0, "is_regular_session"])
    assert not bool(bars.loc[0, "is_extended_session"])


def test_market_session_export_marks_schedule_source():
    instrument = {
        "canonical_symbol": "BTC_USD_SPOT",
        "exchange_timezone": "UTC",
        "session_calendar": "24/7",
    }
    sessions = build_market_sessions(
        instrument,
        pd.Timestamp("2026-07-13T00:00:00Z").to_pydatetime(),
        pd.Timestamp("2026-07-15T00:00:00Z").to_pydatetime(),
    )
    assert len(sessions) == 2
    assert sessions["source"].str.contains("24/7", regex=False).all()
