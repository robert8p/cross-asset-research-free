import pandas as pd

from app.exporter import align_bars_without_fill


def test_aligned_export_is_compact_and_does_not_repeat_raw_metadata():
    frame = pd.DataFrame({
        "bar_open_timestamp_utc": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z"]),
        "canonical_symbol": ["BTC_USD_SPOT", "BTC_USD_SPOT"],
        "close": [100.0, 101.0],
        "volume": [1.0, 2.0],
        "source": ["coinbase", "coinbase"],
        "methodological_limitations": ["large repeated text", "large repeated text"],
        "is_regular_session": [True, True],
    })
    aligned = align_bars_without_fill(frame)
    assert "source" not in aligned.columns
    assert "methodological_limitations" not in aligned.columns
    assert {"is_observed", "is_missing_slot", "return_5m"}.issubset(aligned.columns)
    assert len(aligned) == 2
