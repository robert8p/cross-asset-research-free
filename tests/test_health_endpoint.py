from app import status_api


def test_health_is_liveness_even_when_bootstrap_failed(monkeypatch):
    monkeypatch.setattr(status_api, "BOOTSTRAP_ERROR", "database unavailable")
    payload = status_api.health()
    assert payload["ok"] is True
    assert payload["ready"] is False


def test_health_ready_when_bootstrap_succeeded(monkeypatch):
    monkeypatch.setattr(status_api, "BOOTSTRAP_ERROR", None)
    payload = status_api.health()
    assert payload["ok"] is True
    assert payload["ready"] is True
