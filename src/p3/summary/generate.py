"""AI summary with verified numbers: LLM -> verify -> (one regeneration with the bad numbers) -> template.

The LLM receives only `llm_view(facts)`: aggregated weekly numbers, never order-level or personal data.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal

from p3.llm.base import LLMClient, LLMError, load_prompt
from p3.report.facts import llm_view
from p3.summary.template import ACTION_LABEL, POSSIBLE_WORD, template_summary
from p3.summary.verify import Verification, parse_bullets, verify_summary

MIN_BULLETS, MAX_BULLETS = 4, 6
LANGUAGES = {"en": "English", "ar": "Arabic"}
POSSIBLE_EXAMPLE = {"en": "a possible cause is the free-shipping threshold",
                    "ar": "سبب محتمل: حدّ الشحن المجاني"}
NOTICE_FAILED = "AI summary unavailable: failed number check"
NOTICE_ERROR = "AI summary unavailable: LLM error"


@dataclass
class SummaryOutcome:
    bullets: list[str]
    source: str  # llm | template
    status: str  # verified | failed_number_check | llm_error | no_llm
    notice: str | None
    log: dict = field(default_factory=dict)


def build_system_prompt(lang: str) -> tuple[str, dict]:
    prompt = load_prompt()
    language_rules = load_prompt("language_ar.md").text if lang == "ar" else ""
    text = prompt.text.format(bullet_min=MIN_BULLETS, bullet_max=MAX_BULLETS, language_name=LANGUAGES[lang],
                              action_label=ACTION_LABEL[lang], possible_word=POSSIBLE_WORD[lang],
                              possible_example=POSSIBLE_EXAMPLE[lang], language_rules=language_rules)
    return text, {"prompt_version": prompt.version, "prompt_sha256": prompt.sha256}


def build_user_message(facts: dict, previous: str | None = None, verification: Verification | None = None) -> str:
    message = "facts.json:\n```json\n" + json.dumps(llm_view(facts), ensure_ascii=False, indent=1) + "\n```"
    if previous is not None and verification is not None:
        bad = [f"- {n.text!r}: {n.reason}" for n in verification.failures] + [f"- {p}" for p in verification.problems]
        message += ("\n\nYour previous summary failed the number check. Problems:\n" + "\n".join(bad) +
                    "\n\nPrevious summary:\n" + previous +
                    "\n\nRewrite the summary. Use only numbers that appear in facts.json, with their direction.")
    return message


def generate_summary(facts: dict, client: LLMClient | None, lang: str = "en", no_client_reason: str | None = None,
                     prices: tuple[Decimal, Decimal] | None = None, max_attempts: int = 2) -> SummaryOutcome:
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language {lang!r}; expected one of {sorted(LANGUAGES)}")
    system, prompt_meta = build_system_prompt(lang)
    log: dict = {"language": lang, "provider": getattr(client, "provider", None), "model": getattr(client, "model", None),
                 **prompt_meta, "attempts": []}

    def fallback(status: str, notice: str | None, reason: str | None) -> SummaryOutcome:
        bullets = template_summary(facts, lang)
        check = verify_summary("\n".join(f"- {b}" for b in bullets), facts, MIN_BULLETS, MAX_BULLETS, ACTION_LABEL[lang])
        log.update({"source": "template", "status": status, "reason": reason, "template_verification": check.as_log()})
        _add_usage(log, prices)
        return SummaryOutcome(bullets, "template", status, notice, log)

    if client is None:
        return fallback("no_llm", None, no_client_reason or "no LLM client configured")

    previous, verification = None, None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.generate(system, build_user_message(facts, previous, verification))
        except LLMError as exc:
            log["attempts"].append({"attempt": attempt, "error": str(exc)})
            return fallback("llm_error", NOTICE_ERROR, str(exc))
        verification = verify_summary(response.text, facts, MIN_BULLETS, MAX_BULLETS, ACTION_LABEL[lang])
        log["model"] = response.model
        log["attempts"].append({"attempt": attempt, "input_tokens": response.input_tokens,
                                "output_tokens": response.output_tokens, **verification.as_log(),
                                "text": response.text})
        if verification.passed:
            log.update({"source": "llm", "status": "verified", "reason": None})
            _add_usage(log, prices)
            return SummaryOutcome(parse_bullets(response.text), "llm", "verified", None, log)
        previous = response.text
    return fallback("failed_number_check", NOTICE_FAILED, f"number check failed after {max_attempts} attempts")


def _add_usage(log: dict, prices: tuple[Decimal, Decimal] | None) -> None:
    tokens_in = sum(a.get("input_tokens") or 0 for a in log["attempts"])
    tokens_out = sum(a.get("output_tokens") or 0 for a in log["attempts"])
    log["tokens"] = {"input": tokens_in, "output": tokens_out}
    if prices is None:
        log["cost_estimate_usd"], log["cost_note"] = None, "pricing not configured"
    else:
        cost = Decimal(tokens_in) / 1_000_000 * prices[0] + Decimal(tokens_out) / 1_000_000 * prices[1]
        log["cost_estimate_usd"] = float(cost.quantize(Decimal("0.000001")))
        log["cost_note"] = (f"USD from .env: {prices[0]} per 1M input tokens, {prices[1]} per 1M output tokens; "
                            "estimate only")
