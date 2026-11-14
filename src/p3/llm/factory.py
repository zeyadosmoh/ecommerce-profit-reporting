"""Pick the LLM client from settings. Returns (None, reason) when no usable key: the caller then uses the
deterministic template summary instead of failing."""

from decimal import Decimal, InvalidOperation

from p3.config import Settings
from p3.llm.base import LLMClient

PLACEHOLDERS = {"", "your_api_key_here", "change-me"}


def api_key(settings: Settings) -> str | None:
    for value in (settings.gemini_api_key, settings.llm_api_key):
        if value.strip() not in PLACEHOLDERS:
            return value.strip()
    return None


def get_llm_client(settings: Settings) -> tuple[LLMClient | None, str | None]:
    provider = settings.llm_provider.strip().lower() or "gemini"
    if provider != "gemini":
        return None, f"LLM_PROVIDER {provider!r} is not implemented (only gemini)"
    key = api_key(settings)
    if key is None:
        return None, "no API key (GEMINI_API_KEY / LLM_API_KEY not set)"
    from p3.llm.gemini import GeminiClient  # lazy: tests never import the SDK client path

    temperature = float(settings.llm_temperature) if settings.llm_temperature.strip() else None
    return GeminiClient(key, settings.llm_model.strip() or "gemini-3.5-flash-lite", temperature), None


def prices(settings: Settings) -> tuple[Decimal, Decimal] | None:
    """USD per million input / output tokens, or None when pricing is not configured."""
    try:
        if settings.llm_price_input_per_mtok.strip() and settings.llm_price_output_per_mtok.strip():
            return Decimal(settings.llm_price_input_per_mtok), Decimal(settings.llm_price_output_per_mtok)
    except InvalidOperation:
        pass
    return None
