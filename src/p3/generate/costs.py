"""Cost sheet generator: the CSV a store owner keeps in Google Sheets (costs are ex-VAT)."""

import random
from decimal import Decimal

from p3.generate.catalog import (
    COST_CHANGES,
    COST_RATIO,
    COST_SHEET_START,
    MISSING_COST_SKUS,
    PACKAGING,
    ProductDef,
)
from p3.generate.money import fmt, q, split_inclusive

COST_COLUMNS = ["sku", "unit_cost", "packaging_cost", "effective_from"]


def generate_costs(rng: random.Random, products: list[ProductDef]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for p in products:
        lo, hi = COST_RATIO[p.category_code]
        net_price, _ = split_inclusive(p.price_incl)
        unit_cost = q(net_price * Decimal(str(round(rng.uniform(lo, hi), 4))))
        packaging = q(PACKAGING[p.category_code] + Decimal(str(round(rng.uniform(-0.5, 0.5), 2))))
        if p.sku in MISSING_COST_SKUS:
            continue  # deliberately absent: must never be treated as zero cost
        rows.append(_row(p.sku, unit_cost, packaging, COST_SHEET_START.isoformat()))
        if p.sku in COST_CHANGES:
            eff, mult, pack_delta = COST_CHANGES[p.sku]
            rows.append(_row(p.sku, q(unit_cost * mult), packaging + pack_delta, eff.isoformat()))
    rows.sort(key=lambda r: (r["sku"], r["effective_from"]))
    return rows


def _row(sku: str, unit_cost: Decimal, packaging: Decimal, effective_from: str) -> dict[str, str]:
    return {"sku": sku, "unit_cost": fmt(unit_cost), "packaging_cost": fmt(packaging), "effective_from": effective_from}
