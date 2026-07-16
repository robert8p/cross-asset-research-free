from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .adapters import create_adapter
from .config import ProjectConfig
from .db import Database
from .quality import preinsert_bar_checks
from .session_utils import build_market_sessions

LOGGER = logging.getLogger(__name__)


@dataclass
class CollectionSummary:
    succeeded: list[str]
    failed: dict[str, str]
    rows: int
    api_calls: int


def _raw_contract_instrument(parent: dict[str, Any], contract_code: str, expiry_date: Any = None) -> dict[str, Any]:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", contract_code).strip("_").upper()
    return {
        **parent,
        "canonical_symbol": f"{parent['canonical_symbol']}__RAW__{safe}",
        "canonical_name": f"{parent['canonical_name']} — raw contract {contract_code}",
        "instrument_type": "future_contract",
        "provider_symbol": contract_code,
        "contract_code": contract_code,
        "expiry_date": expiry_date,
        "is_continuous": False,
        "continuous_method": None,
        "enabled": True,
        "required": False,
        "methodological_limitations": "Raw unadjusted contract extracted from point-in-time continuous-symbol response; expiry date is null unless separately supplied by provider metadata.",
    }


def detect_rolls(bars: pd.DataFrame, continuous_instrument_id: str) -> list[dict[str, Any]]:
    if bars.empty or "contract_code" not in bars:
        return []
    ordered = bars.sort_values("bar_open_timestamp_utc").reset_index(drop=True)
    changed = ordered["contract_code"].fillna("").ne(ordered["contract_code"].fillna("").shift())
    rolls = []
    for idx in ordered.index[changed]:
        if idx == 0 or not ordered.at[idx, "contract_code"]:
            continue
        previous = ordered.iloc[idx - 1]
        current = ordered.iloc[idx]
        rolls.append({
            "continuous_instrument_id": continuous_instrument_id,
            "outgoing_contract": previous["contract_code"],
            "incoming_contract": current["contract_code"],
            "decision_timestamp": current["bar_open_timestamp_utc"],
            "roll_timestamp": current["bar_open_timestamp_utc"],
            "roll_basis": "provider point-in-time volume-ranked continuous mapping",
            "price_adjustment": None,
            "adjustment_method": "unadjusted",
            "outgoing_volume": None,
            "incoming_volume": None,
            "metadata_json": {
                "source": current.get("source"),
                "bar_interval": "5m",
                "adjacent_outgoing_bar_volume": previous.get("volume"),
                "adjacent_incoming_bar_volume": current.get("volume"),
                "note": "Adjacent bar volumes are not claimed to be the provider roll-decision aggregates.",
            },
        })
    return rolls


def checkpoint_resume_start(
    requested_start: datetime,
    requested_end: datetime,
    checkpoint: dict[str, Any] | None,
    job_type: str,
    overlap: timedelta,
) -> datetime:
    """Resume only a checkpoint created for the exact same historical request.

    This prevents a newer prospective checkpoint from causing an older historical range
    to be skipped. The overlap re-fetches the boundary safely for roll detection/upserts.
    """
    if job_type != "historical_backfill" or not checkpoint:
        return requested_start
    metadata = checkpoint.get("metadata") or {}
    if metadata.get("job_type") != job_type:
        return requested_start
    if metadata.get("requested_start") != requested_start.isoformat():
        return requested_start
    if metadata.get("requested_end") != requested_end.isoformat():
        return requested_start
    stamp = checkpoint.get("last_complete_timestamp_utc")
    if stamp is None:
        return requested_start
    stamp = pd.Timestamp(stamp)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    bounded = min(stamp.to_pydatetime(), requested_end)
    return max(requested_start, bounded - overlap)


class Collector:
    def __init__(self, config: ProjectConfig, database: Database | None, dry_run: bool = False):
        self.config = config
        self.database = database
        self.dry_run = dry_run

    def _chunks(self, provider: str, start: datetime, end: datetime):
        collection = self.config.settings.get("collection", {})
        by_provider = collection.get("pipeline_chunk_days_by_provider", {})
        days = int(by_provider.get(provider, collection.get("pipeline_chunk_days_default", 14)))
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + timedelta(days=days))
            yield cursor, chunk_end
            cursor = chunk_end

    def run(self, instruments: list[dict[str, Any]], start: datetime, end: datetime, job_type: str = "historical_backfill") -> CollectionSummary:
        if start >= end:
            raise ValueError("start must be before end")
        instrument_ids = self.database.upsert_instruments(instruments) if self.database else {}
        succeeded: list[str] = []
        failed: dict[str, str] = {}
        total_rows = 0
        total_calls = 0

        for instrument in instruments:
            symbol = instrument["canonical_symbol"]
            provider = instrument["provider"]
            run_id = self.database.start_run(provider, job_type, start, end, self.config.hash(), {"canonical_symbol": symbol}) if self.database else None
            adapter = create_adapter(provider, self.config.settings, dry_run=self.dry_run)
            received = rejected = issue_count = inserted = updated = duplicates = 0
            raw_contract_rows_materialised = rolls_recorded = 0
            sessions_inserted = sessions_updated = 0
            previous_last_bar = pd.DataFrame()
            chunk_audit: list[dict[str, Any]] = []
            interval = "daily" if instrument.get("instrument_type") == "yield" else "5m"
            overlap = timedelta(days=14) if interval == "daily" else timedelta(
                minutes=int(self.config.settings.get("collection", {}).get("incremental_overlap_minutes", 30))
            )
            checkpoint = self.database.get_checkpoint(provider, symbol, interval) if self.database else None
            effective_start = checkpoint_resume_start(start, end, checkpoint, job_type, overlap)
            try:
                LOGGER.info("Collecting instrument", extra={"provider": provider, "instrument": symbol, "event": "collection_start", "requested_start": start.isoformat(), "effective_start": effective_start.isoformat()})
                for chunk_start, chunk_end in self._chunks(provider, effective_start, end):
                    batch = adapter.fetch(instrument, chunk_start, chunk_end)
                    chunk_received = len(batch.bars) + len(batch.yields)
                    received += chunk_received
                    chunk_validated = 0

                    if not batch.bars.empty:
                        clean, issues = preinsert_bar_checks(batch.bars, instrument)
                        chunk_rejected = len(batch.bars) - len(clean)
                        rejected += chunk_rejected
                        issue_count += len(issues)
                        chunk_validated += len(clean)
                        if self.database:
                            if not issues.empty:
                                issues["instrument_id"] = instrument_ids[symbol]
                                self.database.record_quality_issues(issues, run_id)
                            result = self.database.bulk_upsert_bars(clean, instrument_ids[symbol])
                            inserted += result["inserted"]
                            updated += result["updated"]
                            duplicates += result["duplicates"]

                            session_result = self.database.upsert_market_sessions(build_market_sessions(instrument, chunk_start, chunk_end))
                            sessions_inserted += session_result["inserted"]
                            sessions_updated += session_result["updated"]

                            if instrument.get("is_continuous") and clean["contract_code"].notna().any():
                                expiry_map = clean.groupby("contract_code", dropna=True)["expiry_date"].agg(
                                    lambda x: x.dropna().iloc[-1] if x.dropna().size else None
                                ).to_dict()
                                raw_defs = [
                                    _raw_contract_instrument(instrument, code, expiry_map.get(code))
                                    for code in sorted(clean["contract_code"].dropna().unique())
                                ]
                                raw_ids = self.database.upsert_instruments(raw_defs)
                                for raw_def in raw_defs:
                                    code = raw_def["contract_code"]
                                    raw = clean[clean["contract_code"] == code].copy()
                                    raw["is_continuous"] = False
                                    raw["continuous_method"] = None
                                    raw_result = self.database.bulk_upsert_bars(raw, raw_ids[raw_def["canonical_symbol"]])
                                    raw_contract_rows_materialised += raw_result["inserted"] + raw_result["updated"]

                                roll_input = clean
                                if not previous_last_bar.empty:
                                    roll_input = pd.concat([previous_last_bar, clean], ignore_index=True)
                                rolls_recorded += self.database.upsert_rolls(detect_rolls(roll_input, instrument_ids[symbol]))
                                previous_last_bar = clean.sort_values("bar_open_timestamp_utc").tail(1).copy()

                            if not clean.empty:
                                self.database.checkpoint(
                                    provider,
                                    symbol,
                                    "5m",
                                    pd.to_datetime(clean["bar_close_timestamp_utc"], utc=True).max().to_pydatetime(),
                                    {**batch.metadata, "chunk_start": chunk_start.isoformat(), "chunk_end": chunk_end.isoformat(),
                                     "job_type": job_type, "requested_start": start.isoformat(), "requested_end": end.isoformat()},
                                )
                        total_rows += len(clean)

                    if not batch.yields.empty:
                        chunk_validated += len(batch.yields)
                        if self.database:
                            result = self.database.bulk_upsert_yields(batch.yields, instrument_ids[symbol])
                            inserted += result["inserted"]
                            updated += result["updated"]
                            duplicates += result["duplicates"]
                            self.database.checkpoint(
                                provider,
                                symbol,
                                "daily",
                                pd.to_datetime(batch.yields["observation_timestamp_utc"], utc=True).max().to_pydatetime(),
                                {**batch.metadata, "chunk_start": chunk_start.isoformat(), "chunk_end": chunk_end.isoformat(),
                                 "job_type": job_type, "requested_start": start.isoformat(), "requested_end": end.isoformat()},
                            )
                        total_rows += len(batch.yields)

                    chunk_audit.append({
                        "start": chunk_start.isoformat(),
                        "end": chunk_end.isoformat(),
                        "rows_received": chunk_received,
                        "rows_validated": chunk_validated,
                    })

                if received == 0 and instrument.get("required", False) and not self.dry_run:
                    raise RuntimeError("Required instrument returned zero rows across the complete requested range")
                total_calls += adapter.api_calls

                if self.database and run_id:
                    self.database.finish_run(
                        run_id,
                        status="succeeded",
                        rows_received=received,
                        rows_validated=received-rejected,
                        rows_inserted=inserted,
                        rows_updated=updated,
                        duplicates=duplicates,
                        rejected_rows=rejected,
                        api_calls=adapter.api_calls,
                        retries=adapter.retries,
                        metadata={
                            "chunks": chunk_audit,
                            "preinsert_issue_count": issue_count,
                            "raw_contract_rows_materialised": raw_contract_rows_materialised,
                            "rolls_recorded": rolls_recorded,
                            "market_sessions_inserted": sessions_inserted,
                            "market_sessions_updated": sessions_updated,
                        },
                    )
                succeeded.append(symbol)
                LOGGER.info("Instrument collected", extra={"provider": provider, "instrument": symbol, "rows": received-rejected, "api_calls": adapter.api_calls, "event": "collection_success"})
            except Exception as exc:
                total_calls += adapter.api_calls
                failed[symbol] = str(exc)
                if self.database and run_id:
                    self.database.finish_run(
                        run_id,
                        status="failed",
                        rows_received=received,
                        rows_validated=received-rejected,
                        rows_inserted=inserted,
                        rows_updated=updated,
                        duplicates=duplicates,
                        rejected_rows=rejected,
                        api_calls=adapter.api_calls,
                        retries=adapter.retries,
                        error={"type": type(exc).__name__, "message": str(exc)},
                        metadata={"completed_chunks": chunk_audit, "last_successful_checkpoint_may_be_reused": True},
                    )
                LOGGER.exception("Instrument collection failed", extra={"provider": provider, "instrument": symbol, "event": "collection_failure"})

        return CollectionSummary(succeeded=succeeded, failed=failed, rows=total_rows, api_calls=total_calls)
