from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import app.exporter as exporter_module
from app.config import ProjectConfig, load_config
from app.exporter import Exporter
from app.quality import QualityResult


class FakeDatabase:
    def __init__(self, bars, yields, instruments):
        self.bars = bars
        self.yields = yields
        self.instruments = instruments
        self.calls = []

    def read_dataframe(self, sql, params=None):
        self.calls.append((sql, params))
        text = " ".join(sql.lower().split())
        if "min(bar_open_timestamp_utc)" in text:
            return pd.DataFrame({"min_ts": [self.bars.bar_open_timestamp_utc.min()], "max_ts": [self.bars.bar_close_timestamp_utc.max()]})
        if "from market_bars b join instruments" in text:
            start, end = params
            return self.bars[(self.bars.bar_open_timestamp_utc >= start) & (self.bars.bar_open_timestamp_utc < end)].copy()
        if "from yield_observations y join instruments" in text:
            start, end = params
            return self.yields[(self.yields.observation_timestamp_utc >= start) & (self.yields.observation_timestamp_utc < end)].copy()
        if "select * from instruments" in text:
            return self.instruments.copy()
        if "from futures_rolls" in text or "from market_sessions" in text:
            return pd.DataFrame()
        raise AssertionError(sql)


def make_bars():
    timestamps = pd.to_datetime([
        "2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z",
        "2026-01-03T00:00:00Z", "2026-01-03T00:05:00Z",
    ])
    return pd.DataFrame({
        "bar_id": [1,2,3,4], "instrument_id": ["i1"]*4, "interval": ["5m"]*4,
        "provider_timestamp": timestamps.astype(str), "bar_open_timestamp_utc": timestamps,
        "bar_close_timestamp_utc": timestamps + pd.Timedelta(minutes=5),
        "open": [100,101,200,201], "high": [101,102,201,202], "low": [99,100,199,200],
        "close": [100.5,101.5,200.5,201.5], "volume": [10,11,12,13],
        "vwap": [None]*4, "trade_count": [None]*4, "bid": [None]*4, "ask": [None]*4,
        "mid": [None]*4, "open_interest": [None]*4, "source": ["coinbase"]*4,
        "source_symbol": ["BTC-USD"]*4, "contract_code": [None]*4, "expiry_date": [None]*4,
        "is_continuous": [False]*4, "continuous_method": [None]*4, "is_roll_affected": [False]*4,
        "session_type": ["24x7"]*4, "exchange_trading_date": [x.date() for x in timestamps],
        "is_regular_session": [True]*4, "is_extended_session": [False]*4,
        "minutes_since_session_open": [None]*4, "minutes_until_session_close": [None]*4,
        "day_of_week": timestamps.dayofweek, "is_holiday": [False]*4, "is_shortened_session": [False]*4,
        "is_partial_bar": [False]*4, "is_stale": [False]*4, "quality_status": ["pass"]*4,
        "raw_payload_json": [None]*4, "ingested_at": timestamps,
        "canonical_symbol": ["BTC_USD_SPOT"]*4, "canonical_name": ["Bitcoin"]*4,
        "asset_class": ["cryptocurrency"]*4, "subcategory": ["spot"]*4,
        "instrument_type": ["spot"]*4, "economic_exposure": ["BTC/USD"]*4,
        "exchange": ["Coinbase"]*4, "exchange_timezone": ["UTC"]*4, "currency": ["USD"]*4,
        "volume_type": ["genuine"]*4, "data_frequency": ["5m_native"]*4,
        "normal_session": ["24/7"]*4, "methodological_limitations": ["single venue"]*4,
    })


def test_exporter_quality_never_receives_untouched_rows(tmp_path, monkeypatch):
    base = load_config()
    settings = {**base.settings, "exports": {**base.settings["exports"], "output_directory": "exports", "create_full_archive": False}}
    config = ProjectConfig(settings=settings, instruments=base.instruments, root=tmp_path)
    bars = make_bars()
    yields = pd.DataFrame(columns=["observation_timestamp_utc", "canonical_symbol"])
    instruments = pd.DataFrame([{
        "instrument_id": "i1", "canonical_symbol": "BTC_USD_SPOT", "canonical_name": "Bitcoin",
        "asset_class": "cryptocurrency", "subcategory": "spot", "instrument_type": "spot",
        "economic_exposure": "BTC/USD", "provider": "coinbase", "provider_symbol": "BTC-USD",
        "exchange": "Coinbase", "exchange_timezone": "UTC", "currency": "USD", "volume_type": "genuine",
        "data_frequency": "5m_native", "normal_session": "24/7", "data_licence": "terms",
        "redistribution_restrictions": "internal", "methodological_limitations": "single venue",
    }])
    fake = FakeDatabase(bars, yields, instruments)
    seen = []

    def fake_quality(frame, instruments_frame, settings_frame, **kwargs):
        seen.extend(pd.to_datetime(frame.bar_open_timestamp_utc, utc=True).tolist())
        coverage = pd.DataFrame([{"instrument_id": "i1", "canonical_symbol": "BTC_USD_SPOT"}])
        return QualityResult(pd.DataFrame(), coverage, len(frame))

    monkeypatch.setattr(exporter_module, "evaluate_bars", fake_quality)
    monkeypatch.setattr(exporter_module, "evaluate_yields", lambda frame: pd.DataFrame())
    cutoff = datetime(2026, 1, 2, tzinfo=timezone.utc)
    result = Exporter(config, fake).create(explicit_untouched_start=cutoff, include_full_archive=False)
    assert seen
    assert max(seen) < cutoff
    assert result.discovery_archive.exists()
    assert result.untouched_archive.exists()
