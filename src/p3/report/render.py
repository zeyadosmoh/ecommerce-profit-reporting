"""Render report.html (self-contained) and report.pdf (Playwright Chromium) from facts + summary."""

import base64
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from p3.report.charts import trend_charts
from p3.report.i18n import BRIDGE_LABELS_AR, ISSUE_LABELS, KPI_LABELS_AR, MAIN_KPIS, STRINGS

PACKAGE = files("p3.report")


@dataclass
class SummaryView:
    bullets: list[str]
    source_label: str
    notice: str | None = None


def money(v: float | None) -> str:
    return "n/a" if v is None else f"{v:,.2f}"


def signed(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+,.2f}"


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


def arrow(direction: str) -> str:
    return {"up": "▲", "down": "▼"}.get(direction, "■")


def fmt_value(v: float | int | None, unit: str) -> str:
    if v is None:
        return "n/a"
    return {"count": f"{v:,}", "pct": f"{v:.2f}%"}.get(unit, f"SAR {v:,.2f}")


def fmt_change(k: dict) -> str:
    c = k["change"]
    if k["unit"] == "pct":
        return f"{c['abs']:+.2f} pp"
    absolute = f"{c['abs']:+,}" if k["unit"] == "count" else f"{c['abs']:+,.2f}"
    return f"{absolute} ({pct(c.get('pct'))})"


def tone(k: dict) -> str:
    d = k["change"]["direction"]
    if d == "flat":
        return "flat"
    return "good" if d == k["good_when"] else "bad"


def font_css(lang: str) -> str:
    if lang != "ar":
        return ""
    css = []
    for weight, name in (("normal", "Amiri-Regular.ttf"), ("bold", "Amiri-Bold.ttf")):
        data = base64.b64encode(PACKAGE.joinpath("assets/fonts", name).read_bytes()).decode()
        css.append(f'@font-face {{ font-family: "Amiri"; font-weight: {weight}; '
                   f'src: url(data:font/ttf;base64,{data}) format("truetype"); }}')
    return "\n".join(css)


def render_html(facts: dict, summary: SummaryView, lang: str = "en") -> str:
    env = Environment(loader=FileSystemLoader(str(PACKAGE.joinpath("templates"))),
                      autoescape=select_autoescape(["html", "j2"]), undefined=StrictUndefined)
    ar = lang == "ar"
    template = env.get_template("report.html.j2")
    return template.render(
        f=facts, t=STRINGS[lang], lang=lang, summary=summary, main_kpis=MAIN_KPIS,
        charts=trend_charts(facts["trend"]), font_css=font_css(lang),
        money=money, signed=signed, pct=pct, arrow=arrow, fmt_value=fmt_value, fmt_change=fmt_change, tone=tone,
        kpi_label=lambda key, k: KPI_LABELS_AR.get(key, k["label"]) if ar else k["label"],
        bridge_label=lambda label: BRIDGE_LABELS_AR.get(label, label) if ar else label,
        issue_label=lambda it: ISSUE_LABELS[lang].get(it, it),
    )


def write_pdf(html: str, pdf_path: Path, preview_png: Path | None = None) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1.5)
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            page.pdf(path=str(pdf_path), format="A4", print_background=True,
                     margin={"top": "14mm", "bottom": "16mm", "left": "13mm", "right": "13mm"})
            if preview_png is not None:
                page.screenshot(path=str(preview_png), clip={"x": 0, "y": 0, "width": 794, "height": 1123})
        finally:
            browser.close()
