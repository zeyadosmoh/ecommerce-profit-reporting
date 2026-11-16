"""End-to-end weekly report against the dbt-built p3_test database, with a fake LLM (no key needed)."""

import json
import unicodedata
from datetime import date
from pathlib import Path

import psycopg
import pytest
from pypdf import PdfReader

from p3.config import Settings
from p3.report.build import build_report
from p3.report.facts import FactsError, build_facts
from p3.summary.template import template_summary
from tests.test_summary import FakeLLM

pytestmark = pytest.mark.dbt
WEEK = date(2026, 11, 2)


def pdf_text(path: Path) -> str:
    # NFKC folds typographic ligatures such as "ﬁ" back to "fi"
    return unicodedata.normalize("NFKC", "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages))


def test_english_report_with_verified_ai_summary(built: psycopg.Connection, settings: Settings, tmp_path: Path) -> None:
    facts = build_facts(built, WEEK)
    good = "\n".join(f"- {b}" for b in template_summary(facts, "en"))  # numbers straight from facts
    files = build_report(built, settings, WEEK, "en", client=FakeLLM(good), out_dir=tmp_path, preview=True)
    assert files.directory == tmp_path / "2026-W45"
    for p in (files.facts, files.html, files.pdf, files.preview):
        assert p.exists() and p.stat().st_size > 1000
    saved = json.loads(files.facts.read_text())
    assert saved["ai_summary"]["source"] == "llm" and saved["ai_summary"]["status"] == "verified"
    assert saved["ai_summary"]["model"] == "fake-model-1" and saved["ai_summary"]["tokens"]["input"] == 1000
    assert saved["ai_summary"]["cost_note"] == "pricing not configured"
    html = files.html.read_text()
    assert "Synthetic data" in html and "AI summary unavailable" not in html
    text = pdf_text(files.pdf)
    assert "Synthetic data" in text and "Weekly profit report" in text and "2026-W45" in text


def test_bridge_in_facts_reconciles(built: psycopg.Connection) -> None:
    facts = build_facts(built, WEEK)
    for block in ("revenue", "profit"):
        for col in ("this_week", "previous_week"):
            running = None
            for row in facts["bridge"][block]:
                value = round(row[col], 2)
                if row["op"] == "":
                    running = value
                elif row["op"] == "+":
                    running = round(running + value, 2)
                elif row["op"] == "-":
                    running = round(running - value, 2)
                else:
                    assert running == value, (block, row["label"], col)
    ship = facts["shipping"]
    assert round(ship["free_shipping_part"] + ship["paid_shipping_part"], 2) == ship["subsidy"]
    assert round(ship["shipping_cost"] - ship["shipping_charged"], 2) == ship["subsidy"]


def test_failed_number_check_is_shown_in_the_report(built: psycopg.Connection, settings: Settings, tmp_path: Path) -> None:
    bad = "- Net profit rose 99.9% to SAR 1,234,567.00.\n- a\n- b\n- Recommended action: none."
    files = build_report(built, settings, WEEK, "en", client=FakeLLM(bad, bad), out_dir=tmp_path)
    assert "AI summary unavailable: failed number check" in files.html.read_text()
    assert "AI summary unavailable: failed number check" in pdf_text(files.pdf)
    assert json.loads(files.facts.read_text())["ai_summary"]["source"] == "template"


def test_arabic_report_without_key_uses_template(built: psycopg.Connection, settings: Settings, tmp_path: Path) -> None:
    files = build_report(built, settings.model_copy(update={"gemini_api_key": "", "llm_api_key": ""}), WEEK, "ar",
                         out_dir=tmp_path)
    assert files.html.name == "report.ar.html" and files.pdf.name == "report.ar.pdf"
    html = files.html.read_text()
    assert 'dir="rtl"' in html and "محتمل" in html and "Synthetic data" in html
    assert "Synthetic data" in pdf_text(files.pdf)
    saved = json.loads(files.facts.read_text())
    assert saved["ai_summary"]["status"] == "no_llm" and saved["ai_summary"]["template_verification"]["passed"]


def test_rejects_non_monday_and_partial_weeks(built: psycopg.Connection) -> None:
    with pytest.raises(FactsError, match="not a Monday"):
        build_facts(built, date(2026, 11, 3))
    with pytest.raises(FactsError):
        build_facts(built, date(2026, 11, 9))


def test_movers_flag_products_without_sales(built: psycopg.Connection, settings: Settings, tmp_path: Path) -> None:
    facts = build_facts(built, WEEK)
    movers = facts["movers"]["risers"] + facts["movers"]["fallers"] + facts["movers"]["missing_cost_products"]
    assert all(isinstance(m["sold_this_week"], bool) for m in movers)
    unsold = [m for m in movers if not m["sold_this_week"]]
    assert unsold and all(m["net_revenue_this_week"] == 0 for m in unsold)
    assert all(m["net_revenue_this_week"] != 0 for m in movers if m["sold_this_week"])
    files = build_report(built, settings, WEEK, "en", use_ai=False, out_dir=tmp_path)
    assert "no sales this week" in files.html.read_text()


def test_pptx_only_with_no_key_writes_facts_and_deck(built: psycopg.Connection, settings: Settings,
                                                     tmp_path: Path) -> None:
    from pptx import Presentation

    files = build_report(built, settings.model_copy(update={"gemini_api_key": "", "llm_api_key": ""}), WEEK, "ar",
                         out_dir=tmp_path, formats="pptx")
    assert files.html is None and files.pdf is None and files.pptx.name == "report.ar.pptx"
    assert sorted(p.name for p in files.directory.iterdir()) == ["facts.ar.json", "report.ar.pptx"]
    saved = json.loads(files.facts.read_text())
    assert saved["report"]["files"] == ["report.ar.pptx"] and saved["ai_summary"]["status"] == "no_llm"
    assert len(Presentation(str(files.pptx)).slides) == 7


def test_pdf_and_pptx_carry_the_same_verified_summary(built: psycopg.Connection, settings: Settings,
                                                      tmp_path: Path) -> None:
    from tests.test_deck import slide_text
    from pptx import Presentation

    facts = build_facts(built, WEEK)
    good = "\n".join(f"- {b}" for b in template_summary(facts, "en"))
    files = build_report(built, settings, WEEK, "en", client=FakeLLM(good), out_dir=tmp_path, formats="pdf,pptx")
    assert [p.name for p in files.written] == ["facts.json", "report.html", "report.pdf", "report.pptx"]
    saved = json.loads(files.facts.read_text())
    deck_summary = slide_text(Presentation(str(files.pptx)).slides[2])
    for bullet in saved["ai_summary"]["bullets"]:
        assert bullet in deck_summary and bullet in files.html.read_text()
    assert "fake-model-1" in deck_summary
