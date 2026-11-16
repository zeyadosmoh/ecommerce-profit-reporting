"""Editable PowerPoint deck: built only from facts.json, no DB and no AI key needed."""

import json
import re
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
    banner = STRINGS[lang]["banner"]
    if lang == "ar":  # the English label and the Arabic sentence are separate paragraphs (no mixed-direction line)
        head, rest = banner.split(" — ", 1)
        assert head == "Synthetic data" and {head, rest} <= set(paragraphs(prs.slides[0]))
    else:
        assert banner in paragraphs(prs.slides[0])
    for slide in prs.slides:
        text = slide_text(slide)
        assert DECK_STRINGS[lang]["footer"] in paragraphs(slide)
        assert "Synthetic data" in text  # English label in both languages, so a forwarded slide is still marked
    props = prs.core_properties
    assert "synthetic" in props.title.lower() and props.subject.startswith("SYNTHETIC DATA")
    assert props.author == props.last_modified_by == "P3 profit reporting (synthetic data)"  # no template leftovers


def para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(qn("a:t")))


ARABIC = re.compile("[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
BIDI_CONTROLS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


@pytest.mark.parametrize("src", ["template", "sample"])
def test_arabic_paragraph_direction_follows_content(src: str) -> None:
    prs = build_deck(facts_for(src, "ar"), "ar")
    rtl = ltr = 0
    for slide in prs.slides:
        for p in slide._element.iter(qn("a:p")):
            text, pPr = para_text(p), p.find(qn("a:pPr"))
            assert pPr is not None, text
            if ARABIC.search(text):
                assert pPr.get("rtl") == "1", text
                rtl += 1
            else:  # KPI values, change lines, SKU / product names, week ids, dates, numeric cells
                assert pPr.get("rtl") == "0", text
                ltr += 1
            assert pPr.get("algn") in ("r", "l", "ctr")  # right, except numeric table columns at the far end
    assert rtl > 60 and ltr > 60


@pytest.mark.parametrize(("src", "lang"), CASES)
def test_no_bidi_control_characters_in_deck_text(src: str, lang: str) -> None:
    # PowerPoint ignores LRE/PDF/LRM here, so the deck must not rely on them
    for slide in build_deck(facts_for(src, lang), lang).slides:
        for text in paragraphs(slide):
            assert not BIDI_CONTROLS.search(text), repr(text)


def test_arabic_mixed_tokens_never_share_a_paragraph_with_arabic_text() -> None:
    """SKUs ("ACC-006") and week ids ("2026-W45") are reordered by PowerPoint inside RTL text, so they get their own
    LTR paragraph or box. The AI bullets on slide 3 are verified model text and are left as written."""
    facts = sample_facts("ar")
    prs = build_deck(facts, "ar")
    token = re.compile(r"[A-Z]{3}-\d{3}|\d{4}-W\d{2}")
    for n, slide in enumerate(prs.slides):
        for p in slide._element.iter(qn("a:p")):
            text = para_text(p)
            if n == 2 and text in facts["ai_summary"]["bullets"]:
                continue
            assert not (ARABIC.search(text) and token.search(text)), text


def test_arabic_sku_lines_are_ltr_and_descriptions_rtl_with_arabic_comma() -> None:
    facts = template_facts("ar")
    slide = build_deck(facts, "ar").slides[6]
    paras = list(slide._element.iter(qn("a:p")))
    texts = [para_text(p) for p in paras]
    for sku in facts["missing_cost"]["skus"]:
        i = texts.index(f"{sku['sku']} — {sku['product']}")
        pPr = paras[i].find(qn("a:pPr"))
        assert pPr.get("rtl") == "0" and pPr.get("algn") == "r"
        desc = texts[i + 1]
        assert paras[i + 1].find(qn("a:pPr")).get("rtl") == "1"
        assert desc == f"{sku['lines_to_date']} {STRINGS['ar']['lines_to_date']}، {STRINGS['ar']['since']}:"
        assert texts[i + 2] == sku["first_sale"] and paras[i + 2].find(qn("a:pPr")).get("rtl") == "0"  # date on its own


def test_arabic_kpi_values_and_footer_week_are_right_aligned_ltr() -> None:
    facts = template_facts("ar")
    prs = build_deck(facts, "ar")
    ltr_right = {para_text(p) for s in prs.slides for p in s._element.iter(qn("a:p"))
                 if p.find(qn("a:pPr")).get("rtl") == "0" and p.find(qn("a:pPr")).get("algn") == "r"}
    assert "SAR 30,247.12" in ltr_right and "40.17%" in ltr_right and "▲ +2.84 pp" in ltr_right
    assert "▼ -7,562.57 (-20.0%)" in ltr_right and "2026-W45" in ltr_right
    for slide in list(prs.slides)[1:]:  # the week id is its own box in every footer
        assert "2026-W45" in paragraphs(slide)


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


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_data_quality_amount_is_labelled_by_order_week_in_deck_and_pdf(lang: str) -> None:
    from p3.report.build import summary_label
    from p3.report.render import SummaryView, render_html

    facts = template_facts(lang)
    t = STRINGS[lang]
    text = slide_text(build_deck(facts, lang).slides[6])
    assert t["dq_amount"] in text and t["dq_basis"] in text
    ai = facts["ai_summary"]
    html = render_html(facts, SummaryView(ai["bullets"], summary_label(ai, lang), ai["notice"]), lang)
    assert t["dq_amount"] in html and t["dq_basis"].replace("'", "&#39;") in html


def test_render_from_facts_writes_the_deck_and_leaves_facts_untouched(tmp_path: Path) -> None:
    from p3.report.build import render_from_facts

    week = tmp_path / "2026-W45"
    week.mkdir()
    facts_path = week / "facts.ar.json"
    facts_path.write_text(json.dumps(template_facts("ar"), ensure_ascii=False), encoding="utf-8")
    before = facts_path.read_bytes()
    files = render_from_facts(facts_path, "pptx")
    assert files.pptx == week / "report.ar.pptx" and files.pdf is None and files.summary is None
    assert facts_path.read_bytes() == before
    assert main(["report", "--from-facts", str(facts_path), "--format", "pptx"]) == 0
