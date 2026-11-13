"""Fault injection for testing the reject path. OFF unless explicitly enabled (tests or `serve --inject-faults N`)."""

import copy

FAULT_KINDS = ("bad_date", "negative_quantity", "non_numeric_total", "missing_sku")


def pick_fault_ids(order_ids: list[int], count: int) -> dict[int, str]:
    """Deterministically choose `count` orders spread over the id range, cycling through fault kinds."""
    if count <= 0 or not order_ids:
        return {}
    ids = sorted(order_ids)
    step = max(len(ids) // count, 1)
    chosen = ids[step // 2 :: step][:count]
    return {oid: FAULT_KINDS[i % len(FAULT_KINDS)] for i, oid in enumerate(chosen)}


def corrupt_order(order: dict, kind: str) -> dict:
    bad = copy.deepcopy(order)
    if kind == "bad_date":
        bad["date_created_gmt"] = "2026-13-45T99:00:00"
    elif kind == "negative_quantity":
        bad["line_items"][0]["quantity"] = -2
    elif kind == "non_numeric_total":
        bad["total"] = "12,50 SAR"
    elif kind == "missing_sku":
        bad["line_items"][0]["sku"] = ""
    return bad
