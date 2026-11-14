"""`python -m p3 profit-summary`: last full week's KPIs, top/bottom products, missing-cost warning."""

from datetime import date
from decimal import Decimal

import psycopg

from p3 import SYNTHETIC_NOTICE

# Bridge blocks: (op, column, label). op "" starts a running total, "+"/"-" adjust it, "=" must equal it.
# Every block adds up exactly to the halala; tests/test_profit_dbt.py re-adds the printed text.
BRIDGE_BLOCKS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Revenue, all lines (ex-VAT)", [
        ("", "gross_sales_ex_vat", "Gross sales"),
        ("-", "discounts_ex_vat", "Discounts"),
        ("=", "net_sales_ex_vat", "Net sales"),
        ("-", "product_refunds_ex_vat", "Product refunds"),
        ("=", "net_revenue_ex_vat", "Net revenue after refunds"),
        ("-", "missing_cost_net_revenue_ex_vat", "Excluded: missing-cost lines"),
        ("=", "net_revenue_known_cost_ex_vat", "Net revenue after refunds (known cost)"),
    ]),
    ("Profit bridge, known-cost lines only (ex-VAT)", [
        ("", "net_revenue_known_cost_ex_vat", "Net revenue after refunds (known cost)"),
        ("-", "cogs_known_cost", "COGS"),
        ("=", "gross_profit_known_cost", "Gross profit"),
        ("+", "shipping_charged_known_cost_ex_vat", "Shipping charged"),
        ("-", "shipping_refunds_known_cost_ex_vat", "Shipping refunded"),
        ("-", "shipping_cost_known_cost_ex_vat", "Shipping cost"),
        ("-", "payment_fees_known_cost", "Payment fees"),
        ("=", "net_profit_known_cost", "Net profit (known cost)"),
    ]),
]
EXCLUDED_ROWS = [
    ("missing_cost_net_revenue_ex_vat", "Revenue after refunds"),
    ("missing_cost_shipping_charged_ex_vat", "Allocated shipping charged"),
    ("missing_cost_shipping_refunds_ex_vat", "Allocated shipping refunded"),
    ("missing_cost_shipping_cost_ex_vat", "Allocated shipping cost"),
    ("missing_cost_payment_fees", "Allocated payment fees"),
]
OTHER_ROWS = [
    ("orders", "Orders", "int"),
    ("units_sold", "Units sold", "int"),
    ("units_refunded", "Units refunded", "int"),
    ("shipping_refunds_ex_vat", "Shipping refunds (ex-VAT, all lines)", "sar"),
    ("aov_ex_vat_products", "aov_ex_vat_products", "sar"),
    ("missing_cost_lines", "Missing-cost lines", "int"),
]


def fmt(value: object, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "int":
        return f"{int(value):,}"
    if kind == "pct":
        return f"{Decimal(value):.2f}%"
    return f"SAR {Decimal(value):,.2f}"


def _rows(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [c.name for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def build_profit_summary(conn: psycopg.Connection, week_start: date | None = None) -> str:
    weeks = _rows(conn, """SELECT * FROM marts.agg_weekly_kpis
                           WHERE is_full_week AND (%(w)s::date IS NULL OR week_start <= %(w)s::date)
                           ORDER BY week_start DESC LIMIT 2""", {"w": week_start})
    if not weeks or (week_start and weeks[0]["week_start"] != week_start):
        return "No such full week in marts.agg_weekly_kpis - run `python -m p3 dbt build` first."
    week, prev = weeks[0], (weeks[1] if len(weeks) > 1 else None)

    def row(op: str, label: str, col: str, kind: str = "sar") -> str:
        prev_val = fmt(prev[col], kind) if prev else ""
        return f"  {op:1} {label:<40}{fmt(week[col], kind):>16}{prev_val:>16}"

    out = [SYNTHETIC_NOTICE, "",
           f"Week: {week['week_start']} (Mon) to {week['week_end']} (Sun), ISO week, SAR",
           f"    {'':<40}{'this week':>16}{'previous week':>16}"]
    for title, rows in BRIDGE_BLOCKS:
        out += ["", f"  {title}"] + [row(op, label, col) for op, col, label in rows]
    out.append(row(" ", "net_margin_pct_known_cost", "net_margin_pct_known_cost", "pct"))
    out.append(f"    -> excludes {fmt(week['missing_cost_net_revenue_ex_vat'], 'sar')} of missing-cost revenue "
               f"({week['missing_cost_lines']} lines) this week")
    out += ["", "  Excluded from profit (missing-cost lines, ex-VAT)"]
    out += [row(" ", label, col) for col, label in EXCLUDED_ROWS]
    out += ["", "  Other"] + [row(" ", label, col, kind) for col, label, kind in OTHER_ROWS]

    products = _rows(conn, """
        SELECT sku, max(product_name) AS product_name, sum(units_sold) AS units,
               sum(net_revenue_ex_vat) AS net_revenue, sum(net_profit_known_cost) AS net_profit,
               round(100 * sum(net_profit_known_cost) / nullif(sum(net_revenue_known_cost_ex_vat), 0), 2) AS margin
        FROM marts.agg_product_weekly GROUP BY sku
        HAVING sum(net_profit_known_cost) IS NOT NULL
        ORDER BY net_profit DESC""")
    period = conn.execute("SELECT min(week_start), max(week_end) FROM marts.agg_weekly_kpis").fetchone()
    for title, subset in (("Top 5", products[:5]), ("Bottom 5", list(reversed(products[-5:])))):
        out += ["", f"{title} products by net profit, whole period {period[0]} to {period[1]} (known-cost lines):",
                f"  {'sku':<9}{'product':<30}{'units':>7}{'net revenue':>16}{'net profit':>16}{'margin':>9}"]
        out += [f"  {p['sku']:<9}{p['product_name'][:29]:<30}{p['units']:>7}{fmt(p['net_revenue'], 'sar'):>16}"
                f"{fmt(p['net_profit'], 'sar'):>16}{fmt(p['margin'], 'pct'):>9}" for p in subset]

    missing = _rows(conn, """
        SELECT sku, max(product_name) AS product_name, count(*) AS lines, sum(quantity) AS units,
               sum(net_revenue_after_refunds_ex_vat) AS revenue, min(order_date) AS first_sale
        FROM marts.fct_order_lines_profit WHERE cost_missing GROUP BY sku ORDER BY revenue DESC""")
    out.append("")
    if missing:
        total = sum(m["revenue"] for m in missing)
        out.append(f"WARNING - missing cost: {sum(m['lines'] for m in missing)} order lines, {fmt(total, 'sar')} "
                   "of net revenue are EXCLUDED from profit (never treated as zero cost). Add these SKUs to the cost sheet:")
        out += [f"  {m['sku']:<9}{m['product_name'][:29]:<30}{m['lines']:>5} lines {m['units']:>5} units "
                f"{fmt(m['revenue'], 'sar'):>14}  selling since {m['first_sale']}" for m in missing]
    else:
        out.append("Missing cost: none - every sold SKU has a cost row.")
    return "\n".join(out)
