"""`python -m p3 report`: facts.json + report.html + report.pdf and/or report.pptx for one ISO week."""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

from p3.config import Settings
from p3.llm.base import LLMClient
from p3.llm.factory import get_llm_client, prices
from p3.report.deck import write_deck
from p3.report.facts import build_facts, last_full_week
from p3.report.formats import parse_formats
from p3.report.i18n import STRINGS
from p3.report.render import SummaryView, render_html, write_pdf
from p3.summary.generate import SummaryOutcome, generate_summary


@dataclass
class ReportFiles:
    directory: Path
    facts: Path
    html: Path | None
    pdf: Path | None
    preview: Path | None
    summary: SummaryOutcome | None  # None when re-rendered from an existing facts.json
    pptx: Path | None = None

    @property
    def written(self) -> list[Path]:
        return [p for p in (self.facts, self.html, self.pdf, self.pptx, self.preview) if p]


def report_paths(out_dir: Path, iso_week: str, lang: str) -> tuple[Path, Path, Path, Path]:
    suffix = "" if lang == "en" else f".{lang}"
    d = out_dir / iso_week
    return d, d / f"facts{suffix}.json", d / f"report{suffix}.html", d / f"report{suffix}.pdf"


def build_report(conn: psycopg.Connection, settings: Settings, week: date | None = None, lang: str | None = None,
                 use_ai: bool = True, client: LLMClient | None = None, out_dir: Path | None = None,
                 preview: bool = False, formats: str | tuple[str, ...] = ("pdf",)) -> ReportFiles:
    formats = parse_formats(formats)
    lang = (lang or settings.report_lang or "en").strip().lower()
    if lang not in STRINGS:
        raise ValueError(f"unsupported language {lang!r}; expected en or ar")
    facts = build_facts(conn, week or last_full_week(conn))

    reason = None
    if client is None and use_ai:
        client, reason = get_llm_client(settings)
    elif not use_ai:
        client, reason = None, "AI disabled with --no-ai"
    outcome = generate_summary(facts, client, lang, no_client_reason=reason, prices=prices(settings))

    directory, facts_path, html_path, pdf_path = report_paths(out_dir or settings.reports_dir, facts["week"]["iso_week"], lang)
    names = [p.name for p in (html_path, pdf_path) if "pdf" in formats]
    names += [pdf_path.with_suffix(".pptx").name] if "pptx" in formats else []
    names += [_preview_path(directory, lang).name] if preview and "pdf" in formats else []
    facts_out = {**facts,
                 "ai_summary": {**outcome.log, "bullets": outcome.bullets, "notice": outcome.notice},
                 "report": {"language": lang, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                            "files": names}}
    html, pdf, pptx, png = write_outputs(facts_out, directory, lang, formats, preview)
    facts_path.write_text(json.dumps(facts_out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return ReportFiles(directory, facts_path, html, pdf, png, outcome, pptx)


def summary_label(ai: dict, lang: str) -> str:
    """The PDF's summary source line, from facts.json's ai_summary."""
    t = STRINGS[lang]
    if ai.get("source") == "llm":
        return f"{t['summary_ai']} · {ai.get('provider')} {ai.get('model')}"
    return t["summary_template"] + (f" · {ai['reason']}" if ai.get("reason") else "")


def _preview_path(directory: Path, lang: str) -> Path:
    return directory / f"preview{'' if lang == 'en' else '.' + lang}.png"


def write_outputs(facts_out: dict, directory: Path, lang: str, formats: str | tuple[str, ...] = ("pdf",),
                  preview: bool = False) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    """Render report.html/.pdf and/or report.pptx from a finished facts dict (no DB, no AI call)."""
    formats = parse_formats(formats)
    _, _, html_path, pdf_path = report_paths(directory.parent, directory.name, lang)
    directory.mkdir(parents=True, exist_ok=True)
    html_out = pdf_out = pptx_out = png_out = None
    if "pdf" in formats:
        ai = facts_out.get("ai_summary") or {}
        html = render_html(facts_out, SummaryView(list(ai.get("bullets") or []), summary_label(ai, lang),
                                                  ai.get("notice")), lang)
        html_path.write_text(html, encoding="utf-8")
        png_out = _preview_path(directory, lang) if preview else None
        write_pdf(html, pdf_path, png_out)
        html_out, pdf_out = html_path, pdf_path
    if "pptx" in formats:
        pptx_out = write_deck(facts_out, pdf_path.with_suffix(".pptx"), lang)
    return html_out, pdf_out, pptx_out, png_out


def render_from_facts(facts_path: Path, formats: str | tuple[str, ...] = ("pdf",), preview: bool = False,
                      lang: str | None = None) -> ReportFiles:
    """Re-render the outputs next to an existing facts.json; facts.json itself is not rewritten."""
    facts_out = json.loads(facts_path.read_text(encoding="utf-8"))
    lang = lang or (facts_out.get("report") or {}).get("language") or (facts_out.get("ai_summary") or {}).get("language") or "en"
    if lang not in STRINGS:
        raise ValueError(f"unsupported language {lang!r}; expected en or ar")
    html, pdf, pptx, png = write_outputs(facts_out, facts_path.parent, lang, formats, preview)
    return ReportFiles(facts_path.parent, facts_path, html, pdf, png, None, pptx)
