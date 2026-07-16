from __future__ import annotations

import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np
import pandas as pd

BAR_DB_COLUMNS = [
    "instrument_id", "interval", "provider_timestamp", "bar_open_timestamp_utc",
    "bar_close_timestamp_utc", "open", "high", "low", "close", "volume", "vwap",
    "trade_count", "bid", "ask", "mid", "open_interest", "source", "source_symbol",
    "contract_code", "expiry_date", "is_continuous", "continuous_method",
    "is_roll_affected", "session_type", "exchange_trading_date", "is_regular_session",
    "is_extended_session", "minutes_since_session_open", "minutes_until_session_close",
    "day_of_week", "is_holiday", "is_shortened_session", "is_partial_bar", "is_stale",
    "quality_status", "raw_payload_json",
]

YIELD_DB_COLUMNS = [
    "instrument_id", "observation_timestamp_utc", "observation_date", "maturity",
    "yield_value", "yield_type", "source", "source_series", "published_at",
    "vintage_date", "is_revised", "original_value",
]

INSTRUMENT_COLUMNS = [
    "canonical_symbol", "canonical_name", "asset_class", "subcategory", "instrument_type",
    "economic_exposure", "provider", "provider_symbol", "exchange", "exchange_timezone",
    "currency", "price_unit", "contract_multiplier", "contract_code", "expiry_date",
    "is_continuous", "continuous_method", "volume_type", "data_frequency", "normal_session",
    "extended_session", "active_from", "active_to", "data_licence",
    "redistribution_restrictions", "methodological_limitations", "metadata_json",
]


class DatabaseError(RuntimeError):
    pass


def _psycopg():
    try:
        import psycopg
        return psycopg
    except ImportError as exc:
        raise DatabaseError("psycopg is not installed. Run: pip install -r requirements.lock") from exc


def _clean(value: Any) -> Any:
    if value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.to_pydatetime()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return value


class Database:
    def __init__(self, url: str | None = None):
        self.url = url or os.getenv("SUPABASE_DB_URL")
        if not self.url:
            raise DatabaseError("SUPABASE_DB_URL is not set")

    @contextmanager
    def connection(self):
        psycopg = _psycopg()
        # prepare_threshold=None is compatible with Supabase transaction pooler connections.
        with psycopg.connect(self.url, prepare_threshold=None) as conn:
            yield conn

    def apply_migration(self, path: Path) -> None:
        sql = path.read_text(encoding="utf-8")
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

    def ping(self) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("select now(), current_database(), version()")
                now, name, version = cur.fetchone()
        return {"ok": True, "database": name, "server_time": now.isoformat(), "version": version}

    def upsert_instruments(self, instruments: Sequence[dict[str, Any]]) -> dict[str, str]:
        if not instruments:
            return {}
        columns = INSTRUMENT_COLUMNS
        placeholders = ",".join(["%s"] * len(columns))
        updates = ",".join(f"{c}=excluded.{c}" for c in columns if c != "canonical_symbol")
        sql = f"""
            insert into instruments ({','.join(columns)}) values ({placeholders})
            on conflict (canonical_symbol) do update set {updates}, updated_at=now()
            returning canonical_symbol, instrument_id
        """
        mapping: dict[str, str] = {}
        with self.connection() as conn:
            with conn.cursor() as cur:
                for item in instruments:
                    metadata = {k: v for k, v in item.items() if k not in columns and k not in {"enabled", "required"}}
                    row = dict(item)
                    row["metadata_json"] = json.dumps(metadata, default=str)
                    cur.execute(sql, tuple(_clean(row.get(c)) for c in columns))
                    symbol, instrument_id = cur.fetchone()
                    mapping[symbol] = str(instrument_id)
            conn.commit()
        return mapping

    def get_instrument_ids(self) -> dict[str, str]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("select canonical_symbol, instrument_id from instruments")
                return {row[0]: str(row[1]) for row in cur.fetchall()}

    def start_run(self, source: str, job_type: str, start: datetime, end: datetime, config_hash: str, metadata: dict[str, Any] | None = None) -> str:
        run_id = str(uuid.uuid4())
        git_commit = os.getenv("RENDER_GIT_COMMIT") or self._git_commit()
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """insert into ingestion_runs
                    (run_id, source, job_type, requested_start, requested_end, software_version, git_commit, configuration_hash, metadata_json)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (run_id, source, job_type, start, end, "1.1.0-free-data", git_commit, config_hash, json.dumps(metadata or {})),
                )
            conn.commit()
        return run_id

    def finish_run(self, run_id: str, *, status: str, rows_received: int = 0, rows_validated: int = 0, rows_inserted: int = 0, rows_updated: int = 0, duplicates: int = 0, rejected_rows: int = 0, api_calls: int = 0, retries: int = 0, error: Any = None, metadata: dict[str, Any] | None = None) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """update ingestion_runs set ended_at=now(), status=%s, rows_received=%s,
                    rows_validated=%s, rows_inserted=%s, rows_updated=%s, duplicates=%s,
                    rejected_rows=%s, api_calls=%s, retries=%s, error_details=%s,
                    metadata_json=metadata_json || %s::jsonb where run_id=%s""",
                    (status, rows_received, rows_validated, rows_inserted, rows_updated, duplicates,
                     rejected_rows, api_calls, retries, json.dumps(error, default=str) if error else None,
                     json.dumps(metadata or {}, default=str), run_id),
                )
            conn.commit()

    def bulk_upsert_bars(self, bars: pd.DataFrame, instrument_id: str, interval: str = "5m") -> dict[str, int]:
        if bars.empty:
            return {"received": 0, "inserted": 0, "updated": 0, "duplicates": 0}
        df = bars.copy()
        df.insert(0, "instrument_id", instrument_id)
        df.insert(1, "interval", interval)
        df = df.reindex(columns=BAR_DB_COLUMNS)
        identity = ["instrument_id", "interval", "bar_open_timestamp_utc", "contract_code", "is_continuous"]
        received = len(df)
        df = df.drop_duplicates(identity, keep="last").reset_index(drop=True)
        duplicate_count = received - len(df)
        cols = BAR_DB_COLUMNS
        update_cols = [c for c in cols if c not in {"instrument_id", "interval", "bar_open_timestamp_utc", "contract_code"}]
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("create temp table tmp_market_bars (like market_bars including defaults) on commit drop")
                with cur.copy(f"copy tmp_market_bars ({','.join(cols)}) from stdin") as copy:
                    for row in df.itertuples(index=False, name=None):
                        copy.write_row(tuple(_clean(v) for v in row))
                cur.execute("""
                    select count(*)
                    from tmp_market_bars t
                    join market_bars m
                      on m.instrument_id=t.instrument_id
                     and m.interval=t.interval
                     and m.bar_open_timestamp_utc=t.bar_open_timestamp_utc
                     and m.contract_code_key=coalesce(t.contract_code,'')
                     and m.is_continuous=t.is_continuous
                """)
                existing = int(cur.fetchone()[0])
                cur.execute(f"""
                    insert into market_bars ({','.join(cols)})
                    select {','.join(cols)} from tmp_market_bars
                    on conflict (instrument_id, interval, bar_open_timestamp_utc, contract_code_key, is_continuous)
                    do update set {','.join(f'{c}=excluded.{c}' for c in update_cols)}, ingested_at=now()
                """)
                affected = max(0, cur.rowcount)
            conn.commit()
        updated = min(existing, affected)
        inserted = max(0, affected - updated)
        return {"received": received, "inserted": inserted, "updated": updated, "duplicates": duplicate_count}

    def bulk_upsert_yields(self, yields: pd.DataFrame, instrument_id: str) -> dict[str, int]:
        if yields.empty:
            return {"received": 0, "inserted": 0, "updated": 0, "duplicates": 0}
        df = yields.copy()
        df.insert(0, "instrument_id", instrument_id)
        df = df.reindex(columns=YIELD_DB_COLUMNS)
        identity = ["instrument_id", "observation_date", "maturity", "vintage_date"]
        received = len(df)
        df = df.drop_duplicates(identity, keep="last").reset_index(drop=True)
        duplicate_count = received - len(df)
        cols = YIELD_DB_COLUMNS
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("create temp table tmp_yields (like yield_observations including defaults) on commit drop")
                with cur.copy(f"copy tmp_yields ({','.join(cols)}) from stdin") as copy:
                    for row in df.itertuples(index=False, name=None):
                        copy.write_row(tuple(_clean(v) for v in row))
                cur.execute("""
                    select count(*)
                    from tmp_yields t
                    join yield_observations y
                      on y.instrument_id=t.instrument_id
                     and y.observation_date=t.observation_date
                     and y.maturity=t.maturity
                     and y.vintage_date=t.vintage_date
                """)
                existing = int(cur.fetchone()[0])
                cur.execute(f"""
                    insert into yield_observations ({','.join(cols)})
                    select {','.join(cols)} from tmp_yields
                    on conflict (instrument_id, observation_date, maturity, vintage_date)
                    do update set yield_value=excluded.yield_value,
                    published_at=least(yield_observations.published_at, excluded.published_at),
                    is_revised=excluded.is_revised, original_value=excluded.original_value, ingested_at=now()
                """)
                affected = max(0, cur.rowcount)
            conn.commit()
        updated = min(existing, affected)
        inserted = max(0, affected - updated)
        return {"received": received, "inserted": inserted, "updated": updated, "duplicates": duplicate_count}

    def upsert_market_sessions(self, sessions: pd.DataFrame) -> dict[str, int]:
        if sessions.empty:
            return {"received": 0, "inserted": 0, "updated": 0, "duplicates": 0}
        cols = [
            "canonical_symbol", "exchange_trading_date", "exchange_timezone",
            "regular_open_utc", "regular_close_utc", "extended_open_utc",
            "extended_close_utc", "is_holiday", "is_shortened_session", "source",
            "metadata_json",
        ]
        df = sessions.reindex(columns=cols).copy()
        received = len(df)
        df = df.drop_duplicates(["canonical_symbol", "exchange_trading_date"], keep="last").reset_index(drop=True)
        duplicates = received - len(df)
        placeholders = ",".join(["%s"] * len(cols))
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    select count(*)
                    from market_sessions
                    where (canonical_symbol, exchange_trading_date) in (
                        select x.canonical_symbol, x.exchange_trading_date
                        from jsonb_to_recordset(%s::jsonb)
                             as x(canonical_symbol text, exchange_trading_date date)
                    )
                """, (json.dumps([
                    {"canonical_symbol": r["canonical_symbol"], "exchange_trading_date": str(r["exchange_trading_date"])}
                    for _, r in df.iterrows()
                ]),))
                existing = int(cur.fetchone()[0])
                sql = f"""
                    insert into market_sessions ({','.join(cols)}) values ({placeholders})
                    on conflict (canonical_symbol, exchange_trading_date) do update set
                    exchange_timezone=excluded.exchange_timezone,
                    regular_open_utc=excluded.regular_open_utc,
                    regular_close_utc=excluded.regular_close_utc,
                    extended_open_utc=excluded.extended_open_utc,
                    extended_close_utc=excluded.extended_close_utc,
                    is_holiday=excluded.is_holiday,
                    is_shortened_session=excluded.is_shortened_session,
                    source=excluded.source,
                    metadata_json=excluded.metadata_json
                """
                cur.executemany(sql, [tuple(_clean(r.get(c)) for c in cols) for _, r in df.iterrows()])
            conn.commit()
        updated = min(existing, len(df))
        return {"received": received, "inserted": len(df)-updated, "updated": updated, "duplicates": duplicates}

    def upsert_rolls(self, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = """insert into futures_rolls
            (continuous_instrument_id,outgoing_contract,incoming_contract,decision_timestamp,roll_timestamp,
             roll_basis,price_adjustment,adjustment_method,outgoing_volume,incoming_volume,
             outgoing_open_interest,incoming_open_interest,metadata_json)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (continuous_instrument_id,roll_timestamp,incoming_contract) do update set
            decision_timestamp=excluded.decision_timestamp, roll_basis=excluded.roll_basis,
            price_adjustment=excluded.price_adjustment, adjustment_method=excluded.adjustment_method,
            outgoing_volume=excluded.outgoing_volume, incoming_volume=excluded.incoming_volume,
            metadata_json=excluded.metadata_json"""
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, [(
                    r["continuous_instrument_id"], r["outgoing_contract"], r["incoming_contract"],
                    r["decision_timestamp"], r["roll_timestamp"], r.get("roll_basis", "provider_point_in_time_volume_mapping"),
                    r.get("price_adjustment"), r.get("adjustment_method", "unadjusted"),
                    r.get("outgoing_volume"), r.get("incoming_volume"), r.get("outgoing_open_interest"),
                    r.get("incoming_open_interest"), json.dumps(r.get("metadata_json", {}), default=str),
                ) for r in rows])
            conn.commit()
        return len(rows)

    def record_quality_issues(self, issues: pd.DataFrame, run_id: str | None = None) -> int:
        if issues.empty:
            return 0
        cols = ["run_id", "instrument_id", "issue_timestamp", "issue_type", "severity",
                "observed_value", "expected_condition", "resolution", "disposition",
                "original_value_json", "corrected_value_json"]
        sql = f"insert into data_quality_issues ({','.join(cols)}) values ({','.join(['%s']*len(cols))})"
        rows = []
        for _, r in issues.iterrows():
            rows.append(tuple(_clean(run_id if c == "run_id" else r.get(c)) for c in cols))
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
        return len(rows)

    def get_checkpoint(self, provider: str, canonical_symbol: str, interval: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """select last_complete_timestamp_utc, metadata_json, updated_at
                       from collector_checkpoints
                       where provider=%s and canonical_symbol=%s and interval=%s""",
                    (provider, canonical_symbol, interval),
                )
                row = cur.fetchone()
        if not row:
            return None
        metadata = row[1] if isinstance(row[1], dict) else json.loads(row[1] or "{}")
        return {
            "last_complete_timestamp_utc": row[0],
            "metadata": metadata,
            "updated_at": row[2],
        }

    def checkpoint(self, provider: str, canonical_symbol: str, interval: str, timestamp: datetime, metadata: dict[str, Any] | None = None) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""insert into collector_checkpoints
                    (provider,canonical_symbol,interval,last_complete_timestamp_utc,metadata_json)
                    values (%s,%s,%s,%s,%s) on conflict (provider,canonical_symbol,interval)
                    do update set last_complete_timestamp_utc=excluded.last_complete_timestamp_utc,
                    metadata_json=excluded.metadata_json,updated_at=now()""",
                    (provider, canonical_symbol, interval, timestamp, json.dumps(metadata or {}, default=str)))
            conn.commit()

    def read_dataframe(self, sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                rows = cur.fetchall()
                columns = [x.name for x in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=columns)

    @staticmethod
    def _git_commit() -> str | None:
        try:
            return subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
        except Exception:
            return None
