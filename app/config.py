from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

PROJECT_ROOT = Path(os.getenv("CROSS_ASSET_PROJECT_ROOT", Path(__file__).resolve().parents[1]))


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    settings: dict[str, Any]
    instruments: list[dict[str, Any]]
    root: Path = PROJECT_ROOT

    @property
    def enabled_instruments(self) -> list[dict[str, Any]]:
        return [x for x in self.instruments if x.get("enabled", True)]

    def by_provider(self, provider: str) -> list[dict[str, Any]]:
        return [x for x in self.enabled_instruments if x.get("provider") == provider]

    def hash(self) -> str:
        payload = {"settings": self.settings, "instruments": self.instruments}
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"Expected a YAML mapping in {path}")
    return data


def load_config(root: Path | None = None) -> ProjectConfig:
    base = root or PROJECT_ROOT
    settings_doc = _read_yaml(base / "config" / "settings.yaml")
    instruments_doc = _read_yaml(base / "config" / "instruments.yaml")
    instruments = instruments_doc.get("instruments", [])
    if not isinstance(instruments, list) or not instruments:
        raise ConfigurationError("config/instruments.yaml contains no instruments")
    symbols = [x.get("canonical_symbol") for x in instruments]
    duplicates = sorted({x for x in symbols if symbols.count(x) > 1})
    if duplicates:
        raise ConfigurationError(f"Duplicate canonical symbols: {duplicates}")
    return ProjectConfig(settings=settings_doc, instruments=instruments, root=base)


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def default_date_range(config: ProjectConfig, history_days: int | None = None) -> tuple[datetime, datetime]:
    days = history_days or int(config.settings["project"].get("default_history_days", 90))
    now = datetime.now(timezone.utc)
    # Exclude the current incomplete five-minute bar.
    end = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
    return end - timedelta(days=days), end


def select_instruments(
    config: ProjectConfig,
    symbols: Iterable[str] | None = None,
    providers: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    selected = config.enabled_instruments
    if symbols:
        wanted = {x.strip().upper() for x in symbols}
        selected = [x for x in selected if x["canonical_symbol"].upper() in wanted]
        missing = wanted - {x["canonical_symbol"].upper() for x in selected}
        if missing:
            raise ConfigurationError(f"Unknown or disabled instrument(s): {sorted(missing)}")
    if providers:
        pset = {x.strip().lower() for x in providers}
        selected = [x for x in selected if x["provider"].lower() in pset]
    return selected


def validate_environment(instruments: list[dict[str, Any]], require_database: bool = True) -> list[str]:
    required: dict[str, str] = {}
    providers = {x["provider"] for x in instruments}
    if "alpaca" in providers:
        required["ALPACA_API_KEY"] = "Alpaca paper-account API key"
        required["ALPACA_SECRET_KEY"] = "Alpaca paper-account secret key"
    if "databento" in providers:
        required["DATABENTO_API_KEY"] = "Databento historical API key"
    if "twelve_data" in providers:
        required["TWELVE_DATA_API_KEY"] = "Twelve Data API key"
    if "fred" in providers:
        required["FRED_API_KEY"] = "FRED API key"
    if require_database:
        required["SUPABASE_DB_URL"] = "Supabase Postgres connection string"
    missing = [f"{key} ({description})" for key, description in required.items() if not os.getenv(key)]
    return missing
