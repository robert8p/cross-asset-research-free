import pandas as pd

from app.quality import evaluate_bars


def _instrument_frame(calendar="HKEX", normal="cash"):
    return pd.DataFrame([{
        "instrument_id": "i1",
        "canonical_symbol": "TEST",
        "normal_session": normal,
        "exchange_timezone": "Asia/Hong_Kong" if calendar == "HKEX" else "UTC",
        "metadata_json": {"session_calendar": calendar},
    }])


def _bars(times, trading_date, regular=True):
    ts = pd.to_datetime(times, utc=True)
    return pd.DataFrame({
        "bar_id": range(1, len(ts) + 1),
        "instrument_id": "i1",
        "interval": "5m",
        "bar_open_timestamp_utc": ts,
        "bar_close_timestamp_utc": ts + pd.Timedelta(minutes=5),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1.0,
        "contract_code": None,
        "is_continuous": False,
        "is_regular_session": regular,
        "exchange_trading_date": pd.to_datetime([trading_date] * len(ts)).date,
        "session_type": "regular_session",
        "is_partial_bar": False,
        "is_roll_affected": False,
    })


def _settings():
    return {"quality": {"stale_run_bars": 9999, "zero_volume_run_bars": 9999, "extreme_return_abs_threshold": 0.10, "flag_weekends_for_non_247": True}}


def test_hkex_scheduled_lunch_break_is_not_a_gap():
    # 2026-07-13: morning ends 04:00 UTC, afternoon starts 05:00 UTC.
    times = list(pd.date_range("2026-07-13T01:30:00Z", "2026-07-13T04:00:00Z", freq="5min", inclusive="left"))
    times += list(pd.date_range("2026-07-13T05:00:00Z", "2026-07-13T08:00:00Z", freq="5min", inclusive="left"))
    result = evaluate_bars(
        _bars(times, "2026-07-13"), _instrument_frame(), _settings(),
        coverage_start=pd.Timestamp("2026-07-13T01:30:00Z"),
        coverage_end_exclusive=pd.Timestamp("2026-07-13T08:00:00Z"),
    )
    assert "missing_bars_in_scheduled_session" not in set(result.issues["issue_type"])
    assert result.coverage.iloc[0]["coverage_percentage"] == 100.0
    assert result.coverage.iloc[0]["expected_observations"] == len(times)


def test_calendar_quality_finds_missing_session_edge_bar():
    times = list(pd.date_range("2026-07-13T01:35:00Z", "2026-07-13T04:00:00Z", freq="5min", inclusive="left"))
    times += list(pd.date_range("2026-07-13T05:00:00Z", "2026-07-13T08:00:00Z", freq="5min", inclusive="left"))
    result = evaluate_bars(
        _bars(times, "2026-07-13"), _instrument_frame(), _settings(),
        coverage_start=pd.Timestamp("2026-07-13T01:30:00Z"),
        coverage_end_exclusive=pd.Timestamp("2026-07-13T08:00:00Z"),
    )
    assert "missing_bars_in_scheduled_session" in set(result.issues["issue_type"])
    assert result.coverage.iloc[0]["missing_expected_observations"] == 1
