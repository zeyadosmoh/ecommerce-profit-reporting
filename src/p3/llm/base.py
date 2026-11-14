"""Provider-agnostic LLM interface for the weekly summary (pattern shared with invoice-extraction)."""

import hashlib
import re
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Protocol


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None  # billed output, including thinking tokens where reported
    extra: dict = field(default_factory=dict)


class LLMClient(Protocol):
    provider: str
    model: str

    def generate(self, system: str, user: str) -> LLMResponse: ...


class LLMError(Exception):
    """Any provider failure. Messages must never contain credentials: build them with `redact`."""


class LLMConfigError(LLMError):
    pass


@dataclass(frozen=True)
class Prompt:
    text: str
    version: str
    sha256: str


def load_prompt(name: str = "weekly_summary.md") -> Prompt:
    """Load a versioned prompt. The first line must be `<!-- prompt-version: X -->`; it is stripped."""
    raw = files("p3.llm").joinpath("prompts", name).read_text(encoding="utf-8")
    m = re.match(r"\s*<!--\s*prompt-version:\s*(\S+)\s*-->\s*\n", raw)
    if not m:
        raise ValueError(f"prompt {name} must start with '<!-- prompt-version: X -->'")
    text = raw[m.end():].strip()
    return Prompt(text=text, version=m.group(1), sha256=hashlib.sha256(text.encode()).hexdigest()[:12])


def redact(text: str, *secrets: str | None) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[REDACTED]")
    return re.sub(r"AIza[0-9A-Za-z_\-]{20,}", "[REDACTED]", text)
