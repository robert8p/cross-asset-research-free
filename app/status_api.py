from __future__ import annotations

import html
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .control import bootstrap, create_job, get_settings, latest_discovery_object, latest_jobs, signed_discovery_url
from .db import Database

security = HTTPBasic()
BOOTSTRAP_ERROR: str | None = None


def _auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    expected = os.getenv("DASHBOARD_PASSWORD", "")
    username_ok = secrets.compare_digest(credentials.username.encode(), b"admin")
    password_ok = bool(expected) and secrets.compare_digest(credentials.password.encode(), expected.encode())
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@asynccontextmanager
async def lifespan(_: FastAPI):
    global BOOTSTRAP_ERROR
    try:
        bootstrap()
        BOOTSTRAP_ERROR = None
    except Exception as exc:  # Keep the dashboard alive so the user can see a useful error.
        BOOTSTRAP_ERROR = str(exc)
    yield


app = FastAPI(title="Cross-Asset Research — No-Code Dashboard", version="1.3.0-worker", lifespan=lifespan)


@app.get("/health")
def health():
    """Render liveness check: report whether the web process is running.

    Database readiness is intentionally reported separately. A missing or invalid
    database setting must not trap the deployment in a health-check loop, because
    the dashboard is the place where a non-technical user can see and correct it.
    """
    return {
        "ok": True,
        "service": "cross-asset-research-dashboard",
        "ready": BOOTSTRAP_ERROR is None,
    }


@app.get("/ready")
def ready():
    """Detailed readiness check for diagnostics; not used as Render's liveness check."""
    if BOOTSTRAP_ERROR:
        return JSONResponse(status_code=503, content={"ok": False, "error": BOOTSTRAP_ERROR})
    try:
        return Database(os.getenv("SUPABASE_DB_URL")).ping()
    except Exception as exc:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(exc)})


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


def _status_badge(value: str) -> str:
    css = {
        "succeeded": "good",
        "running": "running",
        "queued": "running",
        "failed": "bad",
    }.get(value, "neutral")
    return f'<span class="badge {css}">{html.escape(value.replace("_", " ").title())}</span>'


def _job_card(job: dict[str, Any]) -> str:
    output = job.get("error_text") or job.get("output_text") or ""
    tail = str(output)[-6000:]
    details = ""
    if tail:
        details = f"<details><summary>Technical details</summary><pre>{html.escape(tail)}</pre></details>"
    return f"""
      <section class="job-card">
        <div class="job-head">
          <div><strong>{html.escape(str(job.get('job_type', '')).replace('_', ' ').title())}</strong><br>
          <span class="muted">{html.escape(_fmt(job.get('created_at')))}</span></div>
          {_status_badge(str(job.get('status', 'unknown')))}
        </div>
        <div class="step">{html.escape(str(job.get('current_step') or 'Waiting'))}</div>
        <div class="progress"><div style="width:{int(job.get('progress_percent') or 0)}%"></div></div>
        {details}
      </section>
    """


def _recent_runs(db: Database) -> str:
    try:
        frame = db.read_dataframe(
            """select source,job_type,status,started_at,ended_at,rows_received,rows_inserted,rejected_rows
               from ingestion_runs order by started_at desc limit 15"""
        )
    except Exception:
        return "<p class='muted'>No collection runs yet.</p>"
    if frame.empty:
        return "<p class='muted'>No collection runs yet.</p>"
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.source))}</td>"
            f"<td>{html.escape(str(row.job_type).replace('_',' '))}</td>"
            f"<td>{_status_badge(str(row.status))}</td>"
            f"<td>{int(row.rows_received or 0):,}</td>"
            f"<td>{int(row.rows_inserted or 0):,}</td>"
            f"<td>{int(row.rejected_rows or 0):,}</td>"
            "</tr>"
        )
    return """
    <div class="table-wrap"><table>
      <thead><tr><th>Source</th><th>Run</th><th>Status</th><th>Received</th><th>Inserted</th><th>Rejected</th></tr></thead>
      <tbody>%s</tbody>
    </table></div>
    """ % "".join(rows)


@app.get("/", response_class=HTMLResponse)
def dashboard(_: str = Depends(_auth)):
    if BOOTSTRAP_ERROR:
        body = f"""
        <h1>Setup needs one correction</h1>
        <div class="alert bad-panel"><strong>The service could not connect or initialise.</strong><br>{html.escape(BOOTSTRAP_ERROR)}</div>
        <p>Check the six values entered during Render deployment, then redeploy.</p>
        """
        return HTMLResponse(_page(body, refresh=False), status_code=503)

    db = Database(os.getenv("SUPABASE_DB_URL"))
    settings = get_settings(db)
    jobs = latest_jobs(db)
    active = any(j.get("status") in {"queued", "running"} for j in jobs)
    latest = jobs[0] if jobs else None
    discovery_ready = bool(latest_discovery_object(db))

    action_panel = ""
    if active:
        action_panel = """
        <div class="alert info"><strong>The system is working.</strong> You can close this page and return later. The database checkpoints protect completed work.</div>
        """
    elif discovery_ready:
        action_panel = """
        <div class="actions">
          <a class="button primary" href="/download/discovery">Download discovery package</a>
          <form method="post" action="/run/incremental"><button class="button secondary">Collect latest data</button></form>
          <form method="post" action="/run/quality-export"><button class="button secondary">Recreate export</button></form>
        </div>
        <div class="alert warning"><strong>Do not open the untouched-test archive.</strong> The dashboard intentionally provides no download button for it.</div>
        """
    elif latest and latest.get("status") == "failed":
        action_panel = """
        <div class="actions">
          <form method="post" action="/run/resume" onsubmit="return confirm('Resume from the saved checkpoints?');"><button class="button primary">Resume safely</button></form>
          <form method="post" action="/run/preflight"><button class="button secondary">Check connections only</button></form>
        </div>
        """
    else:
        action_panel = """
        <div class="actions">
          <form method="post" action="/run/full-setup" onsubmit="return confirm('Start the complete 90-day collection and export?');">
            <button class="button primary big">Run complete setup</button>
          </form>
          <form method="post" action="/run/preflight"><button class="button secondary">Check connections only</button></form>
        </div>
        <p class="muted">The complete setup automatically creates the database tables, checks every source, runs a small test, collects 90 days, performs discovery-only quality checks and uploads the export.</p>
        """

    job_html = "".join(_job_card(j) for j in jobs[:5]) or "<p class='muted'>No jobs have run yet.</p>"
    body = f"""
      <header>
        <div>
          <div class="eyebrow">NO-CODE FREE-DATA EDITION</div>
          <h1>Cross-Asset Research Collector</h1>
          <p class="lead">One button runs the complete integrity-controlled workflow.</p>
        </div>
        <div class="status-dot {'pulse' if active else ''}">{'Working' if active else 'Ready'}</div>
      </header>

      <section class="panel">
        <h2>Your research window</h2>
        <div class="metrics">
          <div><span>Starts</span><strong>{html.escape(settings.get('RESEARCH_DATASET_START_UTC','—')[:10])}</strong></div>
          <div><span>Untouched test starts</span><strong>{html.escape(settings.get('UNTOUCHED_START_UTC','—')[:10])}</strong></div>
          <div><span>Ends</span><strong>{html.escape(settings.get('RESEARCH_DATASET_END_UTC','—')[:10])}</strong></div>
        </div>
      </section>

      <section class="panel">
        <h2>Action</h2>
        {action_panel}
      </section>

      <section class="panel">
        <h2>Workflow status</h2>
        {job_html}
      </section>

      <section class="panel">
        <h2>Recent source activity</h2>
        {_recent_runs(db)}
      </section>

      <footer>Username: <code>admin</code>. This dashboard never profiles or displays untouched-period market behaviour.</footer>
    """
    return HTMLResponse(_page(body, refresh=active))


def _page(body: str, refresh: bool = False) -> str:
    refresh_tag = '<meta http-equiv="refresh" content="10">' if refresh else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{refresh_tag}<title>Cross-Asset Research Collector</title>
<style>
:root{{--bg:#f4f6f8;--panel:#fff;--text:#16202a;--muted:#607080;--line:#dce2e8;--accent:#1265e5;--good:#137a4b;--bad:#b42318;--warning:#9a6700}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;color:var(--text)}}
main{{max-width:1050px;margin:0 auto;padding:48px 22px 80px}} header{{display:flex;justify-content:space-between;align-items:flex-start;gap:30px;margin-bottom:28px}}
h1{{font-size:38px;letter-spacing:-.04em;margin:5px 0 8px}} h2{{font-size:20px;margin:0 0 18px}} .lead{{color:var(--muted);font-size:18px;margin:0}} .eyebrow{{font-size:12px;font-weight:800;letter-spacing:.13em;color:var(--accent)}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:24px;margin:16px 0;box-shadow:0 8px 28px rgba(20,35,50,.04)}}
.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}} .metrics div{{background:#f7f9fb;border-radius:12px;padding:16px}} .metrics span{{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}} .metrics strong{{font-size:18px}}
.actions{{display:flex;flex-wrap:wrap;gap:12px;align-items:center}} form{{margin:0}} .button{{border:0;border-radius:10px;padding:12px 17px;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block;font-size:14px}} .button.primary{{background:var(--accent);color:#fff}} .button.secondary{{background:#edf2f7;color:#26384a}} .button.big{{font-size:17px;padding:15px 24px}}
.alert{{border-radius:12px;padding:15px 17px;margin:14px 0;line-height:1.5}} .info{{background:#eaf2ff;color:#174b91}} .warning{{background:#fff6db;color:#6d4a00}} .bad-panel{{background:#fff0ee;color:var(--bad)}}
.status-dot{{background:#e8f7ef;color:var(--good);border-radius:999px;padding:9px 14px;font-weight:800;font-size:13px}} .pulse{{animation:pulse 1.7s infinite}} @keyframes pulse{{50%{{opacity:.55}}}}
.job-card{{border-top:1px solid var(--line);padding:18px 0}} .job-card:first-of-type{{border-top:0;padding-top:0}} .job-head{{display:flex;justify-content:space-between;gap:20px}} .step{{margin:13px 0 8px;color:#34485b}} .progress{{height:8px;background:#edf1f5;border-radius:999px;overflow:hidden}} .progress div{{height:100%;background:var(--accent);border-radius:999px}}
.badge{{display:inline-block;border-radius:999px;padding:5px 9px;font-size:11px;font-weight:800}} .badge.good{{background:#e7f6ee;color:var(--good)}} .badge.bad{{background:#ffebe8;color:var(--bad)}} .badge.running{{background:#eaf2ff;color:#174b91}} .badge.neutral{{background:#eef1f4;color:#52606d}}
.muted{{color:var(--muted);font-size:13px}} details{{margin-top:12px}} summary{{cursor:pointer;color:var(--muted);font-size:13px}} pre{{white-space:pre-wrap;max-height:300px;overflow:auto;background:#101821;color:#dce7f3;border-radius:10px;padding:14px;font-size:11px}}
.table-wrap{{overflow:auto}} table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}} th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}} footer{{color:var(--muted);font-size:12px;margin-top:25px;text-align:center}}
@media(max-width:700px){{header{{display:block}} .status-dot{{display:inline-block;margin-top:18px}} .metrics{{grid-template-columns:1fr}} h1{{font-size:31px}}}}
</style></head><body><main>{body}</main></body></html>"""


@app.post("/run/full-setup")
def run_full(_: str = Depends(_auth)):
    db = Database(os.getenv("SUPABASE_DB_URL"))
    create_job(db, "full_setup")
    return RedirectResponse(url="/", status_code=303)


@app.post("/run/resume")
def run_resume(_: str = Depends(_auth)):
    db = Database(os.getenv("SUPABASE_DB_URL"))
    create_job(db, "resume_backfill")
    return RedirectResponse(url="/", status_code=303)


@app.post("/run/quality-export")
def run_quality_export(_: str = Depends(_auth)):
    db = Database(os.getenv("SUPABASE_DB_URL"))
    create_job(db, "quality_export")
    return RedirectResponse(url="/", status_code=303)


@app.post("/run/incremental")
def run_incremental(_: str = Depends(_auth)):
    db = Database(os.getenv("SUPABASE_DB_URL"))
    create_job(db, "incremental")
    return RedirectResponse(url="/", status_code=303)


@app.post("/run/preflight")
def run_preflight(_: str = Depends(_auth)):
    db = Database(os.getenv("SUPABASE_DB_URL"))
    create_job(db, "preflight")
    return RedirectResponse(url="/", status_code=303)


@app.get("/download/discovery")
def download_discovery(_: str = Depends(_auth)):
    db = Database(os.getenv("SUPABASE_DB_URL"))
    try:
        url = signed_discovery_url(db)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not create the secure download link: {exc}") from exc
    if not url:
        raise HTTPException(status_code=404, detail="No discovery export has been uploaded yet")
    return RedirectResponse(url=url, status_code=302)


@app.get("/api/status")
def api_status(_: str = Depends(_auth)):
    if BOOTSTRAP_ERROR:
        return JSONResponse(status_code=503, content={"ok": False, "error": BOOTSTRAP_ERROR})
    db = Database(os.getenv("SUPABASE_DB_URL"))
    return {
        "ok": True,
        "settings": get_settings(db),
        "jobs": latest_jobs(db),
        "discovery_ready": bool(latest_discovery_object(db)),
    }
