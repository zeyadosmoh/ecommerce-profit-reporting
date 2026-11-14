"""Gemini client using the official `google-genai` SDK, configured like invoice-extraction:
SDK retries disabled (the summary flow owns its single regeneration), key redacted from errors."""

from google import genai
from google.genai import errors, types

from p3.llm.base import LLMError, LLMResponse, redact


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, temperature: float | None = None, timeout_s: float = 60.0) -> None:
        self.model = model
        self._key = api_key
        self._temperature = temperature
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000),
                                           retry_options=types.HttpRetryOptions(attempts=1)),
        )

    def generate(self, system: str, user: str) -> LLMResponse:
        config = types.GenerateContentConfig(system_instruction=system, temperature=self._temperature)
        try:
            resp = self._client.models.generate_content(model=self.model, contents=user, config=config)
        except errors.APIError as e:
            raise LLMError(redact(f"{e.code} {e.status}: {e.message}", self._key)) from None
        except Exception as e:  # network errors etc.; never chain (tracebacks could carry request data)
            raise LLMError(redact(f"{type(e).__name__}: {e}", self._key)) from None
        usage = resp.usage_metadata
        candidates = (usage.candidates_token_count or 0) if usage else 0
        thoughts = (usage.thoughts_token_count or 0) if usage else 0
        return LLMResponse(
            text=resp.text or "",
            model=self.model,
            input_tokens=usage.prompt_token_count if usage else None,
            output_tokens=(candidates + thoughts) if usage else None,
            extra={"thoughts_tokens": thoughts, "model_version": getattr(resp, "model_version", None)},
        )
