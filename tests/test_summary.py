"""AI summary flow with a fake LLM client (never a real key): verify, regenerate once, fall back to template."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from p3.llm.base import LLMError, LLMResponse
from p3.report.facts import llm_view
from p3.summary.generate import NOTICE_ERROR, NOTICE_FAILED, generate_summary
from p3.summary.template import template_summary

FACTS = json.loads((Path(__file__).parent / "fixtures" / "facts_w45.json").read_text(encoding="utf-8"))

GOOD_EN = """- Net revenue after refunds fell 20.0% to SAR 30,247.12 versus SAR 37,809.69 the previous week.
- Net revenue after refunds on known-cost lines fell SAR 7,491.87, which hurt profit, while COGS fell SAR 4,607.80.
- Net profit on known-cost lines fell 14.7% to SAR 11.5k, while net margin on known-cost lines rose 2.84 points to 40.17%.
- Shipping cost exceeded shipping charged by SAR 1,469.90; free-shipping orders are a possible cause of the gap.
- 20 order lines (SAR 1,666.03) have no cost row and are excluded from profit.
- Recommended action: add the missing cost rows for ACC-006 and HOM-006."""

GOOD_AR = """- انخفض صافي الإيرادات بعد المرتجعات بنسبة 20.0% إلى 30.2 ألف ريال.
- انخفض صافي الإيرادات للبنود معروفة التكلفة 7,491.87 ريال مما أضر بالربح.
- انخفض صافي الربح على البنود معروفة التكلفة بنسبة ١٤٫٧٪ إلى 11,481.12 ريال، بينما ارتفع الهامش 2.84 نقطة إلى 40.17%.
- تجاوزت تكلفة الشحن الرسوم المحصّلة بمبلغ 1,469.90 ريال، والشحن المجاني سبب محتمل.
- 20 بند طلب بقيمة 1,666.03 ريال بلا تكلفة ومستبعدة من الربح.
- الإجراء المقترح: أضف أسطر التكلفة للمنتجين ACC-006 و HOM-006."""

INVENTED = GOOD_EN.replace("SAR 1,469.90", "SAR 2,345.00")
WRONG_DIRECTION = GOOD_EN.replace("Net profit on known-cost lines fell 14.7%", "Net profit on known-cost lines rose 14.7%")


class FakeLLM:
    provider, model = "fake", "fake-model-1"

    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, model=self.model, input_tokens=1000, output_tokens=200)


def test_good_summary_passes_first_time() -> None:
    llm = FakeLLM(GOOD_EN)
    out = generate_summary(FACTS, llm, "en")
    assert (out.source, out.status, out.notice) == ("llm", "verified", None)
    assert len(out.bullets) == 6 and len(llm.calls) == 1
    log = out.log
    assert log["model"] == "fake-model-1" and log["prompt_version"] == "4"
    assert log["attempts"][0]["passed"] and log["attempts"][0]["numbers_checked"] >= 10
    assert log["tokens"] == {"input": 1000, "output": 200}
    assert log["cost_estimate_usd"] is None and log["cost_note"] == "pricing not configured"


def test_llm_receives_only_the_facts() -> None:
    llm = FakeLLM(GOOD_EN)
    generate_summary(FACTS, llm, "en")
    user = llm.calls[0][1]
    sent = json.loads(user.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert sent == llm_view(FACTS)
    blob = json.dumps(sent).lower()
    for personal in ("email", "customer_id", "billing", "address", "phone", "first_name"):
        assert personal not in blob


def test_invented_number_triggers_one_retry_with_the_bad_numbers() -> None:
    llm = FakeLLM(INVENTED, GOOD_EN)
    out = generate_summary(FACTS, llm, "en")
    assert (out.source, out.status) == ("llm", "verified")
    assert len(llm.calls) == 2
    retry_message = llm.calls[1][1]
    assert "failed the number check" in retry_message and "'2,345.00'" in retry_message
    assert "not found in facts.json" in retry_message
    assert [a["passed"] for a in out.log["attempts"]] == [False, True]
    assert out.log["attempts"][0]["unmatched_numbers"] == [{"text": "2,345.00", "reason": "not found in facts.json"}]


def test_still_failing_after_retry_falls_back_to_template_with_notice() -> None:
    llm = FakeLLM(INVENTED, INVENTED)
    out = generate_summary(FACTS, llm, "en")
    assert (out.source, out.status, out.notice) == ("template", "failed_number_check", NOTICE_FAILED)
    assert out.notice == "AI summary unavailable: failed number check"
    assert out.bullets == template_summary(FACTS, "en")
    assert len(out.log["attempts"]) == 2 and out.log["template_verification"]["passed"]


def test_wrong_direction_counts_as_a_failed_number() -> None:
    out = generate_summary(FACTS, FakeLLM(WRONG_DIRECTION, WRONG_DIRECTION), "en")
    assert out.status == "failed_number_check"
    reasons = [u["reason"] for u in out.log["attempts"][0]["unmatched_numbers"]]
    assert any("direction contradicts" in r for r in reasons)


def test_arabic_summary_passes() -> None:
    out = generate_summary(FACTS, FakeLLM(GOOD_AR), "ar")
    assert (out.source, out.status) == ("llm", "verified"), out.log["attempts"]


def test_arabic_wrong_direction_fails() -> None:
    bad = GOOD_AR.replace("انخفض صافي الربح", "ارتفع صافي الربح")
    out = generate_summary(FACTS, FakeLLM(bad, bad), "ar")
    assert out.status == "failed_number_check" and out.source == "template"


def test_no_client_uses_template_without_crashing() -> None:
    out = generate_summary(FACTS, None, "en", no_client_reason="no API key")
    assert (out.source, out.status, out.notice) == ("template", "no_llm", None)
    assert out.log["reason"] == "no API key" and out.log["template_verification"]["passed"]


def test_llm_error_uses_template_with_notice() -> None:
    out = generate_summary(FACTS, FakeLLM(LLMError("503 UNAVAILABLE")), "en")
    assert (out.source, out.status, out.notice) == ("template", "llm_error", NOTICE_ERROR)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_template_summary_passes_the_verifier(lang: str) -> None:
    out = generate_summary(FACTS, None, lang)
    check = out.log["template_verification"]
    assert check["passed"], check
    assert 4 <= len(out.bullets) <= 6


def test_cost_estimate_uses_configured_prices() -> None:
    out = generate_summary(FACTS, FakeLLM(GOOD_EN), "en", prices=(Decimal("0.10"), Decimal("0.40")))
    assert out.log["cost_estimate_usd"] == 0.00018  # 1000 * 0.10 / 1M + 200 * 0.40 / 1M


def test_wrong_bullet_count_is_a_failure() -> None:
    three = "\n".join(GOOD_EN.splitlines()[:2] + GOOD_EN.splitlines()[-1:])
    out = generate_summary(FACTS, FakeLLM(three, three), "en")
    assert out.status == "failed_number_check"
    assert any("bullets" in p for p in out.log["attempts"][0]["problems"])


# ---- movers without sales this week ---------------------------------------------------------

def test_mover_without_sales_is_described_as_no_sales_not_a_loss() -> None:
    faller = FACTS["movers"]["fallers"][0]
    assert faller["sku"] == "PRF-003" and faller["sold_this_week"] is False  # W45 fixture: no units sold
    en = " ".join(template_summary(FACTS, "en"))
    assert f"{faller['product']}, with no sales this week" in en
    assert f"{faller['product']} at SAR 0.00" not in en
    ar = " ".join(template_summary(FACTS, "ar"))
    assert "بلا مبيعات هذا الأسبوع" in ar and "بصافي ربح 0.00" not in ar


def test_mover_with_sales_keeps_the_profit_comparison() -> None:
    facts = json.loads(json.dumps(FACTS))
    facts["movers"]["fallers"][0]["sold_this_week"] = True
    en = " ".join(template_summary(facts, "en"))
    assert "no sales this week" not in en and "at SAR 0.00 vs a 4-week average of SAR 382.44" in en


def test_prompt_explains_sold_this_week() -> None:
    from p3.summary.generate import build_system_prompt

    en, meta = build_system_prompt("en")
    ar, _ = build_system_prompt("ar")
    assert meta["prompt_version"] == "4"
    assert '"sold_this_week": false' in en and '"no sales this week"' in en
    assert '"بلا مبيعات هذا الأسبوع"' in ar



# ---- v3 checks in the full flow ----------------------------------------------------------------

def test_facts_rank_insights_by_impact() -> None:
    ids = [i["id"] for i in FACTS["insights"]]
    assert ids[:4] == ["top_profit_driver", "net_profit_change", "missing_cost", "shipping_subsidy"]
    assert [i["rank"] for i in FACTS["insights"]] == list(range(1, len(ids) + 1))
    impacts = [i["impact_sar"] for i in FACTS["insights"]]
    assert impacts == sorted(impacts, reverse=True)
    margin = next(i for i in FACTS["insights"] if i["id"] == "margin_change")
    assert margin["unit"] == "pp" and FACTS["kpis"]["net_margin_pct_known_cost"]["change"]["unit"] == "pp"


def test_unhedged_cause_triggers_retry_then_passes() -> None:
    causal = GOOD_EN.replace("free-shipping orders are a possible cause of the gap", "driven by free-shipping orders")
    llm = FakeLLM(causal, GOOD_EN)
    out = generate_summary(FACTS, llm, "en")
    assert out.status == "verified" and len(llm.calls) == 2
    assert any("causal claim without a cited profit driver or a hedge" in p for p in out.log["attempts"][0]["problems"])
    assert "causal claim without a cited profit driver or a hedge" in llm.calls[1][1]


def test_margin_written_as_percent_fails_both_attempts() -> None:
    bad = GOOD_EN.replace("rose 2.84 points", "rose 2.84%")
    out = generate_summary(FACTS, FakeLLM(bad, bad), "en")
    assert out.status == "failed_number_check"
    assert any(u["reason"].startswith("unit:") for u in out.log["attempts"][0]["unmatched_numbers"])


def test_arabic_margin_written_as_percent_fails() -> None:
    bad = GOOD_AR.replace("2.84 نقطة", "2.84%")
    out = generate_summary(FACTS, FakeLLM(bad, bad), "ar")
    assert out.status == "failed_number_check"


def test_summary_missing_top_insight_fails() -> None:
    lines = GOOD_EN.splitlines()
    no_top = "\n".join([lines[0], *lines[2:]])  # drop the bullet with the top driver (7,491.87)
    out = generate_summary(FACTS, FakeLLM(no_top, no_top), "en")
    assert out.status == "failed_number_check"
    assert any("top insight" in p for p in out.log["attempts"][0]["problems"])


def test_vague_action_fails() -> None:
    vague = GOOD_EN.replace("add the missing cost rows for ACC-006 and HOM-006", "improve profitability overall")
    out = generate_summary(FACTS, FakeLLM(vague, vague), "en")
    assert any("recommended action" in p for p in out.log["attempts"][0]["problems"])
