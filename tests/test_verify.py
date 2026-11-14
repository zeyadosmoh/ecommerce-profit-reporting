"""Verifier: number formats, direction check (EN + AR), dates/SKUs, and the exact Arabic strings."""

from importlib.resources import files

import pytest

from p3.summary.verify import check_numbers, normalize, verify_summary

FACTS = {
    "week": {"start": "2026-11-02", "end": "2026-11-08", "iso_week": "2026-W45", "iso_week_number": 45},
    "kpis": {
        "net_profit": {"unit": "SAR", "this_week": 11481.12, "previous_week": 13465.61,
                       "change": {"abs": -1984.49, "direction": "down", "pct": -14.7}},
        "net_revenue": {"unit": "SAR", "this_week": 30247.12, "previous_week": 37809.69,
                        "change": {"abs": -7562.57, "direction": "down", "pct": -20.0}},
        "margin": {"unit": "pct", "this_week": 40.17, "previous_week": 37.33,
                   "change": {"abs": 2.84, "direction": "up"}},
        "orders": {"unit": "count", "this_week": 114, "previous_week": 132,
                   "change": {"abs": -18, "direction": "down", "pct": -13.6}},
        "aov": {"unit": "SAR", "this_week": 270.79, "previous_week": 270.79,
                "change": {"abs": 0.0, "direction": "flat", "pct": 0.0}},
    },
    "shipping": {"subsidy": 1469.9, "free_share_note_pct": 12.04},
    "movers": {"risers": [{"sku": "PRF-007", "product": "Discovery Set 5x8ml"}]},
}


def results(text: str) -> dict[str, tuple[bool, str | None]]:
    return {c.text: (c.ok, c.reason) for c in check_numbers(text, FACTS)}


@pytest.mark.parametrize("text", [
    "SAR 11,481.12", "SAR 11,481", "11481", "11.5k", "11.5K", "11.5 ألف ريال", "SAR 30.2k",
    "١١٬٤٨١٫١٢ ريال", "١١٤ طلب", "12.0%", "12%", "١٢٫٠٪", "40.17%", "40.2%",
])
def test_format_variants_match(text: str) -> None:
    checks = check_numbers(text, FACTS)
    assert checks and all(c.ok for c in checks), [(c.text, c.reason) for c in checks]


@pytest.mark.parametrize("text", ["SAR 9,999", "11.4k", "115 orders", "41%", "114%", "12.1%"])
def test_invented_or_misrounded_numbers_fail(text: str) -> None:
    checks = check_numbers(text, FACTS)
    assert checks and not all(c.ok for c in checks)


def test_percent_only_matches_percentage_facts() -> None:
    assert results("114%")["114%"] == (False, "not found in facts.json")
    assert results("114 orders")["114"][0]


def test_normalize_arabic_forms() -> None:
    assert normalize("١٬٢٣٤٫٥٪ − ۴۵") == "1,234.5% - 45"


# ---- direction check: English ---------------------------------------------------------------

@pytest.mark.parametrize("text,ok", [
    ("Net profit fell 14.7% to SAR 11,481.12.", True),
    ("Net profit decreased by SAR 1,984.49.", True),
    ("Net profit dropped 14.7%.", True),
    ("Net profit rose 14.7%.", False),
    ("Net profit increased by 1,984.49.", False),
    ("Net profit was up 14.7% this week.", False),
    ("Net profit grew 14.7%.", False),
    ("Net margin rose 2.84 points.", True),
    ("Net margin declined 2.84 points.", False),
    ("Net profit -14.7%.", True),
    ("Net profit +14.7%.", False),
    ("Net profit fell to SAR 11,481.12, from SAR 13,465.61.", True),  # levels, not changes
])
def test_direction_english(text: str, ok: bool) -> None:
    checks = check_numbers(text, FACTS)
    assert all(c.ok for c in checks) is ok, [(c.text, c.reason) for c in checks]


def test_direction_uses_the_nearest_word_in_the_clause() -> None:
    assert all(c.ok for c in check_numbers("Net margin rose 2.84 points while net profit fell 14.7%.", FACTS))
    bad = check_numbers("Net margin fell 2.84 points while net profit rose 14.7%.", FACTS)
    assert [c.ok for c in bad] == [False, False]
    assert "direction contradicts" in bad[0].reason


def test_flat_change_contradicted_by_a_direction_word() -> None:
    assert not all(c.ok for c in check_numbers("AOV rose 0.0%.", FACTS))
    assert all(c.ok for c in check_numbers("AOV was unchanged (0.0%).", FACTS))


# ---- direction check: Arabic ----------------------------------------------------------------

@pytest.mark.parametrize("text,ok", [
    ("انخفض صافي الربح بنسبة 14.7% إلى 11,481.12 ريال.", True),
    ("انخفض صافي الربح بنسبة ١٤٫٧٪.", True),
    ("وتراجع صافي الربح 14.7%.", True),
    ("هبط صافي الربح 1,984.49 ريال.", True),
    ("قل صافي الربح 14.7%.", True),
    ("ارتفع صافي الربح بنسبة 14.7%.", False),
    ("زاد صافي الربح ١٬٩٨٤٫٤٩ ريال.", False),
    ("نما صافي الربح 14.7%.", False),
    ("وارتفع هامش الربح 2.84 نقطة مئوية.", True),
    ("انخفض هامش الربح 2.84 نقطة مئوية.", False),
    ("ارتفع هامش الربح 2.84 نقطة، بينما انخفض صافي الربح 14.7%.", True),
])
def test_direction_arabic(text: str, ok: bool) -> None:
    checks = check_numbers(text, FACTS)
    assert all(c.ok for c in checks) is ok, [(c.text, c.reason) for c in checks]


# ---- dates, ISO weeks, SKUs, product names ---------------------------------------------------

def test_fact_strings_are_masked_and_unknown_ones_fail() -> None:
    assert all(c.ok for c in check_numbers("Discovery Set 5x8ml (PRF-007) led the week 2026-W45 starting 2026-11-02.", FACTS))
    bad = results("Sales on 2026-11-09 for XYZ-999.")
    assert bad["2026-11-09"] == (False, "date not in facts")
    assert bad["XYZ-999"] == (False, "SKU not in facts")


def test_verify_summary_checks_bullet_count_and_action() -> None:
    text = "- Net profit fell 14.7%.\n- Orders fell to 114.\n- Net margin rose 2.84 points.\n- Recommended action: review shipping."
    assert verify_summary(text, FACTS, action_label="Recommended action:").passed
    three = "\n".join(text.splitlines()[:3])
    v = verify_summary(three, FACTS, action_label="Recommended action:")
    assert not v.passed and any("bullets" in p for p in v.problems)


# ---- exact Arabic strings in prompt and template (guards against reversed / mis-encoded text) ----

POSSIBLE = "محتمل"       # محتمل
THOUSAND = "ألف"                   # ألف
THOUSANDS = "آلاف"            # آلاف


def test_exact_arabic_words() -> None:
    assert POSSIBLE == "محتمل" and THOUSAND == "ألف" and THOUSANDS == "آلاف"
    prompt = files("p3.llm").joinpath("prompts", "language_ar.md").read_text(encoding="utf-8")
    template = files("p3.summary").joinpath("template.py").read_text(encoding="utf-8")
    generate = files("p3.summary").joinpath("generate.py").read_text(encoding="utf-8")
    assert POSSIBLE in prompt and THOUSAND in prompt and THOUSANDS in prompt
    assert POSSIBLE in template and THOUSAND in template
    assert POSSIBLE in generate
    assert "لمتحم" not in prompt + template + generate and "فلأ" not in prompt + template + generate
