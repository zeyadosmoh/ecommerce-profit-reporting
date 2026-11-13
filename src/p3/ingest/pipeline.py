"""One ingestion run: extract (HTTP) -> validate -> load (single DB transaction) -> run log.

Design:
- Extract everything first, then write in ONE transaction: no DB transaction is held open across network
  retries, and a failure anywhere leaves no partial rows.
- Watermarks live in raw.etl_watermarks and are written inside that same transaction.
- The etl_runs row is written on a separate autocommit connection, so a failed run is still logged.
- Upserts are set-based (COPY into a temp table, then INSERT .. ON CONFLICT .. WHERE hash differs),
  so an unchanged record is never rewritten and is not counted as upserted.
"""

import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from p3.ingest.client import StoreClient
from p3.ingest.costs_loader import CostSheetError, parse_cost_sheet, read_cost_source
from p3.ingest.validate import (
    RejectError,
    validate_cost_row,
    validate_order,
    validate_product,
    validate_refund,
)

WATERMARK_OVERLAP = timedelta(minutes=5)
ADVISORY_LOCK_KEY = 0x50335F494E47  # "P3_ING"

PRODUCT_COLS = ["id", "sku", "name", "category", "status", "price", "regular_price", "tax_status",
                "date_created_gmt", "date_modified_gmt", "payload", "payload_hash", "last_run_id"]
ORDER_COLS = ["id", "number", "status", "currency", "prices_include_tax", "customer_id", "payment_method",
              "billing_city", "date_created_gmt", "date_modified_gmt", "date_paid_gmt", "date_completed_gmt",
              "discount_total", "discount_tax", "shipping_total", "shipping_tax", "total_tax", "total",
              "coupon_codes", "shipping_cost", "payment_fee", "payload", "payload_hash", "last_run_id"]
LINE_COLS = ["order_id", "line_id", "product_id", "sku", "name", "quantity", "price", "subtotal", "subtotal_tax",
             "total", "total_tax", "payload"]
REFUND_COLS = ["id", "order_id", "date_created_gmt", "amount", "reason", "refunded_payment", "line_items",
               "payload", "payload_hash", "last_run_id"]
COST_COLS = ["sku", "effective_from", "unit_cost", "packaging_cost", "source", "payload", "payload_hash",
             "last_run_id"]
JSON_COLS = {"payload", "line_items"}

# entity -> (table, columns, conflict keys, watermark field, validator)
API_ENTITIES: dict[str, tuple[str, list[str], list[str], str, Callable]] = {
    "products": ("raw.raw_products", PRODUCT_COLS, ["id"], "date_modified_gmt", validate_product),
    "orders": ("raw.raw_orders", ORDER_COLS, ["id"], "date_modified_gmt", validate_order),
    "refunds": ("raw.raw_refunds", REFUND_COLS, ["id"], "date_created_gmt", validate_refund),
}


@dataclass
class EntityStats:
    read: int = 0
    rejected: int = 0
    duplicates: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def upserted(self) -> int:
        return self.inserted + self.updated


@dataclass
class RunResult:
    run_id: str
    status: str
    stats: dict[str, EntityStats]
    error: str | None = None

    @property
    def rows_read(self) -> int:
        return sum(s.read for s in self.stats.values())

    @property
    def rows_upserted(self) -> int:
        return sum(s.upserted for s in self.stats.values())

    @property
    def rows_rejected(self) -> int:
        return sum(s.rejected for s in self.stats.values())


@dataclass
class Reject:
    entity: str
    source_id: str | None
    reason: str
    payload: object


@dataclass
class Extracted:
    rows: dict[str, list] = field(default_factory=dict)  # validated rows per entity (orders: (order, lines))
    rejects: list[Reject] = field(default_factory=list)
    watermarks: dict[str, datetime] = field(default_factory=dict)  # new watermark per entity
    stats: dict[str, EntityStats] = field(default_factory=dict)


def run_ingest(dsn: str, client: StoreClient, cost_source: str, cost_http=None) -> RunResult:  # noqa: ANN001
    run_id = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as log_conn:
        log_conn.execute("INSERT INTO raw.etl_runs (run_id, status) VALUES (%s, 'running')", (run_id,))
        result = RunResult(run_id, "running", {})
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                if not conn.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,)).fetchone()[0]:
                    raise RuntimeError("another ingestion run is in progress")
                previous = dict(conn.execute("SELECT entity, watermark FROM raw.etl_watermarks").fetchall())
                extracted = extract(client, cost_source, cost_http, run_id, previous)
                result.stats = extracted.stats
                with conn.transaction():
                    load(conn, run_id, extracted)
            result.status = "success"
        except Exception as exc:
            result.status, result.error = "failed", f"{type(exc).__name__}: {exc}"
            _finish_run(log_conn, result, traceback.format_exc())
            raise
        _finish_run(log_conn, result, None)
    return result


def extract(client: StoreClient, cost_source: str, cost_http, run_id: str,  # noqa: ANN001
            previous: dict[str, datetime]) -> Extracted:
    out = Extracted()
    for entity, (_, _, _, wm_field, validator) in API_ENTITIES.items():
        stats = out.stats[entity] = EntityStats()
        since = previous.get(entity) - WATERMARK_OVERLAP if entity in previous else None
        stats.extra["modified_after"] = since.isoformat() if since else None
        by_id: dict[int, object] = {}
        newest = previous.get(entity)
        for raw in client.iter_records(entity, since):
            stats.read += 1
            try:
                if not isinstance(raw, dict):
                    raise RejectError("record is not a JSON object")
                row = validator(raw, run_id)
            except RejectError as exc:
                out.rejects.append(Reject(entity, _source_id(raw), str(exc), raw))
                stats.rejected += 1
                continue
            head = row[0] if isinstance(row, tuple) else row
            if head["id"] in by_id:
                stats.duplicates += 1  # same record seen twice across pages; keep the latest version
            by_id[head["id"]] = row
            newest = max(newest, head[wm_field]) if newest else head[wm_field]
        out.rows[entity] = list(by_id.values())
        if newest is not None:
            out.watermarks[entity] = newest
        stats.extra["watermark"] = newest.isoformat() if newest else None

    stats = out.stats["costs"] = EntityStats()
    stats.extra["source"] = cost_source
    by_key: dict[tuple, dict] = {}
    for rownum, raw in parse_cost_sheet(read_cost_source(cost_source, cost_http)):
        stats.read += 1
        try:
            row = validate_cost_row(raw, run_id, cost_source)
            key = (row["sku"], row["effective_from"])
            if key in by_key:
                raise RejectError(f"duplicate row for sku {key[0]} effective {key[1]}")
            by_key[key] = row
        except RejectError as exc:
            out.rejects.append(Reject("costs", f"row {rownum}", str(exc), raw))
            stats.rejected += 1
    if not by_key:
        raise CostSheetError("cost sheet has no valid rows; refusing to replace the existing costs")
    out.rows["costs"] = list(by_key.values())
    return out


def load(conn: psycopg.Connection, run_id: str, ex: Extracted) -> None:
    """Everything here runs inside the caller's single transaction."""
    products = _upsert(conn, "products", "raw.raw_products", PRODUCT_COLS, ["id"], ex.rows["products"])
    ex.stats["products"].inserted, ex.stats["products"].updated = products
    load_orders(conn, ex)
    refunds = _upsert(conn, "refunds", "raw.raw_refunds", REFUND_COLS, ["id"], ex.rows["refunds"])
    ex.stats["refunds"].inserted, ex.stats["refunds"].updated = refunds
    load_costs(conn, ex)
    _write_rejects(conn, run_id, ex.rejects)
    for entity, wm in ex.watermarks.items():
        conn.execute(
            """INSERT INTO raw.etl_watermarks (entity, watermark, run_id) VALUES (%s, %s, %s)
               ON CONFLICT (entity) DO UPDATE
               SET watermark = EXCLUDED.watermark, run_id = EXCLUDED.run_id, updated_at = now()""",
            (entity, wm, run_id),
        )
    for entity, stats in ex.stats.items():
        stats.unchanged = len(ex.rows.get(entity, [])) - stats.upserted


def load_orders(conn: psycopg.Connection, ex: Extracted) -> None:
    pairs = ex.rows["orders"]
    stats = ex.stats["orders"]
    changed: list[int] = []
    stats.inserted, stats.updated = _upsert(conn, "orders", "raw.raw_orders", ORDER_COLS, ["id"],
                                            [order for order, _ in pairs], changed)
    # Replace lines of changed orders only, inside the same transaction as the order itself.
    changed_set = set(changed)
    conn.execute("DELETE FROM raw.raw_order_lines WHERE order_id = ANY(%s)", (changed,))
    lines = [ln for order, lns in pairs if order["id"] in changed_set for ln in lns]
    _copy(conn, "raw.raw_order_lines", LINE_COLS, lines)
    stats.extra["lines_written"] = len(lines)


def load_costs(conn: psycopg.Connection, ex: Extracted) -> None:
    stats = ex.stats["costs"]
    stats.inserted, stats.updated = _upsert(conn, "costs", "raw.raw_costs", COST_COLS, ["sku", "effective_from"],
                                            ex.rows["costs"])
    # The sheet is a full snapshot: rows removed from it are removed here (and later flagged as missing cost).
    deleted = conn.execute(
        """DELETE FROM raw.raw_costs c
           WHERE NOT EXISTS (SELECT 1 FROM stg_costs s WHERE s.sku = c.sku AND s.effective_from = c.effective_from)
           RETURNING sku"""
    ).fetchall()
    stats.extra["deleted"] = len(deleted)


def _upsert(conn: psycopg.Connection, name: str, table: str, cols: list[str], keys: list[str], rows: list[dict],
            changed_out: list | None = None) -> tuple[int, int]:
    stg = f"stg_{name}"
    conn.execute(f"CREATE TEMP TABLE {stg} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
    _copy(conn, stg, cols, rows)
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys)
    returned = conn.execute(
        f"""INSERT INTO {table} ({col_list}) SELECT {col_list} FROM {stg}
            ON CONFLICT ({", ".join(keys)}) DO UPDATE SET {updates}, updated_at = now()
            WHERE {table}.payload_hash IS DISTINCT FROM EXCLUDED.payload_hash
            RETURNING {keys[0]}, (xmax = 0) AS inserted"""
    ).fetchall()
    if changed_out is not None:
        changed_out.extend(r[0] for r in returned)
    inserted = sum(1 for r in returned if r[1])
    return inserted, len(returned) - inserted


def _copy(conn: psycopg.Connection, table: str, cols: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    with conn.cursor().copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row([Jsonb(row[c]) if c in JSON_COLS else row[c] for c in cols])


def _write_rejects(conn: psycopg.Connection, run_id: str, rejects: list[Reject]) -> None:
    if rejects:
        conn.cursor().executemany(
            "INSERT INTO raw.etl_rejects (run_id, entity, source_id, reason, payload) VALUES (%s, %s, %s, %s, %s)",
            [(run_id, r.entity, r.source_id, r.reason, Jsonb(r.payload)) for r in rejects],
        )


def _finish_run(log_conn: psycopg.Connection, result: RunResult, tb: str | None) -> None:
    stats = {e: {**asdict(s), "upserted": s.upserted} for e, s in result.stats.items()}
    if result.status == "failed":
        # Nothing was committed, so report zero upserts even if the load got part-way.
        read, upserted, rejected = result.rows_read, 0, 0
        stats = {"attempted": stats, "traceback": tb[-4000:] if tb else None}
    else:
        read, upserted, rejected = result.rows_read, result.rows_upserted, result.rows_rejected
    log_conn.execute(
        """UPDATE raw.etl_runs SET finished_at = now(), status = %s, rows_read = %s, rows_upserted = %s,
           rows_rejected = %s, stats = %s, error = %s WHERE run_id = %s""",
        (result.status, read, upserted, rejected, Jsonb(stats), result.error, result.run_id),
    )


def _source_id(raw: object) -> str | None:
    return str(raw.get("id")) if isinstance(raw, dict) and raw.get("id") is not None else None
