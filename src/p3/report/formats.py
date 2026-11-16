"""Output formats for `p3 report --format` (kept import-light for the CLI parser)."""

FORMATS = ("pdf", "pptx")


def parse_formats(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Parse "pdf", "pptx" or "pdf,pptx" into an ordered tuple; raise ValueError on anything else."""
    items = value.split(",") if isinstance(value, str) else list(value or ["pdf"])
    chosen = {i.strip().lower() for i in items if i.strip()}
    unknown = chosen - set(FORMATS)
    if unknown or not chosen:
        raise ValueError(f"unknown report format(s) {sorted(unknown) or value!r}; expected pdf, pptx or pdf,pptx")
    return tuple(f for f in FORMATS if f in chosen)
