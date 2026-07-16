from pathlib import Path

import yaml

from app.control import _extract_last_json, derive_supabase_url


def test_derives_supabase_url_from_pooler_username():
    url = "postgresql://postgres.abc123:secret@aws-0-eu-west-2.pooler.supabase.com:6543/postgres"
    assert derive_supabase_url(url) == "https://abc123.supabase.co"


def test_extracts_final_cli_json_after_logs():
    text = 'log {not json}\n{\n  "status": "succeeded",\n  "storage_uploads": [{"object": "archives/discovery.zip"}]\n}\n'
    assert _extract_last_json(text)["status"] == "succeeded"


def test_blueprint_is_single_service_and_prompts_six_values():
    doc = yaml.safe_load(Path("render.yaml").read_text())
    assert len(doc["services"]) == 1
    env = doc["services"][0]["envVars"]
    prompted = [item["key"] for item in env if item.get("sync") is False]
    assert prompted == [
        "SUPABASE_DB_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "FRED_API_KEY",
        "DASHBOARD_PASSWORD",
    ]
