import statistics
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from p3.generate import Dataset, generate_dataset
from p3.generate.catalog import COST_CHANGES, MISSING_COST_SKUS
from p3.generate.money import VAT_RATE
from p3.generate.store import END, PROMO_END, PROMO_START, START, StoreConfig


@pytest.fixture(scope="module")
def full() -> Dataset:
    return generate_dataset(StoreConfig())


def test_same_seed_is_identical_and_other_seed_differs() -> None:
    small = StoreConfig(seed=11, target_orders=300)
    assert generate_dataset(small) == generate_dataset(small)
    assert generate_dataset(small).orders != generate_dataset(StoreConfig(seed=12, target_orders=300)).orders


def test_volume_and_catalog(full: Dataset) -> None:
    assert len(full.products) == 40
    assert len({p["categories"][0]["name"] for p in full.products}) == 6
    assert 2700 <= len(full.orders) <= 3300
    assert 6300 <= sum(len(o["line_items"]) for o in full.orders) <= 7700


def test_dates_are_anchored_to_the_fixed_period(full: Dataset) -> None:
    created = [date.fromisoformat(o["date_created"][:10]) for o in full.orders]
    assert min(created) == START and max(created) == END
    assert START.weekday() == 0 and END.weekday() == 6 and ((END - START).days + 1) == 26 * 7


def test_promo_week_and_weekly_seasonality(full: Dataset) -> None:
    weeks = Counter(date.fromisoformat(o["date_created"][:10]).isocalendar()[1] for o in full.orders)
    promo_week = PROMO_START.isocalendar()[1]
    assert weeks[promo_week] > 1.6 * statistics.median(weeks.values())
    dows = Counter(date.fromisoformat(o["date_created"][:10]).weekday() for o in full.orders)
    assert dows[3] > dows[6]  # Thursday beats Sunday
    promo_orders = [o for o in full.orders if PROMO_START <= date.fromisoformat(o["date_created"][:10]) <= PROMO_END]
    assert sum(1 for o in promo_orders if any(c["code"] == "bts20" for c in o["coupon_lines"])) > len(promo_orders) / 2


def test_refunds_and_cancellations(full: Dataset) -> None:
    rate = len(full.refunds) / len(full.orders)
    assert 0.03 <= rate <= 0.05
    statuses = Counter(o["status"] for o in full.orders)
    assert statuses["cancelled"] > 0 and statuses["refunded"] > 0
    orders = {o["id"]: o for o in full.orders}
    partial = [r for r in full.refunds if Decimal(r["amount"]) < Decimal(orders[r["parent_id"]]["total"])]
    assert partial and len(partial) < len(full.refunds)  # both full and partial refunds exist


def test_vat_and_totals_reconcile(full: Dataset) -> None:
    for o in full.orders:
        lines_incl = Decimal("0")
        for li in o["line_items"]:
            total, tax = Decimal(li["total"]), Decimal(li["total_tax"])
            assert abs(tax - total * VAT_RATE) <= Decimal("0.01")
            lines_incl += total + tax
        assert lines_incl + Decimal(o["shipping_total"]) + Decimal(o["shipping_tax"]) == Decimal(o["total"])


def test_payment_fee_and_shipping_cost_in_meta(full: Dataset) -> None:
    o = next(o for o in full.orders if o["status"] == "completed")
    meta = {m["key"]: Decimal(m["value"]) for m in o["meta_data"]}
    expected_fee = (Decimal(o["total"]) * Decimal("0.0275") + 1).quantize(Decimal("0.01"))
    assert abs(meta["_payment_gateway_fee"] - expected_fee) <= Decimal("0.01")
    assert meta["_shipping_cost"] > 0


def test_cost_sheet_changes_and_missing_skus(full: Dataset) -> None:
    rows_per_sku = Counter(r["sku"] for r in full.costs)
    changed = {sku for sku, n in rows_per_sku.items() if n > 1}
    assert len(changed) >= 5 and set(COST_CHANGES) <= changed
    sold = {li["sku"] for o in full.orders for li in o["line_items"]}
    for sku in MISSING_COST_SKUS:
        assert sku not in rows_per_sku, "missing-cost SKU must have no cost row"
        assert sku in sold, "missing-cost SKU must actually sell, so the gap matters"
