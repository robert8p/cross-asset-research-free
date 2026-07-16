from datetime import datetime, timezone

import pandas as pd

from app.exporter import align_bars_without_fill, determine_split

SETTINGS = {"project": {"untouched_test_days": 30, "minimum_untouched_fraction": 0.20}}


def test_90_day_split_uses_30_day_untouched_period():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 1, tzinfo=timezone.utc)
    split = determine_split(start, end, SETTINGS)
    assert (split.discovery_end - split.discovery_start).days == 60
    assert (split.untouched_end - split.untouched_start).days == 30


def test_60_day_fallback_uses_at_least_twenty_percent():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 2, tzinfo=timezone.utc)
    split = determine_split(start, end, SETTINGS)
    assert (split.untouched_end - split.untouched_start).days >= 12


def test_alignment_marks_missing_and_does_not_bridge_return():
    bars = pd.DataFrame({
        "canonical_symbol": ["BTC", "BTC"],
        "bar_open_timestamp_utc": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"]),
        "close": [100.0, 110.0], "open": [100.0,110.0], "high": [101.0,111.0], "low": [99.0,109.0],
    })
    out = align_bars_without_fill(bars)
    middle = out[out.bar_open_timestamp_utc == pd.Timestamp("2026-01-01T00:05:00Z")].iloc[0]
    last = out[out.bar_open_timestamp_utc == pd.Timestamp("2026-01-01T00:10:00Z")].iloc[0]
    assert bool(middle.is_missing_slot) is True
    assert pd.isna(middle.close)
    assert pd.isna(last.return_5m)
