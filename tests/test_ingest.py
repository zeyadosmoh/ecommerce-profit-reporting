from datetime import timedelta
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

from p3.generate.catalog import MISSING_COST_SKUS
from p3.ingest import pipeline
from p3.ingest.pipeline import WATERMARK_OVERLAP
from p3.mock_api.app import API_PREFIX
from p3.mock_api.faults import FAULT_KINDS
from p3.mock_api.store import MockStore
from tests.conftest import API_KEY, API_SECRET, count, make_app

RAW_DATA_TABLES = ["raw_products", "raw_orders", "raw_order_lines", "raw_refunds", "raw_costs"]


def test_first_run_loads_everything(db: psycopg.Connection, store: MockStore, small_dataset, ingest) -> None:
    result = ingest(make_app(store))
    assert result.status == "success" and result.rows_rejected == 0
    assert count(db, "raw_products") == len(small_dataset.products)
    assert count(db, "raw_orders") == len(small_dataset.orders)
    assert count(db, "raw_order_lines") == sum(len(o["line_items"]) for o in small_dataset.orders)
    assert count(db, "raw_refunds") == len(small_dataset.refunds)
    assert count(db, "raw_costs") == len(small_dataset.costs)
    skus = {r[0] for r in db.execute("SELECT DISTINCT sku FROM raw.raw_costs")}
    assert not skus & set(MISSING_COST_SKUS), "missing-cost SKUs must not be invented"
    run = db.execute("SELECT status, rows_read, rows_upserted FROM raw.etl_runs").fetchone()
    assert run == ("success", result.rows_read, result.rows_upserted)
    vat = db.execute("SELECT sum(total_tax) FROM raw.raw_orders").fetchone()[0]
    assert vat == sum(Decimal(o["total_tax"]) for o in small_dataset.orders)


def test_second_run_without_changes_upserts_zero(db: psycopg.Connection, store: MockStore, ingest) -> None:
    ingest(make_app(store))
    second = ingest(make_app(store))
    assert second.status == "success"
    assert second.rows_upserted == 0
    assert second.stats["orders"].extra["lines_written"] == 0
    assert db.execute("SELECT rows_upserted FROM raw.etl_runs ORDER BY started_at DESC LIMIT 1").fetchone()[0] == 0


def test_overlap_window_rereads_records_but_upserts_zero(db: psycopg.Connection, store: MockStore, ingest) -> None:
    # Put several orders just inside the 5-minute overlap below the newest modification time.
    newest = store.max_modified_gmt("orders")
    ids = sorted(store.records["orders"])[:3]
    for minutes, oid in enumerate(ids, start=1):
        store.set_modified_gmt("orders", oid, newest - timedelta(minutes=minutes))
    in_window = [o for o in store.records["orders"].values()
                 if store.modified_gmt("orders", o) > newest - WATERMARK_OVERLAP]
    assert len(in_window) >= 4

    ingest(make_app(store))
    second = ingest(make_app(store))
    orders = second.stats["orders"]
    assert orders.extra["modified_after"].startswith((newest - WATERMARK_OVERLAP).isoformat())
    assert orders.read == len(in_window)  # re-read because of the overlap ...
    assert orders.upserted == 0 and orders.unchanged == len(in_window)  # ... but nothing rewritten
    assert second.rows_upserted == 0


def test_key_order_change_is_not_an_update(db: psycopg.Connection, store: MockStore, ingest) -> None:
    ingest(make_app(store))
    for entity in ("products", "orders", "refunds"):
        for rid, rec in store.records[entity].items():
            store.records[entity][rid] = dict(reversed(list(rec.items())))
    db.execute("DELETE FROM raw.etl_watermarks")  # force a full re-read
    second = ingest(make_app(store))
    assert second.stats["orders"].read == len(store.records["orders"])
    assert second.rows_upserted == 0


def test_upstream_modification_is_picked_up(db: psycopg.Connection, store: MockStore, ingest) -> None:
    app = make_app(store)
    ingest(app)
    status_id, renamed_id, untouched_id = sorted(store.records["orders"])[5:8]
    http = TestClient(app)
    resp = http.put(f"{API_PREFIX}/orders/{status_id}", json={"status": "on-hold"}, auth=(API_KEY, API_SECRET))
    assert resp.status_code == 200
    lines = [dict(li) for li in store.records["orders"][renamed_id]["line_items"]]
    lines[0]["name"] = "Renamed upstream"
    store.update("orders", renamed_id, {"line_items": lines})

    second = ingest(app)
    orders = second.stats["orders"]
    assert (orders.inserted, orders.updated) == (0, 2)
    assert orders.extra["lines_written"] == len(lines) + len(store.records["orders"][status_id]["line_items"])
    assert db.execute("SELECT status FROM raw.raw_orders WHERE id = %s", (status_id,)).fetchone()[0] == "on-hold"
    names = db.execute("SELECT name FROM raw.raw_order_lines WHERE order_id = %s ORDER BY line_id", (renamed_id,)).fetchall()
    assert names[0][0] == "Renamed upstream" and len(names) == len(lines)
    last_runs = dict(db.execute("SELECT id, last_run_id::text FROM raw.raw_orders WHERE id = ANY(%s)",
                                ([status_id, renamed_id, untouched_id],)).fetchall())
    assert last_runs[status_id] == last_runs[renamed_id] == second.run_id
    assert last_runs[untouched_id] != second.run_id


def test_malformed_records_are_rejected_without_aborting(db: psycopg.Connection, store: MockStore, ingest) -> None:
    n_faults = len(FAULT_KINDS)
    result = ingest(make_app(store, inject_faults=n_faults))
    assert result.status == "success"
    assert result.stats["orders"].rejected == n_faults
    assert count(db, "raw_orders") == len(store.records["orders"]) - n_faults
    rejects = db.execute("SELECT entity, source_id, reason FROM raw.etl_rejects WHERE run_id = %s",
                         (result.run_id,)).fetchall()
    assert len(rejects) == n_faults and all(r[0] == "orders" and r[1] for r in rejects)
    reasons = " | ".join(r[2] for r in rejects)
    for expected in ("date_created_gmt: invalid datetime", "quantity: expected a positive integer",
                     "total: not a number", "sku: missing or empty"):
        assert expected in reasons
    rejected_ids = [int(r[1]) for r in rejects]
    assert db.execute("SELECT count(*) FROM raw.raw_orders WHERE id = ANY(%s)", (rejected_ids,)).fetchone()[0] == 0
    run = db.execute("SELECT status, rows_rejected FROM raw.etl_runs WHERE run_id = %s", (result.run_id,)).fetchone()
    assert run == ("success", n_faults)


def test_failed_run_leaves_no_partial_rows(db: psycopg.Connection, store: MockStore, ingest,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(conn, ex) -> None:  # noqa: ANN001 - runs after products/orders/refunds were written in the txn
        assert conn.execute("SELECT count(*) FROM raw.raw_orders").fetchone()[0] > 0
        raise RuntimeError("simulated failure mid-load")

    monkeypatch.setattr(pipeline, "load_costs", boom)
    with pytest.raises(RuntimeError, match="simulated failure"):
        ingest(make_app(store, inject_faults=2))
    for table in RAW_DATA_TABLES + ["etl_rejects", "etl_watermarks"]:
        assert count(db, table) == 0, f"{table} has partial rows"
    status, error, upserted = db.execute("SELECT status, error, rows_upserted FROM raw.etl_runs").fetchone()
    assert status == "failed" and "simulated failure" in error and upserted == 0


def test_failed_later_run_keeps_previous_state(db: psycopg.Connection, store: MockStore, ingest,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(store)
    ingest(app)
    watermarks = db.execute("SELECT entity, watermark FROM raw.etl_watermarks ORDER BY entity").fetchall()
    order_id = sorted(store.records["orders"])[0]
    store.update("orders", order_id, {"status": "on-hold"})
    monkeypatch.setattr(pipeline, "_write_rejects", lambda *a: (_ for _ in ()).throw(RuntimeError("db hiccup")))
    with pytest.raises(RuntimeError):
        ingest(app)
    assert db.execute("SELECT status FROM raw.raw_orders WHERE id = %s", (order_id,)).fetchone()[0] != "on-hold"
    assert db.execute("SELECT entity, watermark FROM raw.etl_watermarks ORDER BY entity").fetchall() == watermarks
    monkeypatch.undo()
    third = ingest(app)  # the change is picked up once the problem is gone
    assert third.stats["orders"].updated == 1
