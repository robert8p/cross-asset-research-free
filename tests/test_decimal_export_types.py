from decimal import Decimal

import pandas as pd
import pytest

from app.exporter import align_bars_without_fill, build_curve_features


def test_decimal_market_values_are_normalised_for_return_calculation():
    frame = pd.DataFrame(
        {
            "bar_open_timestamp_utc": pd.to_datetime(
                ["2026-01-01T14:30:00Z", "2026-01-01T14:35:00Z"]
            ),
            "canonical_symbol": ["SPY_SP500_PROXY", "SPY_SP500_PROXY"],
            "close": [Decimal("100.00"), Decimal("101.00")],
            "volume": [Decimal("10"), Decimal("12")],
            "vwap": [Decimal("100.00"), Decimal("100.75")],
            "trade_count": [Decimal("2"), Decimal("3")],
        }
    )

    aligned = align_bars_without_fill(frame)

    assert aligned["close"].dtype == "float64"
    assert aligned.loc[1, "return_5m"] == pytest.approx(0.01)


def test_decimal_yields_are_normalised_for_curve_features():
    frame = pd.DataFrame(
        {
            "observation_timestamp_utc": pd.to_datetime(
                ["2026-01-02T21:00:00Z", "2026-01-02T21:00:00Z"]
            ),
            "canonical_symbol": ["US2Y_YIELD", "US10Y_YIELD"],
            "yield_value": [Decimal("3.50"), Decimal("4.00")],
        }
    )

    features = build_curve_features(frame)

    assert features.loc[0, "US_2S10S_BP"] == pytest.approx(50.0)
