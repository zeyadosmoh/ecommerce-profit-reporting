"""Synthetic WooCommerce-shaped products, orders and refunds.

All randomness comes from one seeded `random.Random`, and all dates are anchored to fixed constants
(never "today"), so the same seed always yields byte-identical output.
"""

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from p3.generate.catalog import CATEGORIES, ProductDef
from p3.generate.money import fmt, q, split_inclusive

# --- Calendar ------------------------------------------------------------------------------------
START = date(2026, 5, 11)  # Monday
END = date(2026, 11, 8)  # Sunday -> 26 full weeks
AS_OF = datetime(2026, 11, 9, 0, 0, 0)  # data snapshot moment (store local time)
UTC_OFFSET = timedelta(hours=3)  # Asia/Riyadh, no DST
PROMO_START, PROMO_END = date(2026, 8, 10), date(2026, 8, 16)  # back-to-school week (Mon-Sun)
PROMO_COUPON, PROMO_PCT = "BTS20", Decimal("0.20")

# Saudi weekend is Fri-Sat; online peaks Thursday night and Friday. Index = date.weekday() (Mon=0).
DOW_MULT = [0.90, 0.88, 0.95, 1.25, 1.22, 1.02, 0.85]
HOUR_WEIGHTS = [3, 2, 1, 1, 1, 1, 1, 2, 3, 4, 5, 5, 5, 5, 5, 5, 6, 7, 8, 9, 11, 12, 11, 7]
CITIES = [("Riyadh", "01", 35), ("Jeddah", "02", 25), ("Dammam", "04", 12), ("Makkah", "02", 8),
          ("Madinah", "03", 7), ("Khobar", "04", 6), ("Abha", "14", 4), ("Tabuk", "07", 3)]
REMOTE_CITIES = {"Abha", "Tabuk"}
PAYMENT_METHODS = [("mada", "mada", 45), ("apple_pay", "Apple Pay", 27), ("stripe", "Credit / Debit Card", 20),
                   ("tabby", "Tabby - Pay later", 8)]
OTHER_COUPONS = [("WELCOME10", Decimal("0.10"), 0.07, Decimal("0")), ("SAVE15", Decimal("0.15"), 0.05, Decimal("400"))]
REFUND_REASONS_FULL = ["Customer cancelled after dispatch", "Order lost in transit", "Wrong items delivered"]
REFUND_REASONS_PARTIAL = ["Damaged item", "Item not as described", "Missing item in parcel"]

FREE_SHIPPING_FROM = Decimal("299")  # VAT-inclusive basket value
FLAT_SHIPPING_INCL = Decimal("25")
CANCEL_RATE = 0.01
REFUND_RATE = 0.046  # ~4% of all orders after date cut-offs


@dataclass
class StoreConfig:
    seed: int = 42
    target_orders: int = 3000
    fee_pct: Decimal = Decimal("2.75")
    fee_fixed: Decimal = Decimal("1.00")


@dataclass
class StoreData:
    products: list[dict]
    orders: list[dict]
    refunds: list[dict]


def wc_dates(local: datetime | None, prefix: str) -> dict[str, str | None]:
    """WooCommerce exposes each date twice: site-local and GMT, both without offset."""
    if local is None:
        return {prefix: None, f"{prefix}_gmt": None}
    return {prefix: local.isoformat(timespec="seconds"), f"{prefix}_gmt": (local - UTC_OFFSET).isoformat(timespec="seconds")}


def poisson(rng: random.Random, lam: float) -> int:
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


def day_multiplier(d: date, day_index: int, n_days: int) -> float:
    mult = DOW_MULT[d.weekday()]
    if 26 <= d.day <= 30:  # government payday on the 27th
        mult *= 1.28
    if PROMO_START <= d <= PROMO_END:
        mult *= 2.2
    return mult * (0.92 + 0.16 * day_index / n_days)  # mild growth over the period


class _Ids:
    def __init__(self) -> None:
        self.order, self.line, self.meta, self.refund, self.item = 10000, 0, 1000, 50000, 0

    def next(self, attr: str) -> int:
        setattr(self, attr, getattr(self, attr) + 1)
        return getattr(self, attr)


def generate_store(products: list[ProductDef], cfg: StoreConfig, rng: random.Random) -> StoreData:
    weights = {p.sku: rng.uniform(0.5, 2.0) * math.sqrt(100 / float(p.price_incl)) for p in products}
    product_json = [_product_json(p, rng) for p in products]
    customers = list(range(1, int(cfg.target_orders * 0.65) + 1))
    customer_w = [1 / math.sqrt(i) for i in customers]  # a few loyal repeat buyers, a long tail

    days = [START + timedelta(days=i) for i in range((END - START).days + 1)]
    mults = [day_multiplier(d, i, len(days)) for i, d in enumerate(days)]
    base = cfg.target_orders / sum(mults)

    ids, orders, refunds = _Ids(), [], []
    for d, mult in zip(days, mults):
        times = sorted(_order_time(rng, d) for _ in range(poisson(rng, base * mult)))
        catalog = [p for p in products if p.launch is None or p.launch <= d]
        for created in times:
            order, order_refunds = _order(rng, ids, cfg, created, catalog, weights, customers, customer_w)
            orders.append(order)
            refunds.extend(order_refunds)
    return StoreData(product_json, orders, refunds)


def _product_json(p: ProductDef, rng: random.Random) -> dict:
    created = datetime.combine(p.launch or date(2026, 1, 15), time(10, 0)) + timedelta(minutes=rng.randint(0, 600))
    modified = (created if p.launch else datetime(2026, 4, 1, 9, 0)) + timedelta(minutes=rng.randint(0, 40000))
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.name.lower().replace(" ", "-"),
        "sku": p.sku,
        "type": "simple",
        "status": "publish",
        "price": fmt(p.price_incl),
        "regular_price": fmt(p.price_incl),
        "sale_price": "",
        "tax_status": "taxable",
        "tax_class": "",
        "stock_status": "instock",
        "categories": [{"id": list(CATEGORIES).index(p.category_code) + 15, "name": CATEGORIES[p.category_code],
                        "slug": p.category_code.lower()}],
        **wc_dates(created, "date_created"),
        **wc_dates(modified, "date_modified"),
        "meta_data": [],
    }


def _order_time(rng: random.Random, d: date) -> datetime:
    hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
    return datetime.combine(d, time(hour, rng.randint(0, 59), rng.randint(0, 59)))


def _pick_lines(rng: random.Random, catalog: list[ProductDef], weights: dict[str, float], promo: bool) -> list[tuple[ProductDef, int]]:
    n_lines = rng.choices([1, 2, 3, 4, 5], weights=[30, 30, 22, 12, 6])[0]
    pool = list(catalog)
    w = [weights[p.sku] * (2.0 if promo and p.category_code == "ACC" else 1.0) for p in pool]
    picked: list[tuple[ProductDef, int]] = []
    for _ in range(min(n_lines, len(pool))):
        i = rng.choices(range(len(pool)), weights=w)[0]
        p = pool.pop(i)
        w.pop(i)
        qty_weights = [60, 28, 12] if p.category_code in ("CDT", "BKH") else [82, 14, 4]
        picked.append((p, rng.choices([1, 2, 3], weights=qty_weights)[0]))
    return picked


def _coupon(rng: random.Random, promo: bool, gross_incl: Decimal) -> tuple[str, Decimal] | None:
    if promo:
        return (PROMO_COUPON, PROMO_PCT) if rng.random() < 0.65 else None
    roll = rng.random()
    for code, pct, prob, minimum in OTHER_COUPONS:
        if roll < prob and gross_incl >= minimum:
            return code, pct
        roll -= prob
    return None


def _meta(ids: _Ids, key: str, value: str) -> dict:
    return {"id": ids.next("meta"), "key": key, "value": value}


def _order(rng: random.Random, ids: _Ids, cfg: StoreConfig, created: datetime, catalog: list[ProductDef],
           weights: dict[str, float], customers: list[int], customer_w: list[float]) -> tuple[dict, list[dict]]:
    promo = PROMO_START <= created.date() <= PROMO_END
    picked = _pick_lines(rng, catalog, weights, promo)
    gross_incl = sum((p.price_incl * qty for p, qty in picked), Decimal("0"))
    coupon = _coupon(rng, promo, gross_incl)
    pct = coupon[1] if coupon else Decimal("0")

    line_items = []
    for p, qty in picked:
        line_gross = p.price_incl * qty
        subtotal, subtotal_tax = split_inclusive(line_gross)
        total, total_tax = split_inclusive(line_gross - q(line_gross * pct))
        line_items.append({
            "id": ids.next("line"),
            "name": p.name,
            "product_id": p.id,
            "variation_id": 0,
            "quantity": qty,
            "tax_class": "",
            "subtotal": fmt(subtotal),
            "subtotal_tax": fmt(subtotal_tax),
            "total": fmt(total),
            "total_tax": fmt(total_tax),
            "taxes": [{"id": 1, "total": fmt(total_tax), "subtotal": fmt(subtotal_tax)}],
            "meta_data": [],
            "sku": p.sku,
            "price": float(q(total / qty)),
        })

    def dsum(key: str) -> Decimal:
        return sum((Decimal(li[key]) for li in line_items), Decimal("0"))

    items_incl = dsum("total") + dsum("total_tax")
    ship_incl = Decimal("0") if items_incl >= FREE_SHIPPING_FROM else FLAT_SHIPPING_INCL
    ship_net, ship_tax = split_inclusive(ship_incl)
    total = items_incl + ship_incl
    city, state, _ = rng.choices(CITIES, weights=[c[2] for c in CITIES])[0]
    method, method_title, _ = rng.choices(PAYMENT_METHODS, weights=[m[2] for m in PAYMENT_METHODS])[0]
    units = sum(qty for _, qty in picked)
    carrier_cost = q(Decimal("19") + Decimal("3.5") * (units - 1) + (Decimal("6") if city in REMOTE_CITIES else 0))
    fee = q(total * cfg.fee_pct / 100 + cfg.fee_fixed)
    customer_id = 0 if rng.random() < 0.22 else rng.choices(customers, weights=customer_w)[0]

    order_id = ids.next("order")
    order_key = "wc_order_" + "".join(rng.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=13))
    cancelled = rng.random() < CANCEL_RATE
    paid = None if cancelled else created + timedelta(seconds=rng.randint(20, 600))
    completed = None
    if cancelled:
        status, modified = "cancelled", created + timedelta(minutes=rng.randint(30, 360))
        fee, carrier_cost = Decimal("0"), Decimal("0")
    else:
        done = created + timedelta(days=rng.randint(2, 5), hours=rng.randint(0, 10))
        if done < AS_OF:
            status, completed, modified = "completed", done, done
        else:
            status, modified = "processing", paid

    order = {
        "id": order_id,
        "parent_id": 0,
        "number": str(order_id),
        "order_key": order_key,
        "created_via": "checkout",
        "version": "9.3.0",
        "status": status,
        "currency": "SAR",
        "prices_include_tax": True,
        **wc_dates(created, "date_created"),
        **wc_dates(modified, "date_modified"),
        "discount_total": fmt(dsum("subtotal") - dsum("total")),
        "discount_tax": fmt(dsum("subtotal_tax") - dsum("total_tax")),
        "shipping_total": fmt(ship_net),
        "shipping_tax": fmt(ship_tax),
        "cart_tax": fmt(dsum("total_tax")),
        "total": fmt(total),
        "total_tax": fmt(dsum("total_tax") + ship_tax),
        "customer_id": customer_id,
        "billing": {"city": city, "state": state, "country": "SA"},
        "shipping": {"city": city, "state": state, "country": "SA"},
        "payment_method": method,
        "payment_method_title": method_title,
        "transaction_id": "" if cancelled else f"txn_{order_id}_{rng.randint(100000, 999999)}",
        **wc_dates(paid, "date_paid"),
        **wc_dates(completed, "date_completed"),
        "meta_data": [_meta(ids, "_shipping_cost", fmt(carrier_cost)), _meta(ids, "_payment_gateway_fee", fmt(fee))],
        "line_items": line_items,
        "shipping_lines": [{
            "id": ids.next("item"),
            "method_title": "Free shipping" if ship_incl == 0 else "Flat rate",
            "method_id": "free_shipping" if ship_incl == 0 else "flat_rate",
            "total": fmt(ship_net),
            "total_tax": fmt(ship_tax),
        }],
        "coupon_lines": [] if not coupon else [{
            "id": ids.next("item"),
            "code": coupon[0].lower(),
            "discount": fmt(dsum("subtotal") - dsum("total")),
            "discount_tax": fmt(dsum("subtotal_tax") - dsum("total_tax")),
        }],
        "refunds": [],
    }

    refunds: list[dict] = []
    if status == "completed" and rng.random() < REFUND_RATE:
        refund_at = completed + timedelta(days=rng.randint(1, 14), hours=rng.randint(0, 8))
        if refund_at < AS_OF:
            full = rng.random() < 0.5 or units == 1
            refund = _refund(rng, ids, order, refund_at, full)
            refunds.append(refund)
            order["refunds"].append({"id": refund["id"], "reason": refund["reason"], "total": "-" + refund["amount"]})
            order["status"] = "refunded" if full else order["status"]
            order.update(wc_dates(refund_at + timedelta(seconds=5), "date_modified"))
    return order, refunds


def _refund(rng: random.Random, ids: _Ids, order: dict, at: datetime, full: bool) -> dict:
    if full:
        lines = [(li, li["quantity"]) for li in order["line_items"]]
        amount = Decimal(order["total"])
        reason = rng.choice(REFUND_REASONS_FULL)
    else:
        li = rng.choice([li for li in order["line_items"]])
        lines = [(li, 1)]
        amount = None
        reason = rng.choice(REFUND_REASONS_PARTIAL)

    refund_lines, items_incl = [], Decimal("0")
    for li, qty in lines:
        share = Decimal(qty) / Decimal(li["quantity"])
        net, tax = q(Decimal(li["total"]) * share), q(Decimal(li["total_tax"]) * share)
        sub, sub_tax = q(Decimal(li["subtotal"]) * share), q(Decimal(li["subtotal_tax"]) * share)
        items_incl += net + tax
        refund_lines.append({
            "id": ids.next("item"),
            "name": li["name"],
            "product_id": li["product_id"],
            "sku": li["sku"],
            "quantity": -qty,
            "subtotal": fmt(-sub),
            "subtotal_tax": fmt(-sub_tax),
            "total": fmt(-net),
            "total_tax": fmt(-tax),
            "meta_data": [{"key": "_refunded_item_id", "value": str(li["id"])}],
        })
    shipping_lines = []
    if full and Decimal(order["shipping_total"]) > 0:
        shipping_lines.append({"id": ids.next("item"), "method_id": order["shipping_lines"][0]["method_id"],
                               "total": "-" + order["shipping_total"], "total_tax": "-" + order["shipping_tax"]})
    return {
        "id": ids.next("refund"),
        "parent_id": order["id"],
        **wc_dates(at, "date_created"),
        "amount": fmt(amount if amount is not None else items_incl),
        "reason": reason,
        "refunded_by": 1,
        "refunded_payment": True,
        "meta_data": [],
        "line_items": refund_lines,
        "shipping_lines": shipping_lines,
    }
