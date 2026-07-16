from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import psycopg

POLL_SECONDS = 3
OUTPUT_LIMIT = 80_000
STOP = False
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Step:
    name: str
    progress: int
    args: list[str]


def _connect():
    url = os.getenv("SUPABASE_DB_URL")
    if not url:
        raise RuntimeError("SUPABASE_DB_URL is not set")
    return psycopg.connect(url, prepare_threshold=None)


def _derive_supabase_url(database_url: str) -> str | None:
    parsed = urlparse(database_url)
    username = unquote(parsed.username or "")
    if username.startswith("postgres."):
        ref = username.split(".", 1)[1]
        if ref:
            return f"https://{ref}.supabase.co"
    return None


def _settings() -> dict[str, str]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select setting_key, setting_value from system_settings")
            return {str(k): str(v) for k, v in cur.fetchall()}


def _runtime_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(_settings())
    env.setdefault("SUPABASE_STORAGE_UPLOAD", "true")
    env.setdefault("SUPABASE_STORAGE_BUCKET", "cross-asset-research-exports")
    if not env.get("SUPABASE_URL") and env.get("SUPABASE_DB_URL"):
        derived = _derive_supabase_url(env["SUPABASE_DB_URL"])
        if derived:
            env["SUPABASE_URL"] = derived
    if not env.get("UNTOUCHED_ARCHIVE_PASSWORD") and env.get("DASHBOARD_PASSWORD"):
        env["UNTOUCHED_ARCHIVE_PASSWORD"] = env["DASHBOARD_PASSWORD"]
    return env


def _steps(job_type: str) -> list[Step]:
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
    raise ValueError(f"Unsupported job type: {job_type}")


def _update(job_id: str, **values: Any) -> None:
    allowed = {"status", "started_at", "ended_at", "current_step", "progress_percent", "output_text", "error_text", "metadata_json"}
    clean = {k: v for k, v in values.items() if k in allowed}
    if not clean:
        return
    sets: list[str] = []
    params: list[Any] = []
    for key, value in clean.items():
        sets.append(f"{key}=%s")
        if key == "metadata_json" and not isinstance(value, str):
            value = json.dumps(value, default=str)
        params.append(value)
    params.append(job_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"update control_jobs set {','.join(sets)} where job_id=%s", params)
        conn.commit()


def _claim() -> tuple[str, str] | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """select job_id, job_type from control_jobs
                   where status='queued' order by created_at
                   for update skip locked limit 1"""
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            job_id, job_type = str(row[0]), str(row[1])
            cur.execute(
                """update control_jobs set status='running', started_at=coalesce(started_at,now()),
                   ended_at=null, current_step='Starting', progress_percent=1, error_text=null
                   where job_id=%s""",
                (job_id,),
            )
        conn.commit()
    return job_id, job_type


def _recover_interrupted() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """update control_jobs set status='queued', current_step='Resuming after worker restart',
                   error_text=coalesce(error_text,'') || E'\\nWorker restarted; the job was safely requeued.'
                   where status='running'"""
            )
        conn.commit()


def _extract_last_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidate = None
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
            if absolute_end > candidate_end or (absolute_end == candidate_end and consumed > candidate_length):
                candidate = value
                candidate_end = absolute_end
                candidate_length = consumed
    return candidate


def _run_step(job_id: str, step: Step, previous: str) -> tuple[str, dict[str, Any] | None]:
    _update(job_id, current_step=step.name, progress_percent=step.progress, output_text=previous[-OUTPUT_LIMIT:])
    command = [sys.executable, "-m", "app", *step.args]
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=_runtime_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    parts = [previous, f"\n\n=== {step.name} ===\n$ {' '.join(command)}\n"]
    last_write = 0.0
    assert process.stdout is not None
    for line in process.stdout:
        parts.append(line)
        now = time.monotonic()
        if now - last_write >= 1.0:
            _update(job_id, output_text="".join(parts)[-OUTPUT_LIMIT:])
            last_write = now
        if STOP and process.poll() is None:
            process.terminate()
    code = process.wait()
    combined = "".join(parts)[-OUTPUT_LIMIT:]
    _update(job_id, output_text=combined)
    payload = _extract_last_json(combined)
    if code != 0:
        raise RuntimeError(f"{step.name} failed. The last output is shown below.\n\n{combined[-12000:]}")
    return combined, payload


def _execute(job_id: str, job_type: str) -> None:
    output = ""
    metadata: dict[str, Any] = {}
    try:
        for step in _steps(job_type):
            output, payload = _run_step(job_id, step, output)
            if payload and step.args[0] == "export":
                metadata["export"] = payload
        _update(
            job_id,
            status="succeeded",
            ended_at=datetime.now(timezone.utc),
            current_step="Complete",
            progress_percent=100,
            output_text=output[-OUTPUT_LIMIT:],
            metadata_json=metadata,
        )
    except Exception as exc:
        _update(
            job_id,
            status="failed",
            ended_at=datetime.now(timezone.utc),
            current_step="Stopped",
            error_text=str(exc),
            output_text=output[-OUTPUT_LIMIT:],
        )


def _stop(*_: Any) -> None:
    global STOP
    STOP = True


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    # Wait for the dashboard's initial migration/bootstrap on first deployment.
    while not STOP:
        try:
            _recover_interrupted()
            break
        except Exception as exc:
            print(f"Worker waiting for database initialisation: {exc}", flush=True)
            time.sleep(5)

    print("Cross-asset background worker is ready.", flush=True)
    while not STOP:
        try:
            claimed = _claim()
            if claimed:
                _execute(*claimed)
            else:
                time.sleep(POLL_SECONDS)
        except Exception as exc:
            print(f"Worker loop error: {exc}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
