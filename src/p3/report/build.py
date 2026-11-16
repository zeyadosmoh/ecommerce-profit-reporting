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
    summary: SummaryOutcome
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

    t = STRINGS[lang]
    if outcome.source == "llm":
        label = f"{t['summary_ai']} · {outcome.log.get('provider')} {outcome.log.get('model')}"
    else:
        label = t["summary_template"] + (f" · {outcome.log.get('reason')}" if outcome.log.get("reason") else "")
    directory, facts_path, html_path, pdf_path = report_paths(out_dir or settings.reports_dir, facts["week"]["iso_week"], lang)
    pptx_path = pdf_path.with_suffix(".pptx")
    directory.mkdir(parents=True, exist_ok=True)
    preview_path = directory / f"preview{'' if lang == 'en' else '.' + lang}.png" if preview and "pdf" in formats else None
    if "pdf" in formats:
        html = render_html(facts, SummaryView(outcome.bullets, label, outcome.notice), lang)
        html_path.write_text(html, encoding="utf-8")
        write_pdf(html, pdf_path, preview_path)
    else:
        html_path = pdf_path = None
    files = [p.name for p in (html_path, pdf_path, pptx_path if "pptx" in formats else None, preview_path) if p]
    facts_out = {**facts,
                 "ai_summary": {**outcome.log, "bullets": outcome.bullets, "notice": outcome.notice},
                 "report": {"language": lang, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                            "files": files}}
    if "pptx" in formats:
        write_deck(facts_out, pptx_path, lang)  # built only from the facts written next to it
    else:
        pptx_path = None
    facts_path.write_text(json.dumps(facts_out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return ReportFiles(directory, facts_path, html_path, pdf_path, preview_path, outcome, pptx_path)
