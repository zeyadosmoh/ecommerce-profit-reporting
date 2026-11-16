"""Runner API with fake pipeline steps: auth, success payload + email, failure alert, busy lock, file whitelist."""

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from p3.config import Settings
from p3.runner import StepError, Steps, create_runner_app, serve

TOKEN = "runner-test-token-123"
FACTS = json.loads((Path(__file__).parent / "fixtures" / "facts_w45.json").read_text(encoding="utf-8"))
FACTS["ai_summary"] = {"source": "llm", "status": "verified", "notice": None, "model": "fake",
                       "bullets": ["Net profit fell 14.7% <script>x</script>.", "Recommended action: add ACC-006."]}
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(runner_api_token=TOKEN, reports_dir=tmp_path, postgres_password="pg-secret-xyz", **overrides)


def ok_steps(calls: list[str]) -> Steps:
    def report(req: object) -> dict:
        calls.append("report")
        return {"facts": FACTS, "files": {"pdf": "/files/2026-W45/report.pdf"}}
    return Steps(ingest=lambda: calls.append("ingest") or {"rows_upserted": 0},
                 dbt_build=lambda: calls.append("dbt") or {"summary": "PASS=85"}, report=report)


def test_requires_bearer_token(tmp_path: Path) -> None:
    client = TestClient(create_runner_app(settings(tmp_path), ok_steps([])))
    assert client.post("/run").status_code == 401
    assert client.post("/run", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/healthz").json()["ok"] is True


def test_success_runs_steps_in_order_and_builds_email(tmp_path: Path) -> None:
    calls: list[str] = []
    r = TestClient(create_runner_app(settings(tmp_path), ok_steps(calls))).post("/run", headers=AUTH, json={"lang": "en"})
    assert r.status_code == 200 and calls == ["ingest", "dbt", "report"]
    body = r.json()
    assert body["status"] == "success" and body["week"]["iso_week"] == "2026-W45"
    assert body["files"]["pdf"] == "/files/2026-W45/report.pdf"
    assert set(body["kpis"]) == {"net_revenue", "net_profit", "net_margin_pct_known_cost", "orders", "aov_ex_vat_products"}
    email = body["email"]
    assert email["subject"].startswith("Weekly profit report 2026-W45") and "synthetic data" in email["subject"]
    assert "SYNTHETIC DATA" in email["html"] and "SAR 11,481.12" in email["html"] and "+2.84 pp" in email["html"]
    assert "<script>" not in email["html"] and "&lt;script&gt;" in email["html"]  # bullets are escaped


@pytest.mark.parametrize("failing", ["ingest", "dbt", "report"])
def test_failure_returns_500_with_alert_email_and_no_secrets(tmp_path: Path, failing: str) -> None:
    calls: list[str] = []
    steps = ok_steps(calls)

    def boom(*_: object) -> dict:
        raise StepError(failing, f"connection failed (password pg-secret-xyz, token {TOKEN})")
    setattr(steps, {"ingest": "ingest", "dbt": "dbt_build", "report": "report"}[failing], boom)
    r = TestClient(create_runner_app(settings(tmp_path), steps)).post("/run", headers=AUTH)
    assert r.status_code == 500
    body = r.json()
    assert body["status"] == "failed" and body["step"] == failing
    assert "pg-secret-xyz" not in r.text and TOKEN not in r.text and "[REDACTED]" in body["error"]
    assert body["email"]["subject"] == f"ALERT: P3 weekly report failed at step '{failing}'"


def test_second_run_while_busy_gets_409(tmp_path: Path) -> None:
    started, release = threading.Event(), threading.Event()
    steps = ok_steps([])
    steps.ingest = lambda: (started.set(), release.wait(5), {"rows_upserted": 0})[2]
    client = TestClient(create_runner_app(settings(tmp_path), steps))
    first = threading.Thread(target=lambda: client.post("/run", headers=AUTH))
    first.start()
    assert started.wait(5)
    assert client.post("/run", headers=AUTH).status_code == 409
    release.set()
    first.join(5)


def test_file_download_is_whitelisted(tmp_path: Path) -> None:
    (tmp_path / "2026-W45").mkdir()
    (tmp_path / "2026-W45" / "report.pdf").write_bytes(b"%PDF-1.7 test")
    (tmp_path / "2026-W45" / "secret.txt").write_text("nope")
    client = TestClient(create_runner_app(settings(tmp_path), ok_steps([])))
    assert client.get("/files/2026-W45/report.pdf", headers=AUTH).content == b"%PDF-1.7 test"
    assert client.get("/files/2026-W45/report.pdf").status_code == 401
    for bad in ("/files/2026-W45/secret.txt", "/files/..%2F/report.pdf", "/files/2026-W45/..%2F..%2F.env"):
        assert client.get(bad, headers=AUTH).status_code == 404


def test_refuses_placeholder_token_and_all_interfaces(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="RUNNER_API_TOKEN"):
        create_runner_app(Settings(runner_api_token="change-me"), ok_steps([]))
    with pytest.raises(ValueError, match="all interfaces"):
        serve(settings(tmp_path), "0.0.0.0", 8770, False)


def test_pptx_is_listed_attached_and_downloadable(tmp_path: Path) -> None:
    steps = ok_steps([])
    steps.report = lambda req: {"facts": FACTS, "files": {"pdf": "/files/2026-W45/report.pdf",
                                                          "pptx": "/files/2026-W45/report.pptx"}}
    client = TestClient(create_runner_app(settings(tmp_path), steps))
    body = client.post("/run", headers=AUTH).json()
    assert body["files"]["pptx"] == "/files/2026-W45/report.pptx"
    assert "editable PowerPoint deck (.pptx)" in body["email"]["html"]
    (tmp_path / "2026-W45").mkdir()
    (tmp_path / "2026-W45" / "report.ar.pptx").write_bytes(b"PK\x03\x04 deck")
    r = client.get("/files/2026-W45/report.ar.pptx", headers=AUTH)
    assert r.status_code == 200 and r.content == b"PK\x03\x04 deck"
    assert r.headers["content-type"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def test_pdf_only_email_does_not_mention_pptx(tmp_path: Path) -> None:
    body = TestClient(create_runner_app(settings(tmp_path), ok_steps([]))).post("/run", headers=AUTH).json()
    assert "pptx" not in body["files"] and "PowerPoint" not in body["email"]["html"]
    assert "attached as PDF." in body["email"]["html"]


@pytest.mark.parametrize(("attach", "formats"), [(True, ("pdf", "pptx")), (False, ("pdf",))])
def test_report_step_builds_pptx_only_when_the_setting_is_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             attach: bool, formats: tuple[str, ...]) -> None:
    import contextlib

    import p3.db.connection
    import p3.report.build
    from p3.report.build import ReportFiles
    from p3.runner import RunRequest, default_steps

    seen: dict = {}
    d = tmp_path / "2026-W45"
    d.mkdir()
    (d / "facts.json").write_text(json.dumps(FACTS), encoding="utf-8")

    def fake_build(conn: object, s: Settings, **kw: object) -> ReportFiles:
        seen.update(kw)
        return ReportFiles(d, d / "facts.json", d / "report.html", d / "report.pdf", None, None,
                           d / "report.pptx" if "pptx" in kw["formats"] else None)
    monkeypatch.setattr(p3.report.build, "build_report", fake_build)
    monkeypatch.setattr(p3.db.connection, "connect", lambda s: contextlib.nullcontext())
    out = default_steps(settings(tmp_path, report_attach_pptx=attach), mock_store=True).report(RunRequest())
    assert seen["formats"] == formats
    assert ("pptx" in out["files"]) is attach and out["files"]["pdf"] == "/files/2026-W45/report.pdf"
