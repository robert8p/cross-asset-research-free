import pandas as pd

from app.quality import preinsert_bar_checks


def instrument():
    return {"normal_session": "24/7"}


def base_rows():
    return pd.DataFrame({
        "bar_open_timestamp_utc": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z"]),
        "bar_close_timestamp_utc": pd.to_datetime(["2026-01-01T00:05:00Z", "2026-01-01T00:10:00Z"]),
        "open": [100.0,100.0], "high": [101.0,99.0], "low": [99.0,98.0], "close": [100.5,100.0],
        "volume": [10.0,10.0], "quality_status": ["unchecked","unchecked"],
    })


def test_invalid_ohlc_is_excluded_and_logged():
    clean, issues = preinsert_bar_checks(base_rows(), instrument())
    assert len(clean) == 1
    assert "invalid_ohlc" in set(issues.issue_type)
    assert set(issues.disposition) == {"excluded"}


def test_extreme_negative_price_is_retained_for_review():
    rows = base_rows().iloc[[0]].copy()
    rows[["open","high","low","close"]] = [-1.0, 1.0, -2.0, -0.5]
    clean, issues = preinsert_bar_checks(rows, instrument())
    assert len(clean) == 1
    assert "negative_price" in set(issues.issue_type)
    assert set(issues.disposition) == {"retained"}
