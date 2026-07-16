import pandas as pd

from app.pipeline import detect_rolls


def test_roll_detected_at_first_incoming_contract_bar():
    bars = pd.DataFrame({
        "bar_open_timestamp_utc": pd.to_datetime([
            "2026-03-10T10:00:00Z", "2026-03-10T10:05:00Z", "2026-03-10T10:10:00Z"
        ]),
        "contract_code": ["ESH6", "ESH6", "ESM6"],
        "volume": [100, 90, 120], "source": ["databento"]*3,
    })
    rolls = detect_rolls(bars, "instrument-id")
    assert len(rolls) == 1
    assert rolls[0]["outgoing_contract"] == "ESH6"
    assert rolls[0]["incoming_contract"] == "ESM6"
    assert rolls[0]["adjustment_method"] == "unadjusted"
    assert rolls[0]["roll_timestamp"] == pd.Timestamp("2026-03-10T10:10:00Z")
