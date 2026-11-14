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
                   "change": {"abs": 2.84, "unit": "pp", "direction": "up"}},
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
    ok, reason = results("114%")["114%"]
    assert not ok and reason == "unit: written as percent but the fact is an amount"
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
    text = "- Net profit fell 14.7%.\n- Orders fell to 114.\n- Net margin rose 2.84 points.\n- Recommended action: review the free-shipping threshold."
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


# ---- units: margin changes are percentage points ---------------------------------------------

@pytest.mark.parametrize("text,ok", [
    ("Net margin rose 2.84 percentage points to 40.17%.", True),
    ("Net margin rose 2.84 pp to 40.17%.", True),
    ("Net margin rose 2.84 points.", True),
    ("Net margin rose 2.84%.", False),
    ("Net margin rose 2.84 percent.", False),
    ("Net margin was 40.17 percentage points.", False),  # a level in percent written as pp
    ("ارتفع هامش الربح 2.84 نقطة مئوية إلى 40.17%.", True),
    ("ارتفع هامش الربح ٢٫٨٤ نقطة مئوية.", True),
    ("ارتفع هامش الربح بنسبة 2.84%.", False),
    ("ارتفع هامش الربح ٢٫٨٤٪.", False),
    ("ارتفع هامش الربح 2.84 بالمئة.", False),
])
def test_percentage_points_vs_percent(text: str, ok: bool) -> None:
    checks = check_numbers(text, FACTS)
    assert all(c.ok for c in checks) is ok, [(c.text, c.reason) for c in checks]
    if not ok:
        assert any(c.reason and c.reason.startswith("unit:") for c in checks)


# ---- causal claims need a hedge ------------------------------------------------------------------

@pytest.mark.parametrize("bullet,ok", [
    ("Net profit fell 14.7%, driven by lower sales.", False),
    ("Lower COGS helped profit.", False),
    ("Net profit fell 14.7% due to fewer orders.", False),
    ("Net profit fell because orders fell to 114.", False),
    ("Free shipping led to a subsidy of SAR 1,469.90.", False),
    ("Net profit fell 14.7%, possibly driven by lower sales.", True),
    ("Fewer orders may have hurt profit.", True),
    ("Lower sales likely led to the drop.", True),
    ("Net profit fell 14.7% to SAR 11,481.12.", True),  # no causal claim
    ("انخفض صافي الربح 14.7% بسبب تراجع الطلبات.", False),
    ("ساهم نمو المنتجات في الأداء.", False),
    ("أدى الشحن المجاني إلى دعم بقيمة 1,469.90 ريال.", False),
    ("انخفض صافي الربح 14.7%، ربما بسبب تراجع الطلبات.", True),
    ("قد يكون الشحن المجاني أثر على الربح.", True),
    ("الشحن المجاني سبب محتمل لانخفاض الربح، بسبب ارتفاع التكلفة.", True),
])
def test_causal_claims_need_a_hedge(bullet: str, ok: bool) -> None:
    from p3.summary.verify import unhedged_causal_claims

    assert (unhedged_causal_claims([bullet]) == []) is ok


# ---- substance: top insight and specific action -------------------------------------------------

INSIGHT_FACTS = {**FACTS, "insights": [
    {"rank": 1, "id": "net_profit_change", "key_number": 1984.49, "key_number_pct": 14.7, "unit": "SAR",
     "direction": "down", "impact_sar": 1984.49},
    {"rank": 2, "id": "shipping_subsidy", "key_number": 1469.9, "unit": "SAR", "impact_sar": 1469.9},
]}
BASE = ["Orders fell to 114.", "Net margin rose 2.84 percentage points to 40.17%.",
        "Shipping cost exceeded shipping charged by SAR 1,469.90."]


@pytest.mark.parametrize("extra,ok", [
    ("Net profit fell SAR 1,984.49.", True),
    ("Net profit fell about SAR 2.0k.", True),
    ("Net profit fell 14.7%.", True),
    ("Net profit fell to SAR 11,481.12.", False),  # level only: the top insight's key number is missing
])
def test_top_insight_key_number_must_be_mentioned(extra: str, ok: bool) -> None:
    text = "\n".join(f"- {b}" for b in [extra, *BASE, "Recommended action: add cost rows to the cost sheet."])
    v = verify_summary(text, INSIGHT_FACTS, action_label="Recommended action:")
    assert v.passed is ok, (v.problems, [(c.text, c.reason) for c in v.failures])
    if not ok:
        assert any("top insight" in p for p in v.problems)


@pytest.mark.parametrize("action,lang,ok", [
    ("Recommended action: add a cost row for PRF-007 to the cost sheet.", "en", True),
    ("Recommended action: review the free-shipping threshold.", "en", True),
    ("Recommended action: improve profitability across the store.", "en", False),
    ("Recommended action: assign cost records to the missing-cost lines.", "en", False),
    ("الإجراء المقترح: راجع حدّ الشحن المجاني.", "ar", True),
    ("الإجراء المقترح: أضف تكلفة PRF-007 إلى ورقة التكاليف.", "ar", True),
    ("الإجراء المقترح: تحسين الربحية بشكل عام.", "ar", False),
])
def test_action_must_name_a_sku_or_lever(action: str, lang: str, ok: bool) -> None:
    label = action.split(":")[0] + ":"
    text = "\n".join(f"- {b}" for b in ["Net profit fell 14.7%.", *BASE, action])
    v = verify_summary(text, INSIGHT_FACTS, action_label=label, lang=lang)
    assert (not any("recommended action" in p for p in v.problems)) is ok, v.problems
