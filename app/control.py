from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from .config import load_config
from .db import Database

_OUTPUT_LIMIT = 80_000


def derive_supabase_url(database_url: str | None = None) -> str | None:
    """Derive https://<project-ref>.supabase.co from a Supabase pooler URL."""
    raw = database_url or os.getenv("SUPABASE_DB_URL")
    if not raw:
        return None
    parsed = urlparse(raw)
    username = unquote(parsed.username or "")
    if username.startswith("postgres."):
        project_ref = username.split(".", 1)[1]
        if project_ref:
            return f"https://{project_ref}.supabase.co"
    return None


def runtime_environment(db: Database) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in get_settings(db).items():
        env[key] = value
    env.setdefault("SUPABASE_STORAGE_UPLOAD", "true")
    env.setdefault("SUPABASE_STORAGE_BUCKET", "cross-asset-research-exports")
    if not env.get("SUPABASE_URL"):
        derived = derive_supabase_url(env.get("SUPABASE_DB_URL"))
        if derived:
            env["SUPABASE_URL"] = derived
    if not env.get("UNTOUCHED_ARCHIVE_PASSWORD") and env.get("DASHBOARD_PASSWORD"):
        env["UNTOUCHED_ARCHIVE_PASSWORD"] = env["DASHBOARD_PASSWORD"]
    return env


def get_settings(db: Database) -> dict[str, str]:
    try:
        frame = db.read_dataframe("select setting_key, setting_value from system_settings")
    except Exception:
        return {}
    if frame.empty:
        return {}
    return {str(row.setting_key): str(row.setting_value) for row in frame.itertuples(index=False)}


def set_setting(db: Database, key: str, value: str) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into system_settings(setting_key, setting_value) values (%s, %s)
                   on conflict(setting_key) do update set setting_value=excluded.setting_value, updated_at=now()""",
                (key, value),
            )
        conn.commit()


def _core_schema_ready(db: Database) -> bool:
    """Check core tables directly, without depending on a particular db.py version."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select
                       to_regclass('public.instruments'),
                       to_regclass('public.market_bars'),
                       to_regclass('public.system_settings'),
                       to_regclass('public.control_jobs'),
                       to_regclass('public.ingestion_runs'),
                       to_regclass('public.export_runs')"""
            )
            return all(value is not None for value in cur.fetchone())


def bootstrap() -> dict[str, Any]:
    """Create schema once, then perform only lightweight startup reads.

    This implementation deliberately does not call ``Database.schema_ready``.
    That keeps the dashboard compatible with repositories upgraded by small
    browser-uploaded patches where ``control.py`` and ``db.py`` may briefly be
    on different versions.
    """
    db = Database(os.getenv("SUPABASE_DB_URL"))
    config = load_config()
    if not _core_schema_ready(db):
        db.apply_migration(config.root / "sql" / "001_init.sql")

    settings = get_settings(db)
    if not all(settings.get(k) for k in (
        "RESEARCH_DATASET_START_UTC",
        "UNTOUCHED_START_UTC",
        "RESEARCH_DATASET_END_UTC",
    )):
        end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=90)
        untouched = end - timedelta(days=30)
        set_setting(db, "RESEARCH_DATASET_START_UTC", start.isoformat())
        set_setting(db, "UNTOUCHED_START_UTC", untouched.isoformat())
        set_setting(db, "RESEARCH_DATASET_END_UTC", end.isoformat())
        settings = get_settings(db)

    return {"database": db.ping(), "settings": settings}


def create_job(db: Database, job_type: str) -> str:
    allowed = {"full_setup", "resume_backfill", "quality_export", "incremental", "preflight", "round2", "round2_export_only"}
    if job_type not in allowed:
        raise ValueError(f"Unsupported job type: {job_type}")
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select job_id from control_jobs where status in ('queued','running') order by created_at desc limit 1")
            row = cur.fetchone()
            if row:
                return str(row[0])
            cur.execute("insert into control_jobs(job_type,status) values (%s,'queued') returning job_id", (job_type,))
            job_id = str(cur.fetchone()[0])
        conn.commit()
    return job_id


def latest_jobs(db: Database, limit: int = 12) -> list[dict[str, Any]]:
    frame = db.read_dataframe(
        """select job_id,job_type,status,created_at,started_at,ended_at,current_step,
                  progress_percent,output_text,error_text,metadata_json
           from control_jobs order by created_at desc limit %s""",
        (limit,),
    )
    if frame.empty:
        return []
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def _update_job(db: Database, job_id: str, **values: Any) -> None:
    if not values:
        return
    allowed = {"status", "started_at", "ended_at", "current_step", "progress_percent", "output_text", "error_text", "metadata_json"}
    clean = {k: v for k, v in values.items() if k in allowed}
    sets = []
    params: list[Any] = []
    for key, value in clean.items():
        sets.append(f"{key}=%s")
        params.append(json.dumps(value, default=str) if key == "metadata_json" and not isinstance(value, str) else value)
    params.append(job_id)
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"update control_jobs set {','.join(sets)} where job_id=%s", params)
        conn.commit()


def _extract_last_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidate: dict[str, Any] | None = None
    candidate_end = -1
    candidate_length = -1
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            absolute_end = index + consumed
            # Prefer the object that ends latest in the output; on a tie prefer the outer/larger object.
            if absolute_end > candidate_end or (absolute_end == candidate_end and consumed > candidate_length):
                candidate = value
                candidate_end = absolute_end
                candidate_length = consumed
    return candidate


def _run_command(db: Database, job_id: str, step: str, progress: int, args: list[str], output: str) -> tuple[str, dict[str, Any] | None]:
    _update_job(db, job_id, current_step=step, progress_percent=progress, output_text=output[-_OUTPUT_LIMIT:])
    env = runtime_environment(db)
    command = [sys.executable, "-m", "app", *args]
    process = subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines = [output, f"\n\n=== {step} ===\n$ {' '.join(command)}\n"]
    last_write = 0.0
    assert process.stdout is not None
    for line in process.stdout:
        lines.append(line)
        combined = "".join(lines)[-_OUTPUT_LIMIT:]
        now = time.monotonic()
        if now - last_write > 1.0:
            _update_job(db, job_id, output_text=combined)
            last_write = now
    return_code = process.wait()
    combined = "".join(lines)[-_OUTPUT_LIMIT:]
    _update_job(db, job_id, output_text=combined)
    payload = _extract_last_json(combined)
    if return_code != 0:
        raise RuntimeError(f"{step} failed. The last output is shown below.\n\n{combined[-12000:]}")
    return combined, payload


@dataclass(frozen=True)
class Step:
    name: str
    progress: int
    args: list[str]


class JobRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, job_id: str) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._execute, args=(job_id,), daemon=True)
            self._thread.start()

    def _steps(self, job_type: str) -> list[Step]:
        if job_type == "full_setup":
            return [
                Step("Checking every connection and instrument", 10, ["preflight"]),
                Step("Testing public Bitcoin data", 15, ["smoke-test"]),
                Step("Running a small two-day SPY test", 20, ["backfill", "--symbol", "SPY_SP500_PROXY", "--history-days", "2"]),
                Step("Collecting the complete 90-day dataset", 65, ["backfill"]),
                Step("Running discovery-period quality checks", 85, ["quality"]),
                Step("Creating and securely uploading the archives", 95, ["export"]),
            ]
        if job_type == "resume_backfill":
            return [
                Step("Resuming the complete historical collection", 55, ["backfill"]),
                Step("Running discovery-period quality checks", 85, ["quality"]),
                Step("Creating and securely uploading the archives", 95, ["export"]),
            ]
        if job_type == "quality_export":
            return [
                Step("Running discovery-period quality checks", 55, ["quality"]),
                Step("Creating and securely uploading the archives", 90, ["export"]),
            ]
        if job_type == "incremental":
            return [Step("Collecting the latest available observations", 50, ["incremental", "--history-days", "2"])]
        if job_type == "preflight":
            return [Step("Checking every connection and instrument", 50, ["preflight"])]
        raise ValueError(job_type)

    def _execute(self, job_id: str) -> None:
        db = Database(os.getenv("SUPABASE_DB_URL"))
        frame = db.read_dataframe("select job_type from control_jobs where job_id=%s", (job_id,))
        if frame.empty:
            return
        job_type = str(frame.iloc[0]["job_type"])
        output = ""
        metadata: dict[str, Any] = {}
        _update_job(
            db,
            job_id,
            status="running",
            started_at=datetime.now(timezone.utc),
            current_step="Starting",
            progress_percent=1,
        )
        try:
            for step in self._steps(job_type):
                output, payload = _run_command(db, job_id, step.name, step.progress, step.args, output)
                if payload and step.args[0] == "export":
                    metadata["export"] = payload
            _update_job(
                db,
                job_id,
                status="succeeded",
                ended_at=datetime.now(timezone.utc),
                current_step="Complete",
                progress_percent=100,
                output_text=output[-_OUTPUT_LIMIT:],
                metadata_json=metadata,
            )
        except Exception as exc:
            _update_job(
                db,
                job_id,
                status="failed",
                ended_at=datetime.now(timezone.utc),
                current_step="Stopped",
                error_text=str(exc),
                output_text=output[-_OUTPUT_LIMIT:],
            )


RUNNER = JobRunner()


def _latest_object_containing(db: Database, marker: str) -> str | None:
    jobs = latest_jobs(db, limit=50)
    for job in jobs:
        metadata = job.get("metadata_json") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                continue
        for section in ("export", "round2_export"):
            uploads = ((metadata.get(section) or {}).get("storage_uploads") or [])
            for upload in uploads:
                object_name = str(upload.get("object", ""))
                if marker in object_name:
                    return object_name
    return None


def latest_discovery_object(db: Database) -> str | None:
    return _latest_object_containing(db, "cross_asset_discovery_export_")


def latest_round2_object(db: Database) -> str | None:
    return _latest_object_containing(db, "cross_asset_ROUND2_historical_corroboration_")


def _signed_url_for_object(db: Database, object_name: str | None, expires_seconds: int = 3600) -> str | None:
    if not object_name:
        return None
    env = runtime_environment(db)
    base = env.get("SUPABASE_URL", "").rstrip("/")
    key = env.get("SUPABASE_SERVICE_ROLE_KEY")
    bucket = env.get("SUPABASE_STORAGE_BUCKET", "cross-asset-research-exports")
    if not base or not key:
        return None
    encoded = "/".join(requests.utils.quote(part, safe="") for part in object_name.split("/"))
    response = requests.post(
        f"{base}/storage/v1/object/sign/{requests.utils.quote(bucket, safe='')}/{encoded}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        json={"expiresIn": expires_seconds},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    path = payload.get("signedURL") or payload.get("signedUrl")
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"{base}/storage/v1{path if path.startswith('/') else '/' + path}"


def signed_discovery_url(db: Database, expires_seconds: int = 3600) -> str | None:
    return _signed_url_for_object(db, latest_discovery_object(db), expires_seconds)


def signed_round2_url(db: Database, expires_seconds: int = 3600) -> str | None:
    return _signed_url_for_object(db, latest_round2_object(db), expires_seconds)
