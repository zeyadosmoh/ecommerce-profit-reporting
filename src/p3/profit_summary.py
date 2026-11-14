"""`python -m p3 profit-summary`: last full week's KPIs, top/bottom products, missing-cost warning."""

from decimal import Decimal

import psycopg

from p3 import SYNTHETIC_NOTICE

KPI_ROWS = [
    ("orders", "Orders", "int"),
    ("units_sold", "Units sold", "int"),
    ("gross_sales_ex_vat", "Gross sales (ex-VAT)", "sar"),
    ("discounts_ex_vat", "Discounts (ex-VAT)", "sar"),
    ("net_sales_ex_vat", "Net sales (ex-VAT)", "sar"),
    ("refunds_ex_vat", "Refunds (ex-VAT, incl. shipping)", "sar"),
    ("net_revenue_ex_vat", "Net revenue after refunds", "sar"),
    ("cogs_known_cost", "COGS (known-cost lines)", "sar"),
    ("gross_profit_known_cost", "Gross profit (known cost)", "sar"),
    ("shipping_charged_ex_vat", "Shipping charged (ex-VAT)", "sar"),
    ("shipping_cost_ex_vat", "Shipping cost", "sar"),
    ("payment_fees", "Payment fees", "sar"),
    ("net_profit_known_cost", "Net profit (known cost)", "sar"),
    ("net_margin_pct_known_cost", "net_margin_pct_known_cost", "pct"),
    ("aov_ex_vat_products", "aov_ex_vat_products", "sar"),
    ("missing_cost_lines", "Missing-cost lines", "int"),
    ("missing_cost_net_revenue_ex_vat", "Missing-cost revenue (excluded)", "sar"),
]


def fmt(value: object, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "int":
        return f"{int(value):,}"
    if kind == "pct":
        return f"{Decimal(value):.2f}%"
    return f"SAR {Decimal(value):,.2f}"


def _rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [c.name for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def build_profit_summary(conn: psycopg.Connection) -> str:
    weeks = _rows(conn, "SELECT * FROM marts.agg_weekly_kpis WHERE is_full_week ORDER BY week_start DESC LIMIT 2")
    if not weeks:
        return "No full week in marts.agg_weekly_kpis - run `python -m p3 dbt build` first."
    week, prev = weeks[0], (weeks[1] if len(weeks) > 1 else None)
    out = [SYNTHETIC_NOTICE, "",
           f"Last full week: {week['week_start']} (Mon) to {week['week_end']} (Sun), ISO week, ex-VAT, SAR",
           f"  {'KPI':<34}{'this week':>16}{'previous week':>16}"]
    for col, label, kind in KPI_ROWS:
        out.append(f"  {label:<34}{fmt(week[col], kind):>16}{fmt(prev[col], kind) if prev else '':>16}")
    out.append(f"  -> net_margin_pct_known_cost {fmt(week['net_margin_pct_known_cost'], 'pct')} excludes "
               f"{fmt(week['missing_cost_net_revenue_ex_vat'], 'sar')} of missing-cost revenue "
               f"({week['missing_cost_lines']} lines) this week.")

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
