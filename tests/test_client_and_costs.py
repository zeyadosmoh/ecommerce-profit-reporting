import httpx
import pytest

from p3.ingest.client import StoreClient
from p3.ingest.costs_loader import CostSheetError, parse_cost_sheet, read_cost_source
from p3.ingest.validate import canonical_hash
from tests.conftest import count, make_app

BASE = "http://store.test/wp-json/wc/v3"


def flaky_transport(fail_times: int, status: int = 503) -> tuple[httpx.MockTransport, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= fail_times:
            return httpx.Response(status)
        return httpx.Response(200, json=[{"id": 1}], headers={"X-WP-TotalPages": "1"})

    return httpx.MockTransport(handler), calls


def test_client_retries_transient_errors_with_backoff() -> None:
    transport, calls = flaky_transport(fail_times=2)
    client = StoreClient(httpx.Client(transport=transport), BASE, ("k", "s"), backoff_initial=0)
    assert list(client.iter_records("orders")) == [{"id": 1}]
    assert len(calls) == 3


def test_client_gives_up_after_max_attempts() -> None:
    transport, calls = flaky_transport(fail_times=99)
    client = StoreClient(httpx.Client(transport=transport), BASE, ("k", "s"), max_attempts=3, backoff_initial=0)
    with pytest.raises(Exception, match="HTTP 503"):
        list(client.iter_records("orders"))
    assert len(calls) == 3


def test_client_does_not_retry_auth_errors() -> None:
    transport, calls = flaky_transport(fail_times=99, status=401)
    client = StoreClient(httpx.Client(transport=transport), BASE, ("k", "s"), backoff_initial=0)
    with pytest.raises(httpx.HTTPStatusError):
        list(client.iter_records("orders"))
    assert len(calls) == 1


def test_canonical_hash_ignores_key_order() -> None:
    assert canonical_hash({"a": 1, "b": {"x": 1, "y": 2}}) == canonical_hash({"b": {"y": 2, "x": 1}, "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_cost_sheet_from_url() -> None:
    csv_text = "sku,unit_cost,packaging_cost,effective_from\nABC-1,10.00,1.00,2026-01-01\n"
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=csv_text)))
    text = read_cost_source("https://docs.google.com/spreadsheets/d/x/export?format=csv", http)
    assert parse_cost_sheet(text) == [(2, {"sku": "ABC-1", "unit_cost": "10.00", "packaging_cost": "1.00",
                                           "effective_from": "2026-01-01"})]


def test_cost_sheet_missing_column_is_fatal() -> None:
    with pytest.raises(CostSheetError, match="packaging_cost"):
        parse_cost_sheet("sku,unit_cost,effective_from\nA,1,2026-01-01\n")


def test_bad_cost_rows_are_rejected_with_row_numbers(db, store, ingest, tmp_path) -> None:  # noqa: ANN001
    sheet = tmp_path / "costs.csv"
    sheet.write_text(
        "SKU,Unit_Cost,Packaging_Cost,Effective_From\n"   # header case from a hand-made sheet
        "PRF-001,100.00,6.00,2026-01-01\n"
        "PRF-002,-5,6.00,2026-01-01\n"                    # negative cost
        "PRF-003,30.00,,2026-01-01\n"                     # blank packaging is not silently zero
        "PRF-004,30.00,2.00,01/08/2026\n"                 # non-ISO date
        ",,,\n"                                           # blank row: ignored
        "PRF-001,100.00,6.00,2026-01-01\n"                # duplicate
    )
    result = ingest(make_app(store), cost_source=str(sheet))
    assert result.stats["costs"].read == 5 and result.stats["costs"].rejected == 4
    assert count(db, "raw_costs") == 1
    reasons = dict(db.execute("SELECT source_id, reason FROM raw.etl_rejects WHERE entity = 'costs'").fetchall())
    assert reasons["row 3"].startswith("unit_cost: negative")
    assert reasons["row 4"].startswith("packaging_cost: not a number")
    assert reasons["row 5"].startswith("effective_from: invalid date")
    assert reasons["row 7"].startswith("duplicate row")


def test_cost_sheet_with_no_valid_rows_fails_the_run(db, store, ingest, tmp_path) -> None:  # noqa: ANN001
    sheet = tmp_path / "costs.csv"
    sheet.write_text("sku,unit_cost,packaging_cost,effective_from\nX,abc,1,2026-01-01\n")
    with pytest.raises(CostSheetError):
        ingest(make_app(store), cost_source=str(sheet))
    assert count(db, "raw_orders") == 0
