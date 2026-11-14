"""Phase 2 checks that need a full dbt build.

A module fixture loads the seed-42 dataset into the p3_test database (raw schema, via the in-process API),
then runs `dbt build --target test` (all models + dbt tests). Then:

* an independent cross-check recomputes whole-period net revenue, COGS and net profit from the RAW tables
  in plain Python (own allocation, own effective dating; no SQL from the dbt project) and compares to the marts;
* a golden test checks three hand-calculated orders.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

import psycopg
import pytest

from p3.config import Settings
from p3.db.connection import apply_schema, drop_schema
from p3.dbt_runner import run_dbt
from p3.generate import costs_csv, generate_dataset
from p3.generate.store import StoreConfig
from p3.ingest.pipeline import run_ingest
from p3.mock_api.store import MockStore
from tests.conftest import make_app, make_client

pytestmark = pytest.mark.dbt

D = Decimal
REVENUE_STATUSES = {"completed", "processing", "refunded"}
STORE_TZ = ZoneInfo("Asia/Riyadh")


@pytest.fixture(scope="module")
def built(settings: Settings, dsn: str, tmp_path_factory: pytest.TempPathFactory) -> psycopg.Connection:
    ds = generate_dataset(StoreConfig(seed=42))
    cost_file = tmp_path_factory.mktemp("costs") / "costs.csv"
    cost_file.write_text(costs_csv(ds.costs))
    with psycopg.connect(dsn, autocommit=True) as conn:
        drop_schema(conn)
        apply_schema(conn)
        run_ingest(dsn, make_client(make_app(MockStore(ds.products, ds.orders, ds.refunds))), str(cost_file))
        result = run_dbt(["build", "--fail-fast"], settings, target="test", quiet=True)
        assert result.returncode == 0, f"dbt build failed:\n{result.stdout[-6000:]}"
        yield conn


# --------------------------------------------------------------------------------------------------
# Independent cross-check: plain Python over raw.* tables
# --------------------------------------------------------------------------------------------------

def allocate(amount: Decimal, weights: dict[int, Decimal]) -> dict[int, Decimal]:
    """Largest-remainder allocation in integer halalas with exact fractions."""
    cents = int(amount * 100)
    total_w = sum(weights.values())
    if total_w > 0:
        shares = {k: Fraction(cents) * Fraction(w) / Fraction(total_w) for k, w in weights.items()}
    else:
        shares = {k: Fraction(cents, len(weights)) for k in weights}
    floors = {k: s.numerator // s.denominator for k, s in shares.items()}
    leftover = cents - sum(floors.values())
    order = sorted(shares, key=lambda k: (-(shares[k] - floors[k]), k))
    for k in order[:leftover]:
        floors[k] += 1
    return {k: D(v) / 100 for k, v in floors.items()}


@dataclass
class Totals:
    net_revenue: Decimal = D("0")        # product revenue ex-VAT, after refunds, all lines
    cogs: Decimal = D("0")               # known-cost lines, net of reversals
    net_profit: Decimal = D("0")         # known-cost lines
    missing_lines: int = 0
    missing_revenue: Decimal = D("0")


def python_totals(conn: psycopg.Connection) -> Totals:
    orders = {r[0]: r[1:] for r in conn.execute(
        "SELECT id, status, date_created_gmt, shipping_total, shipping_cost, payment_fee FROM raw.raw_orders")}
    lines_by_order: dict[int, list[tuple]] = defaultdict(list)
    for order_id, line_id, sku, qty, total in conn.execute(
            "SELECT order_id, line_id, sku, quantity, total FROM raw.raw_order_lines"):
        lines_by_order[order_id].append((line_id, sku, qty, total))
    costs: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for sku, eff, unit, pack in conn.execute("SELECT sku, effective_from, unit_cost, packaging_cost FROM raw.raw_costs"):
        costs[sku].append((eff, unit + pack))
    refunds = list(conn.execute("SELECT order_id, payload FROM raw.raw_refunds"))

    def landed_cost(sku: str, on: date) -> Decimal | None:
        applicable = [(eff, c) for eff, c in costs.get(sku, []) if eff <= on]
        return max(applicable)[1] if applicable else None

    t = Totals()
    line_cost: dict[int, Decimal | None] = {}
    line_weights: dict[int, dict[int, Decimal]] = {}
    for order_id, (status, created, ship_total, ship_cost, fee) in orders.items():
        if status not in REVENUE_STATUSES:
            continue
        order_day = created.astimezone(STORE_TZ).date()
        weights = {lid: total for lid, _, _, total in lines_by_order[order_id]}
        line_weights[order_id] = weights
        alloc_ship = allocate(ship_total, weights)
        alloc_cost = allocate(ship_cost or D("0"), weights)
        alloc_fee = allocate(fee or D("0"), weights)
        for line_id, sku, qty, total in lines_by_order[order_id]:
            unit = landed_cost(sku, order_day)
            line_cost[line_id] = unit
            t.net_revenue += total
            if unit is None:
                t.missing_lines += 1
                t.missing_revenue += total
                continue
            t.cogs += unit * qty
            t.net_profit += total - unit * qty + alloc_ship[line_id] - alloc_cost[line_id] - alloc_fee[line_id]

    for order_id, payload in refunds:
        if order_id not in line_weights:
            continue  # refund of an excluded order
        for item in payload["line_items"]:
            line_id = int(next(m["value"] for m in item["meta_data"] if m["key"] == "_refunded_item_id"))
            qty, revenue = -item["quantity"], -D(item["total"])
            t.net_revenue -= revenue
            unit = line_cost[line_id]
            if unit is None:
                t.missing_revenue -= revenue
                continue
            t.cogs -= unit * qty
            t.net_profit -= revenue - unit * qty  # revenue back out, units back to stock; fees not recovered
        shipping_refund = sum((-D(s["total"]) for s in payload.get("shipping_lines", [])), D("0"))
        if shipping_refund:
            for line_id, amount in allocate(shipping_refund, line_weights[order_id]).items():
                if line_cost[line_id] is not None:
                    t.net_profit -= amount
    return t


def test_python_cross_check_matches_marts_to_the_halala(built: psycopg.Connection) -> None:
    expected = python_totals(built)
    weekly = built.execute(
        """SELECT sum(net_revenue_ex_vat), sum(cogs_known_cost), sum(net_profit_known_cost),
                  sum(missing_cost_lines), sum(missing_cost_net_revenue_ex_vat)
           FROM marts.agg_weekly_kpis""").fetchone()
    lines = built.execute(
        """SELECT sum(net_revenue_after_refunds_ex_vat), sum(net_cogs), sum(net_profit),
                  count(*) FILTER (WHERE cost_missing),
                  sum(net_revenue_after_refunds_ex_vat) FILTER (WHERE cost_missing)
           FROM marts.fct_order_lines_profit""").fetchone()
    python = (expected.net_revenue, expected.cogs, expected.net_profit, expected.missing_lines, expected.missing_revenue)
    assert tuple(weekly) == python
    assert tuple(lines) == python
    assert expected.missing_lines > 0 and expected.net_profit > 0  # the check is not vacuous


# --------------------------------------------------------------------------------------------------
# Golden orders: expected numbers derived by hand from the raw seed-42 data (see comments).
# Allocation = largest remainder in halalas: floor every share, then give the leftover halala(s)
# to the line(s) with the largest fractional part.
# --------------------------------------------------------------------------------------------------

def line_row(conn: psycopg.Connection, line_id: int) -> dict:
    cur = conn.execute("SELECT * FROM marts.fct_order_lines_profit WHERE line_id = %s", (line_id,))
    return dict(zip([c.name for c in cur.description], cur.fetchone()))


def order_row(conn: psycopg.Connection, order_id: int) -> dict:
    cur = conn.execute("SELECT * FROM marts.fct_orders_profit WHERE order_id = %s", (order_id,))
    return dict(zip([c.name for c in cur.description], cur.fetchone()))


def test_golden_coupon_order_10107(built: psycopg.Connection) -> None:
    # Raw order 10107, completed, created 2026-05-18T15:02:37 local (Mon) -> ISO week 2026-05-18.
    # Coupon welcome10 (10%). Order: discount_total 10.26, shipping_total 21.74, total 131.20, total_tax 17.11,
    # meta _shipping_cost 22.50, _payment_gateway_fee 4.61 (= 131.20 * 2.75% + 1 = 4.608 -> 4.61).
    #   line 246 ACC-007 x1: subtotal 33.91, total 30.52 (39 incl - 10% = 35.10 incl; VAT 4.58) -> discount 3.39
    #   line 247 PRF-002 x1: subtotal 68.70, total 61.83                                     -> discount 6.87
    # Net revenue ex-VAT = 30.52 + 61.83 = 92.35; + shipping 21.74 = 114.09 = 131.20 - 17.11  (reconciles)
    # Weight of line 246 = 30.52 / 92.35 = 0.3304819
    #   shipping charged 2174 h: 246 -> 718.468, 247 -> 1455.532; floors 718+1455 = 2173, leftover 1 -> 247
    #                            => 246: 7.18, 247: 14.56
    #   shipping cost    2250 h: 246 -> 743.584, 247 -> 1506.416; floors 2249, leftover 1 -> 246
    #                            => 246: 7.44, 247: 15.06
    #   payment fee       461 h: 246 -> 152.352, 247 -> 308.648; floors 460, leftover 1 -> 247
    #                            => 246: 1.52, 247: 3.09
    # COGS (cost rows effective 2026-01-01): ACC-007 21.96 + 3.94 = 25.90; PRF-002 18.28 + 6.64 = 24.92
    # Net profit 246 = 30.52 - 25.90 + 7.18 - 7.44 - 1.52 = 2.84
    # Net profit 247 = 61.83 - 24.92 + 14.56 - 15.06 - 3.09 = 33.32
    # Order: gross profit 92.35 - 50.82 = 41.53; net profit 2.84 + 33.32 = 36.16
    #        (= 92.35 - 50.82 + 21.74 - 22.50 - 4.61); margin 36.16 / 92.35 = 39.155% -> 39.16
    l246, l247 = line_row(built, 246), line_row(built, 247)
    assert (l246["discount_ex_vat"], l246["net_revenue_ex_vat"]) == (D("3.39"), D("30.52"))
    assert (l247["discount_ex_vat"], l247["net_revenue_ex_vat"]) == (D("6.87"), D("61.83"))
    assert (l246["shipping_charged_ex_vat"], l246["shipping_cost_ex_vat"], l246["payment_fee"]) == (D("7.18"), D("7.44"), D("1.52"))
    assert (l247["shipping_charged_ex_vat"], l247["shipping_cost_ex_vat"], l247["payment_fee"]) == (D("14.56"), D("15.06"), D("3.09"))
    assert (l246["cost_effective_from"], l246["cogs"]) == (date(2026, 1, 1), D("25.90"))
    assert (l247["cost_effective_from"], l247["cogs"]) == (date(2026, 1, 1), D("24.92"))
    assert (l246["net_profit"], l247["net_profit"]) == (D("2.84"), D("33.32"))
    o = order_row(built, 10107)
    assert o["order_week"] == date(2026, 5, 18) and o["coupon_codes"] == ["welcome10"]
    assert (o["discount_ex_vat"], o["net_revenue_ex_vat"], o["shipping_charged_ex_vat"]) == (D("10.26"), D("92.35"), D("21.74"))
    assert (o["net_cogs"], o["gross_profit"], o["net_profit"]) == (D("50.82"), D("41.53"), D("36.16"))
    assert o["net_margin_pct_known_cost"] == D("39.16")


def test_golden_partially_refunded_order_10221(built: psycopg.Connection) -> None:
    # Raw order 10221, completed, created 2026-05-25T13:47:00 local (Mon) -> ISO week 2026-05-25. No coupon.
    # shipping_total 21.74, total 253.00, total_tax 33.00, _shipping_cost 22.50, _payment_gateway_fee 7.96.
    #   line 510 PRF-007 x1: total 138.26     line 511 CDT-001 x1: total 60.00     -> net revenue 198.26
    #   198.26 + 21.74 = 220.00 = 253.00 - 33.00 (reconciles)
    # Weight of line 511 = 60 / 198.26 = 0.3026329
    #   shipping charged 2174 h: 511 -> 657.924, 510 -> 1516.076; leftover 1 -> 511  => 510: 15.16, 511: 6.58
    #   shipping cost    2250 h: 511 -> 680.924, 510 -> 1569.076; leftover 1 -> 511  => 510: 15.69, 511: 6.81
    #   payment fee       796 h: 511 -> 240.896, 510 ->  555.104; leftover 1 -> 511  => 510:  5.55, 511: 2.41
    # COGS: PRF-007 34.03 + 6.72 = 40.75; CDT-001 31.73 + 4.68 = 36.41
    # Refund 50006 created 2026-06-14T04:47 local (Sun) -> ISO week 2026-06-08: line 511 x1, total -60.00,
    #   no shipping refunded. Revenue -60.00; COGS reversed 36.41; fee and shipping cost NOT recovered.
    # Line 511 lifetime: 60.00 - 60.00 - (36.41 - 36.41) + 6.58 - 6.81 - 2.41 = -2.64
    #   ledger: sale week 2026-05-25 = 60.00 - 36.41 + 6.58 - 6.81 - 2.41 = 20.95; refund week 2026-06-08 = -60.00 + 36.41 = -23.59
    # Line 510: 138.26 - 40.75 + 15.16 - 15.69 - 5.55 = 91.43
    # Order: net revenue after refunds 138.26; net COGS 40.75; net profit 91.43 - 2.64 = 88.79
    #        (= 138.26 - 40.75 + 21.74 - 22.50 - 7.96); margin 88.79 / 138.26 = 64.220% -> 64.22
    l510, l511 = line_row(built, 510), line_row(built, 511)
    assert (l510["shipping_charged_ex_vat"], l510["shipping_cost_ex_vat"], l510["payment_fee"]) == (D("15.16"), D("15.69"), D("5.55"))
    assert (l511["shipping_charged_ex_vat"], l511["shipping_cost_ex_vat"], l511["payment_fee"]) == (D("6.58"), D("6.81"), D("2.41"))
    assert (l511["refunded_qty"], l511["refunded_net_revenue_ex_vat"], l511["refunded_shipping_ex_vat"]) == (1, D("60.00"), D("0.00"))
    assert (l511["cogs"], l511["cogs_reversed"], l511["net_cogs"]) == (D("36.41"), D("36.41"), D("0.00"))
    assert (l511["net_revenue_after_refunds_ex_vat"], l511["net_profit"]) == (D("0.00"), D("-2.64"))
    assert (l510["cogs"], l510["net_profit"]) == (D("40.75"), D("91.43"))
    o = order_row(built, 10221)
    assert (o["net_revenue_ex_vat"], o["refunds_ex_vat"], o["net_revenue_after_refunds_ex_vat"]) == (D("198.26"), D("60.00"), D("138.26"))
    assert (o["net_cogs"], o["net_profit"], o["net_margin_pct_known_cost"]) == (D("40.75"), D("88.79"), D("64.22"))
    ledger = built.execute(
        """SELECT event_type, event_week, net_revenue_ex_vat, cogs, net_profit FROM intermediate.int_profit_ledger
           WHERE line_id = 511 ORDER BY event_type DESC""").fetchall()
    assert ledger == [("sale", date(2026, 5, 25), D("60.00"), D("36.41"), D("20.95")),
                      ("refund", date(2026, 6, 8), D("-60.00"), D("-36.41"), D("-23.59"))]


def test_golden_missing_cost_order_11003(built: psycopg.Connection) -> None:
    # Raw order 11003, completed, created 2026-07-14T13:11:14 local (Tue) -> ISO week 2026-07-13. No coupon.
    # shipping_total 21.74, total 263.00, total_tax 34.31, _shipping_cost 22.50, _payment_gateway_fee 8.23.
    #   line 2343 HOM-002 x1: total 155.65    line 2344 ACC-006 x1: total 51.30   -> net revenue 206.95
    #   206.95 + 21.74 = 228.69 = 263.00 - 34.31 (reconciles)
    # Weight of line 2344 = 51.30 / 206.95 = 0.2478859
    #   shipping charged 2174 h: 2344 -> 538.904, 2343 -> 1635.096; leftover 1 -> 2344 => 2343: 16.35, 2344: 5.39
    #   shipping cost    2250 h: 2344 -> 557.743, 2343 -> 1692.257; leftover 1 -> 2344 => 2343: 16.92, 2344: 5.58
    #   payment fee       823 h: 2344 -> 204.010, 2343 ->  618.990; leftover 1 -> 2343 => 2343:  6.19, 2344: 2.04
    # COGS: HOM-002 69.61 + 11.76 = 81.37. ACC-006 has NO cost row -> cost_missing, COGS/profit NULL (not 0).
    # Line 2343 net profit = 155.65 - 81.37 + 16.35 - 16.92 - 6.19 = 67.52
    # Order: has_missing_cost; net_profit NULL; net_profit_known_cost 67.52; missing 1 line / 51.30 revenue;
    #        margin on known-cost revenue 67.52 / 155.65 = 43.379% -> 43.38
    l2343, l2344 = line_row(built, 2343), line_row(built, 2344)
    assert (l2343["shipping_charged_ex_vat"], l2343["shipping_cost_ex_vat"], l2343["payment_fee"]) == (D("16.35"), D("16.92"), D("6.19"))
    assert (l2344["shipping_charged_ex_vat"], l2344["shipping_cost_ex_vat"], l2344["payment_fee"]) == (D("5.39"), D("5.58"), D("2.04"))
    assert (l2343["cost_missing"], l2343["cogs"], l2343["net_profit"]) == (False, D("81.37"), D("67.52"))
    assert (l2344["cost_missing"], l2344["cogs"], l2344["gross_profit"], l2344["net_profit"]) == (True, None, None, None)
    assert l2344["net_revenue_ex_vat"] == D("51.30")
    o = order_row(built, 11003)
    assert o["order_week"] == date(2026, 7, 13)
    assert (o["has_missing_cost"], o["net_profit"], o["gross_profit"]) == (True, None, None)
    assert (o["net_profit_known_cost"], o["missing_cost_lines"], o["missing_cost_net_revenue_ex_vat"]) == (D("67.52"), 1, D("51.30"))
    assert o["net_margin_pct_known_cost"] == D("43.38")
    issue = built.execute("SELECT amount_ex_vat FROM marts.dq_issues WHERE issue_type = 'missing_cost' AND source_id = '11003-2344'").fetchone()
    assert issue == (D("51.30"),)


def test_refund_week_basis_var_moves_refunds_to_the_order_week(built: psycopg.Connection, settings: Settings) -> None:
    result = run_dbt(["run", "--select", "int_profit_ledger", "--vars", "{refund_week_basis: order_date}"],
                     settings, target="test", quiet=True)
    try:
        assert result.returncode == 0, result.stdout[-3000:]
        week = built.execute("SELECT event_week FROM intermediate.int_profit_ledger WHERE line_id = 511 AND event_type = 'refund'").fetchone()[0]
        assert week == date(2026, 5, 25)  # order week instead of the refund's week 2026-06-08
    finally:
        restore = run_dbt(["run", "--select", "int_profit_ledger"], settings, target="test", quiet=True)
        assert restore.returncode == 0


def test_weeks_are_iso_monday_to_sunday(built: psycopg.Connection) -> None:
    weeks = built.execute("SELECT week_start, week_end, is_full_week FROM marts.agg_weekly_kpis ORDER BY 1").fetchall()
    assert len(weeks) == 26 and all(w[2] for w in weeks)
    assert all(ws.isoweekday() == 1 and we == ws + timedelta(days=6) for ws, we, _ in weeks)
    assert (weeks[0][0], weeks[-1][1]) == (date(2026, 5, 11), date(2026, 11, 8))


# --------------------------------------------------------------------------------------------------
# The printed profit-summary bridge must add up with a calculator, for every week and both columns.
# --------------------------------------------------------------------------------------------------

def _sar(cell: str) -> Decimal:
    return D(cell.strip().removeprefix("SAR ").replace(",", ""))


def check_printed_bridge(text: str) -> int:
    """Re-add every bridge block of the printed summary. Returns the number of '=' rows verified."""
    verified, running = 0, None
    lines = text.splitlines()
    for line in lines:
        if line.startswith(("  Revenue, all lines", "  Profit bridge")):
            running = [None, None]
            continue
        if running is None:
            continue
        cells = (line[44:60], line[60:76])
        if not line.strip() or "SAR" not in cells[0]:
            running = None
            continue
        op = line[2]
        values = [_sar(c) for c in cells]
        for i, value in enumerate(values):
            if op == " ":
                running[i] = value
            elif op == "+":
                running[i] += value
            elif op == "-":
                running[i] -= value
            elif op == "=":
                assert running[i] == value, f"{line.strip()!r}: computed {running[i]}"
                verified += 1
    return verified


def test_printed_bridge_adds_up_for_every_week(built: psycopg.Connection) -> None:
    from p3.profit_summary import build_profit_summary

    weeks = [r[0] for r in built.execute("SELECT week_start FROM marts.agg_weekly_kpis WHERE is_full_week ORDER BY 1")]
    assert len(weeks) == 26
    for week in weeks[1:]:  # first week has no "previous week" column
        text = build_profit_summary(built, week)
        assert f"Week: {week} (Mon)" in text
        # 3 '=' rows in the revenue block + 2 in the profit bridge, for this week and the previous week
        assert check_printed_bridge(text) == 10, week
        # the profit bridge starts from the number the revenue block ends with
        known = [ln for ln in text.splitlines() if "Net revenue after refunds (known cost)" in ln]
        assert len(known) == 2 and known[0][4:] == known[1][4:]
