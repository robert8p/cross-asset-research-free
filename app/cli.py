from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .adapters import create_adapter
from .config import default_date_range, load_config, parse_utc, select_instruments, validate_environment
from .db import Database
from .exporter import Exporter
from .logging_utils import configure_logging
from .pipeline import Collector
from .quality import evaluate_bars, evaluate_yields
from .round2 import backfill as round2_backfill, create_export as round2_create_export
from .storage import SupabaseStorageUploader


def _date_range(args, config):
    if args.start and args.end:
        return parse_utc(args.start), parse_utc(args.end)
    configured_start = os.getenv("RESEARCH_DATASET_START_UTC")
    configured_end = os.getenv("RESEARCH_DATASET_END_UTC")
    if configured_start and configured_end and getattr(args, "command", "") == "backfill":
        return parse_utc(configured_start), parse_utc(configured_end)
    return default_date_range(config, getattr(args, "history_days", None))


def cmd_plan_dates(args) -> int:
    config = load_config()
    days = int(args.history_days or config.settings["project"].get("default_history_days", 90))
    untouched_days = int(args.untouched_days or config.settings["project"].get("untouched_test_days", 30))
    if untouched_days <= 0 or untouched_days >= days:
        raise ValueError("untouched-days must be greater than zero and less than history-days")
    end = parse_utc(args.end) if args.end else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    untouched = end - timedelta(days=untouched_days)
    print(json.dumps({
        "RESEARCH_DATASET_START_UTC": start.isoformat(),
        "UNTOUCHED_START_UTC": untouched.isoformat(),
        "RESEARCH_DATASET_END_UTC": end.isoformat(),
        "discovery_calendar_days": days - untouched_days,
        "untouched_calendar_days": untouched_days,
        "instruction": "Copy these three values into the Render cross-asset-research-settings environment group before backfill/export.",
    }, indent=2))
    return 0


def _database() -> Database:
    return Database(os.getenv("SUPABASE_DB_URL"))


def cmd_migrate(args) -> int:
    config = load_config()
    db = _database()
    db.apply_migration(config.root / "sql" / "001_init.sql")
    print(json.dumps({"status": "succeeded", "migration": "sql/001_init.sql", "database": db.ping()}, indent=2))
    return 0


def cmd_preflight(args) -> int:
    config = load_config()
    instruments = select_instruments(config, args.symbol, args.provider)
    start, end = _date_range(args, config)
    missing = [] if args.dry_run else validate_environment(instruments, require_database=not args.no_database)
    results = []
    for instrument in instruments:
        try:
            adapter = create_adapter(instrument["provider"], config.settings, dry_run=args.dry_run)
            results.append({"canonical_symbol": instrument["canonical_symbol"], **adapter.preflight(instrument, start, end)})
        except Exception as exc:
            results.append({"canonical_symbol": instrument["canonical_symbol"], "ok": False, "error": str(exc)})
    db_status = None
    if not args.no_database and not missing:
        try:
            db_status = _database().ping()
        except Exception as exc:
            db_status = {"ok": False, "error": str(exc)}
    required_by_symbol = {x["canonical_symbol"]: bool(x.get("required", False)) for x in instruments}
    required_failures = [
        item.get("canonical_symbol") for item in results
        if required_by_symbol.get(item.get("canonical_symbol"), False) and not item.get("ok")
    ]
    optional_failures = [
        item.get("canonical_symbol") for item in results
        if not required_by_symbol.get(item.get("canonical_symbol"), False) and not item.get("ok")
    ]
    output = {
        "environment_missing": missing,
        "database": db_status,
        "required_failures": required_failures,
        "optional_failures": optional_failures,
        "providers": results,
    }
    print(json.dumps(output, indent=2, default=str))
    return 1 if missing or required_failures or (db_status and not db_status.get("ok")) else 0


def cmd_collect(args, incremental: bool = False) -> int:
    config = load_config()
    instruments = select_instruments(config, args.symbol, args.provider)
    missing = validate_environment(instruments, require_database=not args.dry_run)
    if missing and not args.dry_run:
        print(json.dumps({"status": "failed", "missing_environment": missing}, indent=2), file=sys.stderr)
        return 2
    start, end = _date_range(args, config)
    if incremental and not args.start:
        start = end - timedelta(days=int(args.history_days or 2))
    db = None if args.dry_run else _database()
    summary = Collector(config, db, dry_run=args.dry_run).run(instruments, start, end, "incremental" if incremental else "historical_backfill")
    print(json.dumps(summary.__dict__, indent=2, default=str))
    required_failures = [x["canonical_symbol"] for x in instruments if x.get("required") and x["canonical_symbol"] in summary.failed]
    return 1 if required_failures else 0


def cmd_quality(args) -> int:
    config = load_config()
    discovery_end_text = args.discovery_end or os.getenv("UNTOUCHED_START_UTC")
    if not discovery_end_text:
        print("Refusing to run: provide --discovery-end or UNTOUCHED_START_UTC so the untouched period cannot be queried.", file=sys.stderr)
        return 2
    end = parse_utc(discovery_end_text)
    start = parse_utc(args.start) if args.start else end - timedelta(days=int(args.history_days or 60))
    db = _database()
    instruments = db.read_dataframe("select * from instruments")
    bars = db.read_dataframe("select * from market_bars where bar_open_timestamp_utc >= %s and bar_open_timestamp_utc < %s order by instrument_id,bar_open_timestamp_utc", (start, end))
    yields = db.read_dataframe("select * from yield_observations where observation_timestamp_utc >= %s and observation_timestamp_utc < %s order by instrument_id,observation_timestamp_utc", (start, end))
    bar_result = evaluate_bars(bars, instruments, config.settings, coverage_start=start, coverage_end_exclusive=end)
    yield_issues = evaluate_yields(yields)
    all_issues = pd.concat([bar_result.issues, yield_issues], ignore_index=True, sort=False)
    recorded = db.record_quality_issues(all_issues)
    print(json.dumps({"status": "succeeded", "discovery_start": start.isoformat(), "discovery_end_exclusive": end.isoformat(), "checked_bar_rows": bar_result.checked_rows, "issues_recorded": recorded, "coverage": bar_result.coverage.to_dict("records")}, indent=2, default=str))
    return 0


def cmd_export(args) -> int:
    config = load_config()
    explicit = parse_utc(args.untouched_start) if args.untouched_start else None
    result = Exporter(config, _database()).create(explicit_untouched_start=explicit, include_full_archive=not args.no_full_archive)
    uploads = []
    if os.getenv("SUPABASE_STORAGE_UPLOAD", "false").lower() in {"1", "true", "yes"}:
        uploader = SupabaseStorageUploader()
        for path in (result.discovery_archive, result.untouched_archive, result.full_archive):
            if path:
                print(f"UPLOAD: sending {path.name} ({path.stat().st_size:,} bytes) to private Supabase Storage", flush=True)
                uploaded = uploader.upload(path)
                uploads.append(uploaded)
                print(f"UPLOAD: completed {path.name}", flush=True)
    print(json.dumps({
        "status": "succeeded", "discovery_archive": str(result.discovery_archive),
        "untouched_archive": str(result.untouched_archive),
        "full_archive": str(result.full_archive) if result.full_archive else None,
        "storage_uploads": uploads,
        "split": result.split.__dict__,
    }, indent=2, default=str))
    return 0


def cmd_status(args) -> int:
    db = _database()
    # Status deliberately reports run/checkpoint state, not untouched-period market row counts.
    runs = db.read_dataframe("select source,job_type,status,started_at,ended_at,rows_received,rows_inserted,rejected_rows,api_calls,error_details from ingestion_runs order by started_at desc limit 50")
    checkpoints = db.read_dataframe("select provider,canonical_symbol,interval,last_complete_timestamp_utc,updated_at from collector_checkpoints order by canonical_symbol")
    print(json.dumps({"database": db.ping(), "recent_runs": runs.to_dict("records"), "checkpoints": checkpoints.to_dict("records")}, indent=2, default=str))
    return 0


def cmd_smoke(args) -> int:
    config = load_config()
    instrument = next(x for x in config.enabled_instruments if x["canonical_symbol"] == "BTC_USD_SPOT")
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(hours=2)
    adapter = create_adapter("coinbase", config.settings)
    batch = adapter.fetch(instrument, start, end)
    # This is a public-source connectivity smoke test, not a research export.
    print(json.dumps({"status": "succeeded" if not batch.bars.empty else "failed", "provider": "coinbase", "symbol": instrument["provider_symbol"], "rows_received": len(batch.bars), "first_timestamp": str(batch.bars["bar_open_timestamp_utc"].min()) if not batch.bars.empty else None, "last_timestamp": str(batch.bars["bar_open_timestamp_utc"].max()) if not batch.bars.empty else None}, indent=2))
    return 0 if not batch.bars.empty else 1



def cmd_round2_backfill(args) -> int:
    result = round2_backfill(_database())
    print(json.dumps(result, indent=2, default=str))
    return 1 if result.get("failed") else 0


def cmd_round2_export(args) -> int:
    result = round2_create_export(_database())
    print(json.dumps(result, indent=2, default=str))
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cross-asset intraday market-data research collector")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan-dates")
    plan.add_argument("--history-days", type=int, default=90)
    plan.add_argument("--untouched-days", type=int, default=30)
    plan.add_argument("--end", help="Exclusive UTC dataset end; defaults to today 00:00 UTC")

    sub.add_parser("migrate")
    preflight = sub.add_parser("preflight")
    for p in [preflight]:
        p.add_argument("--symbol", action="append"); p.add_argument("--provider", action="append")
        p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--history-days", type=int)
    preflight.add_argument("--dry-run", action="store_true"); preflight.add_argument("--no-database", action="store_true")

    for name in ("backfill", "incremental"):
        p = sub.add_parser(name)
        p.add_argument("--symbol", action="append"); p.add_argument("--provider", action="append")
        p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--history-days", type=int)
        p.add_argument("--dry-run", action="store_true")

    quality = sub.add_parser("quality")
    quality.add_argument("--start"); quality.add_argument("--history-days", type=int, default=60)
    quality.add_argument("--discovery-end", help="Exclusive cutoff; normally the untouched-test start")

    export = sub.add_parser("export")
    export.add_argument("--untouched-start", help="Explicit UTC cutoff; otherwise calculated from settings")
    export.add_argument("--no-full-archive", action="store_true")

    sub.add_parser("round2-backfill")
    sub.add_parser("round2-export")
    sub.add_parser("status")
    sub.add_parser("smoke-test")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    commands = {
        "plan-dates": cmd_plan_dates, "migrate": cmd_migrate, "preflight": cmd_preflight,
        "backfill": lambda a: cmd_collect(a, False), "incremental": lambda a: cmd_collect(a, True),
        "quality": cmd_quality, "export": cmd_export,
        "round2-backfill": cmd_round2_backfill, "round2-export": cmd_round2_export,
        "status": cmd_status, "smoke-test": cmd_smoke,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
