"""Runner API for n8n: POST /run runs ingest -> dbt build -> report for the last full week.

n8n runs in Docker and cannot execute host Python, so it calls this small authenticated HTTP API.
Bound to 127.0.0.1 by default (never 0.0.0.0); bearer token from RUNNER_API_TOKEN.
"""

import html
import json
import re
import secrets
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from p3 import SYNTHETIC_NOTICE
from p3.config import Settings
from p3.llm.base import redact

ISO_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
FILE_RE = re.compile(r"^(?:report(?:\.ar)?\.(?:pdf|html|pptx)|facts(?:\.ar)?\.json|preview(?:\.ar)?\.png)$")
FORBIDDEN_HOSTS = {"0.0.0.0", "::", "[::]"}
PLACEHOLDER_TOKENS = {"", "change-me"}
MEDIA_TYPES = {".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
               ".pdf": "application/pdf", ".json": "application/json", ".html": "text/html; charset=utf-8",
               ".png": "image/png"}
HEADLINE_KPIS = ["net_revenue", "net_profit", "net_margin_pct_known_cost", "orders", "aov_ex_vat_products"]


class RunRequest(BaseModel):
    lang: Literal["en", "ar"] = "en"
    week: date | None = None
    use_ai: bool = True


class StepError(Exception):
    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step


@dataclass
class Steps:
    """The pipeline steps; replaceable in tests."""

    ingest: object
    dbt_build: object
    report: object


def default_steps(settings: Settings, mock_store: bool) -> Steps:
    from p3.dbt_runner import run_dbt
    from p3.db.connection import connect
    from p3.ingest.client import StoreClient
    from p3.ingest.inprocess import in_process_store_client
    from p3.ingest.pipeline import run_ingest
    from p3.report.build import build_report

    def ingest() -> dict:
        client = in_process_store_client(settings) if mock_store else StoreClient.from_settings(settings)
        result = run_ingest(settings.dsn(), client, settings.cost_sheet_source)
        return {"run_id": result.run_id, "rows_read": result.rows_read, "rows_upserted": result.rows_upserted,
                "rows_rejected": result.rows_rejected}

    def dbt_build() -> dict:
        proc = run_dbt(["build"], settings, quiet=True)
        done = [ln for ln in proc.stdout.splitlines() if "Done. PASS=" in ln]
        summary = re.sub(r"\x1b\[[0-9;]*m", "", done[-1]).split("Done. ", 1)[1].strip() if done else "no summary"
        if proc.returncode != 0:
            failing = [re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in proc.stdout.splitlines()
                       if re.search(r"\b(FAIL|ERROR)\b", ln)][-5:]
            raise StepError("dbt", f"dbt build failed ({summary}): " + " | ".join(failing))
        return {"summary": summary}

    def report(req: RunRequest) -> dict:
        with connect(settings) as conn:
            files = build_report(conn, settings, week=req.week, lang=req.lang, use_ai=req.use_ai,
                                 formats=("pdf", "pptx") if settings.report_attach_pptx else ("pdf",))
        facts = json.loads(files.facts.read_text(encoding="utf-8"))
        return {"facts": facts, "files": {p.suffix.lstrip("."): f"/files/{files.directory.name}/{p.name}"
                                          for p in (files.facts, files.html, files.pdf, files.pptx) if p}}

    return Steps(ingest, dbt_build, report)


def create_runner_app(settings: Settings, steps: Steps | None = None, mock_store: bool = False) -> FastAPI:
    token = settings.runner_api_token.strip()
    if token in PLACEHOLDER_TOKENS:
        raise ValueError("RUNNER_API_TOKEN is not set (run scripts/append_env.py)")
    steps = steps or default_steps(settings, mock_store)
    secrets_to_redact = [token, settings.postgres_password, settings.gemini_api_key, settings.llm_api_key,
                         settings.store_api_secret]
    lock = threading.Lock()
    app = FastAPI(title="P3 runner (synthetic data)", description=SYNTHETIC_NOTICE)
    bearer = HTTPBearer(auto_error=False)

    def require_token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
        if creds is None or not secrets.compare_digest(creds.credentials.encode(), token.encode()):
            raise HTTPException(401, "invalid or missing bearer token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "synthetic": True, "busy": lock.locked()}

    @app.post("/run", dependencies=[Depends(require_token)])
    def run(req: RunRequest | None = None) -> JSONResponse:
        req = req or RunRequest()
        if not lock.acquire(blocking=False):
            return JSONResponse({"status": "busy", "error": "a run is already in progress"}, status_code=409)
        run_id, started, t0, step = str(uuid.uuid4()), datetime.now(UTC), time.perf_counter(), "ingest"
        try:
            ingest = steps.ingest()
            step = "dbt"
            dbt = steps.dbt_build()
            step = "report"
            report = steps.report(req)
        except Exception as exc:  # any failure -> structured error for the n8n alert branch
            step = exc.step if isinstance(exc, StepError) else step
            message = redact(f"{type(exc).__name__}: {exc}", *secrets_to_redact)[:2000]
            traceback.print_exc()
            body = {"status": "failed", "run_id": run_id, "step": step, "error": message,
                    "started_at": started.isoformat(timespec="seconds"), "notice": SYNTHETIC_NOTICE}
            body["email"] = alert_email(body)
            return JSONResponse(body, status_code=500)
        finally:
            lock.release()
        facts = report["facts"]
        body = {
            "status": "success", "run_id": run_id, "started_at": started.isoformat(timespec="seconds"),
            "duration_s": round(time.perf_counter() - t0, 1), "week": facts["week"], "language": req.lang,
            "steps": {"ingest": ingest, "dbt": dbt, "report": {"files": report["files"]}},
            "files": report["files"],
            "kpis": {k: facts["kpis"][k] for k in HEADLINE_KPIS},
            "ai_summary": {k: facts["ai_summary"].get(k) for k in ("source", "status", "notice", "model", "bullets")},
            "notice": SYNTHETIC_NOTICE,
        }
        body["email"] = report_email(body)
        return JSONResponse(body)

    @app.get("/files/{iso_week}/{name}", dependencies=[Depends(require_token)])
    def files(iso_week: str, name: str) -> FileResponse:
        if not ISO_WEEK_RE.fullmatch(iso_week) or not FILE_RE.fullmatch(name):
            raise HTTPException(404, "not found")
        path = Path(settings.reports_dir) / iso_week / name
        if not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path, filename=name, media_type=MEDIA_TYPES.get(path.suffix))

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"status": "error", "error": exc.detail}, status_code=exc.status_code)

    return app


def _fmt_kpi(k: dict) -> tuple[str, str]:
    c = k["change"]
    if k["unit"] == "pct":
        return f"{k['this_week']:.2f}%", f"{c['abs']:+.2f} pp"
    if k["unit"] == "count":
        return f"{k['this_week']:,}", f"{c['abs']:+,} ({c['pct']:+.1f}%)" if c.get("pct") is not None else f"{c['abs']:+,}"
    pct = f" ({c['pct']:+.1f}%)" if c.get("pct") is not None else ""
    return f"SAR {k['this_week']:,.2f}", f"{c['abs']:+,.2f}{pct}"


def report_email(body: dict) -> dict:
    week, s = body["week"], body["ai_summary"]
    rows = "".join(
        f"<tr><td>{html.escape(k['label'])}</td><td style='text-align:right'>{html.escape(v)}</td>"
        f"<td style='text-align:right;color:#616e7c'>{html.escape(d)}</td></tr>"
        for k in body["kpis"].values() for v, d in [_fmt_kpi(k)])
    source = ("AI summary (Gemini), every number verified against facts.json" if s["source"] == "llm"
              else "Template summary (no AI)")
    notice = f"<p style='color:#8a5a00'><b>{html.escape(s['notice'])}</b></p>" if s.get("notice") else ""
    bullets = "".join(f"<li>{html.escape(b)}</li>" for b in s["bullets"] or [])
    subject = f"Weekly profit report {week['iso_week']} ({week['start']} to {week['end']}) - synthetic data"
    attached = ("The full report is attached as PDF and as an editable PowerPoint deck (.pptx)."
                if "pptx" in body.get("files", {}) else "The full report is attached as PDF.")
    html_body = (
        f"<div style='font-family:Arial,sans-serif;font-size:14px;color:#1f2933'>"
        f"<p style='background:#fdecea;color:#b42318;padding:6px 8px'><b>{html.escape(body['notice'])}</b></p>"
        f"<h2 style='color:#2f6f5e'>Weekly profit report {html.escape(week['iso_week'])}</h2>"
        f"<p>{html.escape(week['start'])} to {html.escape(week['end'])} · SAR, ex-VAT · change vs previous week</p>"
        f"<table cellpadding='4' style='border-collapse:collapse'>{rows}</table>"
        f"<h3>Summary</h3>{notice}<ul>{bullets}</ul><p style='color:#616e7c'>{source}.</p>"
        f"<p>{attached}</p></div>")
    text = "\n".join([body["notice"], "", subject, ""] + [f"- {b}" for b in s["bullets"] or []])
    return {"subject": subject, "html": html_body, "text": text}


def alert_email(body: dict) -> dict:
    subject = f"ALERT: P3 weekly report failed at step '{body['step']}'"
    html_body = (
        f"<div style='font-family:Arial,sans-serif;font-size:14px'>"
        f"<h2 style='color:#b42318'>Weekly report run failed</h2>"
        f"<p><b>Step:</b> {html.escape(body['step'])}<br><b>Run:</b> {html.escape(body['run_id'])}<br>"
        f"<b>Started:</b> {html.escape(body['started_at'])}</p>"
        f"<pre style='white-space:pre-wrap;background:#f5f7fa;padding:8px'>{html.escape(body['error'])}</pre>"
        f"<p style='color:#616e7c'>{html.escape(body['notice'])}</p></div>")
    return {"subject": subject, "html": html_body, "text": f"{subject}\n\n{body['error']}"}


def serve(settings: Settings, host: str, port: int, mock_store: bool) -> None:
    import uvicorn

    if host in FORBIDDEN_HOSTS:
        raise ValueError("refusing to bind the runner to all interfaces; use 127.0.0.1 or the Docker bridge gateway")
    uvicorn.run(create_runner_app(settings, mock_store=mock_store), host=host, port=port, log_level="info")
