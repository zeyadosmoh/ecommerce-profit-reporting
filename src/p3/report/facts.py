"""Build facts.json for one ISO week: every number the report and the LLM may use, precomputed from the marts.

Rules:
* amounts are SAR ex-VAT, rounded to 2 dp; percentages to 1 dp (KPI changes) or 2 dp (margins);
* every change carries a precomputed `direction` (up / down / flat) so the verifier can check wording;
* the LLM receives only this aggregated weekly view: no customer, address or order-level personal data.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import psycopg

from p3 import SYNTHETIC_NOTICE

TREND_WEEKS = 8
MOVERS_WINDOW_WEEKS = 4
MOVERS_TOP_N = 3
FREE_SHIPPING_THRESHOLD_INCL_VAT = 299

CENT, TENTH = Decimal("0.01"), Decimal("0.1")


class FactsError(ValueError):
    pass


@dataclass(frozen=True)
class Kpi:
    key: str
    column: str
    label: str
    unit: str  # SAR | count | pct
    good_when: str  # up | down: only for colouring in the report


KPIS = [
    Kpi("net_revenue", "net_revenue_ex_vat", "Net revenue after refunds", "SAR", "up"),
    Kpi("net_profit", "net_profit_known_cost", "Net profit (known cost)", "SAR", "up"),
    Kpi("net_margin_pct_known_cost", "net_margin_pct_known_cost", "Net margin (known cost)", "pct", "up"),
    Kpi("orders", "orders", "Orders", "count", "up"),
    Kpi("aov_ex_vat_products", "aov_ex_vat_products", "AOV (products, ex-VAT)", "SAR", "up"),
    Kpi("units_sold", "units_sold", "Units sold", "count", "up"),
    Kpi("discounts", "discounts_ex_vat", "Discounts", "SAR", "down"),
    Kpi("product_refunds", "product_refunds_ex_vat", "Product refunds", "SAR", "down"),
    Kpi("cogs", "cogs_known_cost", "COGS (known cost)", "SAR", "down"),
    Kpi("payment_fees", "payment_fees", "Payment fees", "SAR", "down"),
    Kpi("missing_cost_revenue", "missing_cost_net_revenue_ex_vat", "Revenue excluded (missing cost)", "SAR", "down"),
]

BRIDGE = {
    "revenue": [
        ("", "gross_sales_ex_vat", "Gross sales"),
        ("-", "discounts_ex_vat", "Discounts"),
        ("=", "net_sales_ex_vat", "Net sales"),
        ("-", "product_refunds_ex_vat", "Product refunds"),
        ("=", "net_revenue_ex_vat", "Net revenue after refunds"),
        ("-", "missing_cost_net_revenue_ex_vat", "Excluded: missing-cost lines"),
        ("=", "net_revenue_known_cost_ex_vat", "Net revenue after refunds (known cost)"),
    ],
    "profit": [
        ("", "net_revenue_known_cost_ex_vat", "Net revenue after refunds (known cost)"),
        ("-", "cogs_known_cost", "COGS"),
        ("=", "gross_profit_known_cost", "Gross profit"),
        ("+", "shipping_charged_known_cost_ex_vat", "Shipping charged"),
        ("-", "shipping_refunds_known_cost_ex_vat", "Shipping refunded"),
        ("-", "shipping_cost_known_cost_ex_vat", "Shipping cost"),
        ("-", "payment_fees_known_cost", "Payment fees"),
        ("=", "net_profit_known_cost", "Net profit (known cost)"),
    ],
    "excluded": [
        ("", "missing_cost_net_revenue_ex_vat", "Revenue after refunds"),
        ("", "missing_cost_shipping_charged_ex_vat", "Allocated shipping charged"),
        ("", "missing_cost_shipping_refunds_ex_vat", "Allocated shipping refunded"),
        ("", "missing_cost_shipping_cost_ex_vat", "Allocated shipping cost"),
        ("", "missing_cost_payment_fees", "Allocated payment fees"),
    ],
}


def money(value: object) -> float | None:
    return None if value is None else float(Decimal(value).quantize(CENT, ROUND_HALF_UP))


def direction(delta: Decimal) -> str:
    return "up" if delta > 0 else "down" if delta < 0 else "flat"


def change(current: object, previous: object, pct: bool = True) -> dict:
    """Absolute and relative change with a precomputed direction. `pct=False` for percentage metrics
    (their absolute change is already in percentage points)."""
    cur, prev = Decimal(current or 0).quantize(CENT), Decimal(previous or 0).quantize(CENT)
    delta = cur - prev
    out = {"abs": float(delta), "direction": direction(delta)}
    if pct:
        out["pct"] = float((delta / abs(prev) * 100).quantize(TENTH, ROUND_HALF_UP)) if prev else None
    return out


def _rows(conn: psycopg.Connection, sql: str, params: dict | None = None) -> list[dict]:
    cur = conn.execute(sql, params or {})
    names = [c.name for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def last_full_week(conn: psycopg.Connection) -> date:
    row = conn.execute("SELECT max(week_start) FROM marts.agg_weekly_kpis WHERE is_full_week").fetchone()
    if not row or row[0] is None:
        raise FactsError("no full week in marts.agg_weekly_kpis; run `python -m p3 dbt build` first")
    return row[0]


def build_facts(conn: psycopg.Connection, week_start: date) -> dict:
    if week_start.isoweekday() != 1:
        raise FactsError(f"{week_start} is not a Monday (ISO weeks start on Monday)")
    weeks = {r["week_start"]: r for r in _rows(conn, "SELECT * FROM marts.agg_weekly_kpis")}
    week = weeks.get(week_start)
    if week is None or not week["is_full_week"]:
        raise FactsError(f"{week_start} is not a full week in marts.agg_weekly_kpis")
    prev_start = week_start - timedelta(days=7)
    prev = weeks.get(prev_start)
    if prev is None:
        raise FactsError(f"no previous week ({prev_start}) to compare with")
    iso = week_start.isocalendar()

    facts: dict = {
        "notice": SYNTHETIC_NOTICE,
        "currency": "SAR",
        "amounts_basis": "ex-VAT (Saudi VAT 15% excluded from revenue and costs)",
        "week": {
            "start": week_start.isoformat(),
            "end": (week_start + timedelta(days=6)).isoformat(),
            "iso_week": f"{iso.year}-W{iso.week:02d}",
            "iso_week_number": iso.week,
            "previous_start": prev_start.isoformat(),
            "previous_end": (prev_start + timedelta(days=6)).isoformat(),
        },
        "constants": {
            "trend_weeks": TREND_WEEKS,
            "movers_window_weeks": MOVERS_WINDOW_WEEKS,
            "free_shipping_threshold_incl_vat": FREE_SHIPPING_THRESHOLD_INCL_VAT,
        },
        "kpis": {},
        "bridge": {},
        "trend": [],
    }
    for k in KPIS:
        is_pct = k.unit == "pct"
        facts["kpis"][k.key] = {
            "label": k.label,
            "unit": k.unit,
            "good_when": k.good_when,
            "this_week": money(week[k.column]) if k.unit != "count" else int(week[k.column]),
            "previous_week": money(prev[k.column]) if k.unit != "count" else int(prev[k.column]),
            "change": change(week[k.column], prev[k.column], pct=not is_pct),
        }
    facts["kpis"]["missing_cost_lines"] = {
        "label": "Missing-cost lines", "unit": "count", "good_when": "down",
        "this_week": int(week["missing_cost_lines"]), "previous_week": int(prev["missing_cost_lines"]),
        "change": change(week["missing_cost_lines"], prev["missing_cost_lines"]),
    }
    for block, rows in BRIDGE.items():
        facts["bridge"][block] = [
            {"op": op, "key": col, "label": label, "this_week": money(week[col]), "previous_week": money(prev[col])}
            for op, col, label in rows
        ]

    trend_starts = [week_start - timedelta(days=7 * i) for i in range(TREND_WEEKS - 1, -1, -1)]
    facts["trend"] = [
        {"week_start": ws.isoformat(), "net_revenue": money(weeks[ws]["net_revenue_ex_vat"]),
         "net_profit_known_cost": money(weeks[ws]["net_profit_known_cost"]),
         "net_margin_pct_known_cost": money(weeks[ws]["net_margin_pct_known_cost"])}
        for ws in trend_starts if ws in weeks
    ]
    facts["movers"] = build_movers(conn, week_start)
    facts["shipping"] = build_shipping(conn, week_start, prev_start)
    facts["missing_cost"] = build_missing_cost(conn, week_start, week)
    facts["data_quality"] = build_data_quality(conn, week_start)
    return facts


def build_movers(conn: psycopg.Connection, week_start: date) -> dict:
    """Net profit (known cost) this week vs the average of the previous 4 weeks, per product.
    A week without sales counts as 0. Products with any missing-cost revenue in the window are listed
    separately (their profit is unknown) with their revenue change only."""
    window_start = week_start - timedelta(days=7 * MOVERS_WINDOW_WEEKS)
    rows = _rows(conn, """
        SELECT p.sku, max(p.product_name) AS product_name,
               coalesce(sum(p.net_profit_known_cost) FILTER (WHERE p.week_start = %(w)s), 0) AS profit_now,
               coalesce(sum(p.net_profit_known_cost) FILTER (WHERE p.week_start < %(w)s), 0) AS profit_prev_sum,
               coalesce(sum(p.net_revenue_ex_vat) FILTER (WHERE p.week_start = %(w)s), 0) AS revenue_now,
               coalesce(sum(p.net_revenue_ex_vat) FILTER (WHERE p.week_start < %(w)s), 0) AS revenue_prev_sum,
               coalesce(sum(p.missing_cost_net_revenue_ex_vat), 0) <> 0
                 OR bool_or(p.missing_cost_lines > 0) AS has_missing_cost
        FROM marts.agg_product_weekly AS p
        WHERE p.week_start >= %(from)s AND p.week_start <= %(w)s
        GROUP BY p.sku""", {"w": week_start, "from": window_start})
    known, missing = [], []
    for r in rows:
        profit_avg = (Decimal(r["profit_prev_sum"]) / MOVERS_WINDOW_WEEKS).quantize(CENT, ROUND_HALF_UP)
        revenue_avg = (Decimal(r["revenue_prev_sum"]) / MOVERS_WINDOW_WEEKS).quantize(CENT, ROUND_HALF_UP)
        item = {
            "sku": r["sku"],
            "product": r["product_name"],
            "net_revenue_this_week": money(r["revenue_now"]),
            "net_revenue_prev4_avg": money(revenue_avg),
            "revenue_change": change(r["revenue_now"], revenue_avg),
        }
        if r["has_missing_cost"]:
            missing.append(item)
            continue
        item.update({
            "net_profit_this_week": money(r["profit_now"]),
            "net_profit_prev4_avg": money(profit_avg),
            "profit_change": change(r["profit_now"], profit_avg),
        })
        known.append(item)
    known.sort(key=lambda m: (m["profit_change"]["abs"], m["sku"]))
    return {
        "basis": "net profit (known cost) this week vs average of previous 4 weeks",
        "risers": [m for m in reversed(known) if m["profit_change"]["abs"] > 0][:MOVERS_TOP_N],
        "fallers": [m for m in known if m["profit_change"]["abs"] < 0][:MOVERS_TOP_N],
        "missing_cost_products": sorted(missing, key=lambda m: m["sku"]),
    }


def build_shipping(conn: psycopg.Connection, week_start: date, prev_start: date) -> dict:
    rows = {r["week_start"]: r for r in _rows(conn, "SELECT * FROM marts.agg_weekly_shipping WHERE week_start IN (%(a)s, %(b)s)",
                                              {"a": week_start, "b": prev_start})}
    cur, prev = rows[week_start], rows.get(prev_start)
    return {
        "basis": "orders of the week, at sale, ex-VAT; subsidy = shipping cost - shipping charged",
        "orders": int(cur["orders"]),
        "free_shipping_orders": int(cur["free_shipping_orders"]),
        "paid_shipping_orders": int(cur["paid_shipping_orders"]),
        "shipping_charged": money(cur["shipping_charged_ex_vat"]),
        "shipping_cost": money(cur["shipping_cost_ex_vat"]),
        "subsidy": money(cur["shipping_subsidy_ex_vat"]),
        "free_shipping_part": money(cur["free_shipping_part_ex_vat"]),
        "paid_shipping_part": money(cur["paid_shipping_part_ex_vat"]),
        "subsidy_previous_week": money(prev["shipping_subsidy_ex_vat"]) if prev else None,
        "subsidy_change": change(cur["shipping_subsidy_ex_vat"], prev["shipping_subsidy_ex_vat"]) if prev else None,
    }


def build_missing_cost(conn: psycopg.Connection, week_start: date, week: dict) -> dict:
    skus = _rows(conn, """
        SELECT sku, max(product_name) AS product_name, count(*) AS lines, sum(net_revenue_after_refunds_ex_vat) AS revenue,
               min(order_date) AS first_sale
        FROM marts.fct_order_lines_profit WHERE cost_missing AND order_date <= %(end)s
        GROUP BY sku ORDER BY sku""", {"end": week_start + timedelta(days=6)})
    return {
        "lines_this_week": int(week["missing_cost_lines"]),
        "net_revenue_this_week": money(week["missing_cost_net_revenue_ex_vat"]),
        "skus": [{"sku": s["sku"], "product": s["product_name"], "lines_to_date": int(s["lines"]),
                  "net_revenue_to_date": money(s["revenue"]), "first_sale": s["first_sale"].isoformat()}
                 for s in skus],
    }


def build_data_quality(conn: psycopg.Connection, week_start: date) -> dict:
    counts = _rows(conn, """
        SELECT issue_type, count(*) AS n, coalesce(sum(amount_ex_vat), 0) AS amount
        FROM marts.dq_issues WHERE issue_week = %(w)s GROUP BY 1 ORDER BY 1""", {"w": week_start})
    rejects = conn.execute("SELECT count(*) FROM marts.dq_issues WHERE issue_type = 'etl_reject'").fetchone()[0]
    return {
        "issues_this_week": [{"issue_type": c["issue_type"], "count": int(c["n"]), "amount": money(c["amount"])}
                             for c in counts],
        "etl_rejects_all_time": int(rejects),
    }


def llm_view(facts: dict) -> dict:
    """The exact payload sent to the LLM: the facts without logs or presentation-only fields."""
    return {k: v for k, v in facts.items() if k not in ("ai_summary", "report")}
