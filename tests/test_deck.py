"""Editable PowerPoint deck: built only from facts.json, no DB and no AI key needed."""

import json
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.oxml.ns import qn

from p3.cli import main
from p3.report.deck import DECK_STRINGS, build_deck, deck_titles, write_deck
from p3.report.formats import parse_formats
from p3.report.i18n import BRIDGE_LABELS_AR, STRINGS
from p3.summary.generate import generate_summary
from p3.summary.verify import check_numbers

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "docs" / "sample-report" / "2026-W45"
FIXTURE = Path(__file__).parent / "fixtures" / "facts_w45.json"


def template_facts(lang: str) -> dict:
    """facts.json as `p3 report` writes it when no AI key is configured (template summary)."""
    facts = json.loads(FIXTURE.read_text(encoding="utf-8"))
    outcome = generate_summary(facts, None, lang, no_client_reason="GEMINI_API_KEY not set")
    return {**facts, "ai_summary": {**outcome.log, "bullets": outcome.bullets, "notice": outcome.notice},
            "report": {"language": lang, "files": []}}


def sample_facts(lang: str) -> dict:
    return json.loads((SAMPLE / ("facts.json" if lang == "en" else f"facts.{lang}.json")).read_text(encoding="utf-8"))


CASES = [pytest.param(src, lang, id=f"{src}-{lang}") for src in ("template", "sample") for lang in ("en", "ar")]


def facts_for(src: str, lang: str) -> dict:
    return template_facts(lang) if src == "template" else sample_facts(lang)


def paragraphs(slide) -> list[str]:
    """Every paragraph's text on a slide, text boxes and table cells alike."""
    return ["".join(t.text or "" for t in p.iter(qn("a:t"))) for p in slide._element.iter(qn("a:p"))]


def slide_text(slide) -> str:
    return "\n".join(paragraphs(slide))


@pytest.mark.parametrize(("src", "lang"), CASES)
def test_seven_slides_with_expected_titles(src: str, lang: str) -> None:
    prs = build_deck(facts_for(src, lang), lang)
    assert len(prs.slides) == 7
    titles = deck_titles(lang)
    for slide, title in zip(prs.slides, titles, strict=True):
        assert [p for p in paragraphs(slide) if p][0] == title  # first text on every slide is its title
    assert prs.slide_width * 9 == prs.slide_height * 16  # 16:9


@pytest.mark.parametrize(("src", "lang"), CASES)
def test_every_number_in_the_deck_is_in_facts(src: str, lang: str) -> None:
    facts = facts_for(src, lang)
    prs = build_deck(facts, lang)
    checked, failures = 0, []
    for n, slide in enumerate(prs.slides, start=1):
        for text in paragraphs(slide):
            for c in check_numbers(text, facts):
                checked += 1
                if not c.ok:
                    failures.append(f"slide {n}: {c.text!r} in {text!r}: {c.reason}")
    assert not failures, "\n".join(failures)
    assert checked > 100  # KPI cards, bridge, movers and data-quality tables were all checked


@pytest.mark.parametrize(("src", "lang"), CASES)
def test_synthetic_banner_on_title_and_footer_on_every_slide(src: str, lang: str) -> None:
    prs = build_deck(facts_for(src, lang), lang)
    assert STRINGS[lang]["banner"] in paragraphs(prs.slides[0])
    for slide in prs.slides:
        text = slide_text(slide)
        assert DECK_STRINGS[lang]["footer"] in paragraphs(slide)
        assert "Synthetic data" in text  # English label in both languages, so a forwarded slide is still marked
    assert "synthetic" in prs.core_properties.title.lower()


@pytest.mark.parametrize("src", ["template", "sample"])
def test_every_arabic_paragraph_is_rtl_and_right_aligned(src: str) -> None:
    prs = build_deck(facts_for(src, "ar"), "ar")
    count = 0
    for slide in prs.slides:
        for p in slide._element.iter(qn("a:p")):
            pPr = p.find(qn("a:pPr"))
            assert pPr is not None and pPr.get("rtl") == "1", "".join(t.text or "" for t in p.iter(qn("a:t")))
            assert pPr.get("algn") in ("r", "l", "ctr")  # labels right; numeric table columns sit at the far end
            count += 1
    assert count > 150


def test_english_paragraphs_are_not_rtl() -> None:
    prs = build_deck(template_facts("en"), "en")
    assert not [p for s in prs.slides for p in s._element.iter(qn("a:pPr")) if p.get("rtl") == "1"]


def test_arabic_runs_have_an_arabic_capable_font() -> None:
    prs = build_deck(template_facts("ar"), "ar")
    for slide in prs.slides:
        for rPr in slide._element.iter(qn("a:rPr")):
            assert rPr.get("lang") == "ar-SA"
            assert rPr.find(qn("a:cs")).get("typeface") == "Arial"
            assert rPr.find(qn("a:latin")).get("typeface") == "Arial"


def test_arabic_tables_are_mirrored() -> None:
    facts = template_facts("ar")
    en, ar = build_deck(template_facts("en"), "en"), build_deck(facts, "ar")
    en_tbl = next(s for s in en.slides[3].shapes if s.has_table).table
    ar_tbl = next(s for s in ar.slides[3].shapes if s.has_table).table
    first = facts["bridge"]["revenue"][0]
    assert en_tbl.cell(1, 1).text == first["label"]
    assert ar_tbl.cell(1, 2).text == BRIDGE_LABELS_AR[first["label"]]  # label column moved to the right
    assert ar_tbl.cell(1, 0).text.strip("‪‬") == en_tbl.cell(1, 3).text  # previous week now leftmost
    assert ar_tbl.cell(0, 1).text == STRINGS["ar"]["this_week"]
    assert ar_tbl.columns[3].width == en_tbl.columns[0].width


def test_template_deck_shows_the_template_label_and_no_ai_text() -> None:
    facts = template_facts("en")
    text = slide_text(build_deck(facts, "en").slides[2])
    assert STRINGS["en"]["summary_template"] in text and "GEMINI_API_KEY not set" in text
    for bullet in facts["ai_summary"]["bullets"]:
        assert bullet in text


def test_failed_check_notice_is_on_the_summary_slide() -> None:
    facts = template_facts("en")
    facts["ai_summary"]["notice"] = "AI summary unavailable: failed number check"
    assert "AI summary unavailable: failed number check" in slide_text(build_deck(facts, "en").slides[2])


def test_charts_are_the_only_pictures_and_text_is_native() -> None:
    prs = build_deck(template_facts("en"), "en")
    pictures = [(n, s.name) for n, slide in enumerate(prs.slides) for s in slide.shapes if s.shape_type == 13]
    assert pictures == [(4, "chart net_revenue"), (4, "chart net_profit"), (4, "chart margin")]
    tables = [n for n, slide in enumerate(prs.slides) for s in slide.shapes if s.has_table]
    assert tables == [3, 3, 3, 5, 6]  # bridge revenue/profit/excluded, movers, data quality


def test_write_deck_round_trips(tmp_path: Path) -> None:
    out = write_deck(template_facts("ar"), tmp_path / "w" / "report.ar.pptx", "ar")
    prs = Presentation(str(out))
    assert len(prs.slides) == 7 and out.stat().st_size > 20_000


def test_deck_cli_builds_from_facts_without_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    facts_path = tmp_path / "facts.ar.json"
    facts_path.write_text(json.dumps(template_facts("ar"), ensure_ascii=False), encoding="utf-8")
    assert main(["deck", "--facts", str(facts_path)]) == 0
    prs = Presentation(str(tmp_path / "report.ar.pptx"))  # language taken from facts.report.language
    assert [p for p in paragraphs(prs.slides[0]) if p][0] == STRINGS["ar"]["title"]


@pytest.mark.parametrize(("value", "expected"), [("pdf", ("pdf",)), ("pptx", ("pptx",)), ("pptx,pdf", ("pdf", "pptx")),
                                                 (" PDF , pptx ", ("pdf", "pptx")), (None, ("pdf",))])
def test_parse_formats(value: str | None, expected: tuple[str, ...]) -> None:
    assert parse_formats(value) == expected


@pytest.mark.parametrize("value", ["docx", "pdf,xlsx", ","])
def test_parse_formats_rejects_unknown(value: str) -> None:
    with pytest.raises(ValueError):
        parse_formats(value)


def test_unknown_deck_language() -> None:
    with pytest.raises(ValueError):
        build_deck(template_facts("en"), "fr")
