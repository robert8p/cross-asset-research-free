from datetime import datetime, timezone

from app.adapters.alpaca_adapter import AlpacaAdapter


def test_alpaca_normalises_and_paginates(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    calls = []

    def fake_request_json(method, url, **kwargs):
        calls.append(kwargs["params"].copy())
        if len(calls) == 1:
            return {
                "bars": [{"t": "2026-07-01T13:30:00Z", "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 20, "n": 3, "vw": 100.2}],
                "next_page_token": "next",
            }
        return {
            "bars": [{"t": "2026-07-01T13:35:00Z", "o": 100.5, "h": 102, "l": 100, "c": 101.5, "v": 30, "n": 4, "vw": 101.1}],
            "next_page_token": None,
        }

    monkeypatch.setattr("app.adapters.alpaca_adapter.request_json", fake_request_json)
    adapter = AlpacaAdapter({"collection": {"request_timeout_seconds": 1, "max_retries": 0}})
    instrument = {
        "canonical_symbol": "SPY_SP500_PROXY", "provider_symbol": "SPY",
        "exchange_timezone": "America/New_York", "normal_session": "09:30-16:00 America/New_York",
        "session_calendar": "XNYS", "is_continuous": False,
    }
    batch = adapter.fetch(
        instrument,
        datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 1, 13, 40, tzinfo=timezone.utc),
    )
    assert len(batch.bars) == 2
    assert batch.bars.iloc[0]["open"] == 100
    assert batch.bars.iloc[1]["trade_count"] == 4
    assert batch.metadata["feed"] == "iex"
    assert calls[0]["adjustment"] == "raw"
    assert calls[0]["feed"] == "iex"
    assert calls[1]["page_token"] == "next"


def test_nonfree_feed_requires_explicit_override(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_FEED", "sip")
    monkeypatch.delenv("ALLOW_NONFREE_ALPACA_FEED", raising=False)
    try:
        AlpacaAdapter({"collection": {}})
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "free-data edition" in str(exc)
