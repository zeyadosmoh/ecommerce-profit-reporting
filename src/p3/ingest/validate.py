"""Turn raw API / sheet records into typed rows, or raise RejectError with a human-readable reason."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

ORDER_STATUSES = {"pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed", "draft", "checkout-draft"}
CURRENCY = "SAR"
RECONCILE_TOLERANCE = Decimal("0.05")


class RejectError(ValueError):
    pass


def canonical_hash(obj: Any) -> str:
    """sha256 of canonical JSON (sorted keys, no whitespace) so key order never looks like a change."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _id(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RejectError(f"{field}: expected a positive integer id, got {value!r}")
    return value


def _ts(value: Any, field: str, required: bool = True) -> datetime | None:
    if value in (None, ""):
        if required:
            raise RejectError(f"{field}: missing")
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise RejectError(f"{field}: invalid datetime {value!r}") from None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _money(value: Any, field: str, allow_negative: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise RejectError(f"{field}: not a number {value!r}") from None
    if not amount.is_finite():
        raise RejectError(f"{field}: not a finite number {value!r}")
    if amount < 0 and not allow_negative:
        raise RejectError(f"{field}: negative amount {value!r}")
    return amount


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RejectError(f"{field}: missing or empty")
    return value.strip()


def _meta(raw: dict, key: str) -> Decimal | None:
    for entry in raw.get("meta_data") or []:
        if isinstance(entry, dict) and entry.get("key") == key:
            return _money(entry.get("value"), f"meta_data.{key}")
    return None


def validate_product(raw: dict, run_id: str) -> dict:
    cats = raw.get("categories") or []
    return {
        "id": _id(raw.get("id"), "id"),
        "sku": _text(raw.get("sku"), "sku"),
        "name": _text(raw.get("name"), "name"),
        "category": cats[0].get("name") if cats and isinstance(cats[0], dict) else None,
        "status": _text(raw.get("status"), "status"),
        "price": _money(raw.get("price"), "price"),
        "regular_price": _money(raw["regular_price"], "regular_price") if raw.get("regular_price") else None,
        "tax_status": raw.get("tax_status"),
        "date_created_gmt": _ts(raw.get("date_created_gmt"), "date_created_gmt", required=False),
        "date_modified_gmt": _ts(raw.get("date_modified_gmt"), "date_modified_gmt"),
        **_envelope(raw, run_id),
    }


def validate_order(raw: dict, run_id: str) -> tuple[dict, list[dict]]:
    order_id = _id(raw.get("id"), "id")
    status = _text(raw.get("status"), "status")
    if status not in ORDER_STATUSES:
        raise RejectError(f"status: unknown order status {status!r}")
    currency = _text(raw.get("currency"), "currency")
    if currency != CURRENCY:
        raise RejectError(f"currency: expected {CURRENCY}, got {currency!r}")
    items = raw.get("line_items")
    if not isinstance(items, list) or not items:
        raise RejectError("line_items: missing or empty")

    lines = [_validate_line(order_id, i, li) for i, li in enumerate(items)]
    money = {f: _money(raw.get(f), f) for f in
             ("discount_total", "discount_tax", "shipping_total", "shipping_tax", "total_tax", "total")}
    expected = sum((ln["total"] + ln["total_tax"] for ln in lines), Decimal("0")) \
        + money["shipping_total"] + money["shipping_tax"]
    if abs(expected - money["total"]) > RECONCILE_TOLERANCE:
        raise RejectError(f"total: {money['total']} does not reconcile with lines + shipping ({expected})")

    billing = raw.get("billing") if isinstance(raw.get("billing"), dict) else {}
    order = {
        "id": order_id,
        "number": raw.get("number"),
        "status": status,
        "currency": currency,
        "prices_include_tax": raw.get("prices_include_tax"),
        "customer_id": raw.get("customer_id") if isinstance(raw.get("customer_id"), int) else None,
        "payment_method": raw.get("payment_method"),
        "billing_city": billing.get("city"),
        "date_created_gmt": _ts(raw.get("date_created_gmt"), "date_created_gmt"),
        "date_modified_gmt": _ts(raw.get("date_modified_gmt"), "date_modified_gmt"),
        "date_paid_gmt": _ts(raw.get("date_paid_gmt"), "date_paid_gmt", required=False),
        "date_completed_gmt": _ts(raw.get("date_completed_gmt"), "date_completed_gmt", required=False),
        **money,
        "coupon_codes": [c.get("code", "") for c in raw.get("coupon_lines") or [] if isinstance(c, dict)],
        "shipping_cost": _meta(raw, "_shipping_cost"),
        "payment_fee": _meta(raw, "_payment_gateway_fee"),
        **_envelope(raw, run_id),
    }
    return order, lines


def _validate_line(order_id: int, idx: int, li: Any) -> dict:
    if not isinstance(li, dict):
        raise RejectError(f"line_items[{idx}]: not an object")
    qty = li.get("quantity")
    if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        raise RejectError(f"line_items[{idx}].quantity: expected a positive integer, got {qty!r}")
    return {
        "order_id": order_id,
        "line_id": _id(li.get("id"), f"line_items[{idx}].id"),
        "product_id": li.get("product_id") if isinstance(li.get("product_id"), int) else None,
        "sku": _text(li.get("sku"), f"line_items[{idx}].sku"),
        "name": li.get("name"),
        "quantity": qty,
        "price": _money(li["price"], f"line_items[{idx}].price") if li.get("price") is not None else None,
        **{f: _money(li.get(f), f"line_items[{idx}].{f}") for f in ("subtotal", "subtotal_tax", "total", "total_tax")},
        "payload": li,
    }


def validate_refund(raw: dict, run_id: str) -> dict:
    items = raw.get("line_items")
    if not isinstance(items, list):
        raise RejectError("line_items: expected a list")
    return {
        "id": _id(raw.get("id"), "id"),
        "order_id": _id(raw.get("parent_id"), "parent_id"),
        "date_created_gmt": _ts(raw.get("date_created_gmt"), "date_created_gmt"),
        "amount": _money(raw.get("amount"), "amount"),
        "reason": raw.get("reason"),
        "refunded_payment": raw.get("refunded_payment"),
        "line_items": items,
        **_envelope(raw, run_id),
    }


def validate_cost_row(raw: dict[str, str], run_id: str, source: str) -> dict:
    eff_raw = (raw.get("effective_from") or "").strip()
    try:
        effective_from = date.fromisoformat(eff_raw)
    except ValueError:
        raise RejectError(f"effective_from: invalid date {eff_raw!r} (expected YYYY-MM-DD)") from None
    normalized = {
        "sku": _text(raw.get("sku"), "sku"),
        "unit_cost": _money(raw.get("unit_cost"), "unit_cost"),
        "packaging_cost": _money(raw.get("packaging_cost"), "packaging_cost"),
        "effective_from": effective_from,
    }
    payload = {k: str(v) for k, v in normalized.items()}
    return {**normalized, "source": source, "payload": payload, "payload_hash": canonical_hash(payload),
            "last_run_id": run_id}


def _envelope(raw: dict, run_id: str) -> dict:
    return {"payload": raw, "payload_hash": canonical_hash(raw), "last_run_id": run_id}
