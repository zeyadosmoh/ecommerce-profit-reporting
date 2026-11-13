"""In-memory store behind the mock API. Tests mutate it directly to simulate upstream changes."""

import copy
from collections.abc import Callable
from datetime import datetime

from p3.generate.store import UTC_OFFSET

ENTITIES = ("products", "orders", "refunds")
# Refunds are immutable in WooCommerce, so their "modified" time is their creation time.
MODIFIED_FIELD = {"products": "date_modified_gmt", "orders": "date_modified_gmt", "refunds": "date_created_gmt"}
SORT_FIELDS = {"id": "id", "date": "date_created_gmt", "modified": None}


class MockStore:
    def __init__(self, products: list[dict], orders: list[dict], refunds: list[dict],
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.records: dict[str, dict[int, dict]] = {
            "products": {p["id"]: copy.deepcopy(p) for p in products},
            "orders": {o["id"]: copy.deepcopy(o) for o in orders},
            "refunds": {r["id"]: copy.deepcopy(r) for r in refunds},
        }
        self.clock = clock  # returns store-local time

    def modified_gmt(self, entity: str, record: dict) -> datetime:
        return datetime.fromisoformat(record[MODIFIED_FIELD[entity]])

    def query(self, entity: str, modified_after_gmt: datetime | None, orderby: str, order: str,
              parent_id: int | None = None) -> list[dict]:
        rows = list(self.records[entity].values())
        if parent_id is not None:
            rows = [r for r in rows if r.get("parent_id") == parent_id]
        if modified_after_gmt is not None:
            rows = [r for r in rows if self.modified_gmt(entity, r) > modified_after_gmt]
        field = SORT_FIELDS[orderby] or MODIFIED_FIELD[entity]
        rows.sort(key=lambda r: (r[field], r["id"]), reverse=(order == "desc"))
        return rows

    def get(self, entity: str, record_id: int) -> dict | None:
        return self.records[entity].get(record_id)

    def update(self, entity: str, record_id: int, changes: dict, at_local: datetime | None = None) -> dict:
        """Apply top-level field changes and bump date_modified, like a WooCommerce PUT."""
        record = self.records[entity][record_id]
        record.update(copy.deepcopy(changes))
        if entity != "refunds":
            at = (at_local or self.clock()).replace(microsecond=0)
            record["date_modified"] = at.isoformat()
            record["date_modified_gmt"] = (at - UTC_OFFSET).isoformat()
        return record

    def set_modified_gmt(self, entity: str, record_id: int, at_gmt: datetime) -> None:
        record = self.records[entity][record_id]
        field = MODIFIED_FIELD[entity]
        record[field] = at_gmt.isoformat(timespec="seconds")
        record[field.removesuffix("_gmt")] = (at_gmt + UTC_OFFSET).isoformat(timespec="seconds")

    def max_modified_gmt(self, entity: str) -> datetime:
        return max(self.modified_gmt(entity, r) for r in self.records[entity].values())


def gmt_from_param(value: str, dates_are_gmt: bool) -> datetime:
    """Interpret a WooCommerce date filter: offset-aware -> UTC; naive -> GMT or site-local per dates_are_gmt."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        return (parsed - parsed.utcoffset()).replace(tzinfo=None)
    return parsed if dates_are_gmt else parsed - UTC_OFFSET
