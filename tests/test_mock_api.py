from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from p3.mock_api.app import API_PREFIX
from p3.mock_api.store import MockStore
from tests.conftest import API_KEY, API_SECRET, make_app, make_client

AUTH = (API_KEY, API_SECRET)


@pytest.mark.parametrize("entity", ["products", "orders", "refunds"])
@pytest.mark.parametrize("per_page", [7, 100])
def test_pagination_covers_every_record_exactly_once(store: MockStore, entity: str, per_page: int) -> None:
    ids = [r["id"] for r in make_client(make_app(store), per_page=per_page).iter_records(entity)]
    assert len(ids) == len(set(ids)) == len(store.records[entity])
    assert set(ids) == set(store.records[entity])


def test_total_headers_and_page_bounds(store: MockStore) -> None:
    http = TestClient(make_app(store))
    n = len(store.records["orders"])
    resp = http.get(f"{API_PREFIX}/orders", params={"per_page": 50}, auth=AUTH)
    assert resp.headers["X-WP-Total"] == str(n)
    assert resp.headers["X-WP-TotalPages"] == str(-(-n // 50))
    assert 'rel="next"' in resp.headers["Link"]
    assert http.get(f"{API_PREFIX}/orders", params={"per_page": 101}, auth=AUTH).status_code == 400
    assert http.get(f"{API_PREFIX}/orders", params={"page": 999}, auth=AUTH).status_code == 400


def test_requires_basic_auth(store: MockStore) -> None:
    http = TestClient(make_app(store))
    assert http.get(f"{API_PREFIX}/orders").status_code == 401
    assert http.get(f"{API_PREFIX}/orders", auth=(API_KEY, "wrong")).status_code == 401


def test_modified_after_is_strict_and_respects_dates_are_gmt(store: MockStore) -> None:
    newest = store.max_modified_gmt("orders")
    http = TestClient(make_app(store))
    gmt = {"modified_after": newest.isoformat(), "dates_are_gmt": "true"}
    assert http.get(f"{API_PREFIX}/orders", params=gmt, auth=AUTH).json() == []
    one_sec = {"modified_after": (newest - timedelta(seconds=1)).isoformat(), "dates_are_gmt": "true"}
    assert len(http.get(f"{API_PREFIX}/orders", params=one_sec, auth=AUTH).json()) >= 1
    # Without dates_are_gmt the value is site-local (UTC+3): newest GMT read as local is 3h earlier in GMT.
    local = {"modified_after": newest.isoformat()}
    assert len(http.get(f"{API_PREFIX}/orders", params=local, auth=AUTH).json()) >= 1


def test_fault_injection_is_off_by_default(store: MockStore) -> None:
    served = list(make_client(make_app(store)).iter_records("orders"))
    assert served == sorted(store.records["orders"].values(), key=lambda o: o["id"])


def test_put_order_bumps_date_modified(store: MockStore) -> None:
    http = TestClient(make_app(store))
    order_id = next(iter(store.records["orders"]))
    resp = http.put(f"{API_PREFIX}/orders/{order_id}", json={"status": "on-hold"}, auth=AUTH)
    assert resp.status_code == 200 and resp.json()["status"] == "on-hold"
    assert datetime.fromisoformat(resp.json()["date_modified_gmt"]) > store.max_modified_gmt("orders") - timedelta(seconds=1)
    assert http.put(f"{API_PREFIX}/orders/{order_id}", json={"total": "1"}, auth=AUTH).status_code == 400
