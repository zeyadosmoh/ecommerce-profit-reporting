"""8-week trend charts as PNGs (matplotlib, Agg backend, deterministic output): raw bytes for the deck, data URIs for HTML."""

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

INK, MUTED, ACCENT, ACCENT_SOFT, GRID = "#1f2933", "#7b8794", "#2f6f5e", "#a7cbbf", "#e4e7eb"


def _png(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight", metadata={"Software": None})
    plt.close(fig)
    return buf.getvalue()


def _axes(labels: list[str]) -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(3.6, 2.0))
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=7, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_xticks(range(len(labels)), labels)
    return fig, ax


def trend_chart_pngs(trend: list[dict]) -> dict[str, bytes]:
    labels = [t["week_start"][5:] for t in trend]  # MM-DD of the Monday
    last = len(trend) - 1
    thousands = FuncFormatter(lambda v, _: f"{v / 1000:.0f}k")

    fig, ax = _axes(labels)
    values = [t["net_revenue"] or 0 for t in trend]
    ax.bar(range(len(values)), values, color=[ACCENT if i == last else ACCENT_SOFT for i in range(len(values))], width=0.65)
    ax.yaxis.set_major_formatter(thousands)
    revenue = _png(fig)

    fig, ax = _axes(labels)
    values = [t["net_profit_known_cost"] or 0 for t in trend]
    ax.plot(range(len(values)), values, color=ACCENT, linewidth=1.8, marker="o", markersize=3)
    ax.plot([last], [values[last]], marker="o", markersize=5, color=INK)
    ax.yaxis.set_major_formatter(thousands)
    ax.set_ylim(bottom=0)
    profit = _png(fig)

    fig, ax = _axes(labels)
    values = [t["net_margin_pct_known_cost"] or 0 for t in trend]
    ax.plot(range(len(values)), values, color=INK, linewidth=1.8, marker="o", markersize=3)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_ylim(min(values) - 5, max(values) + 5)
    margin = _png(fig)
    return {"net_revenue": revenue, "net_profit": profit, "margin": margin}


def trend_charts(trend: list[dict]) -> dict[str, str]:
    return {k: "data:image/png;base64," + base64.b64encode(v).decode() for k, v in trend_chart_pngs(trend).items()}
