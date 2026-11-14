"""Shared fixtures. Tests use a separate `p3_test` database in the same Postgres container and run the
mock API in-process (ASGI via TestClient), so no server needs to be running."""

from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from p3.config import Settings
from p3.db.connection import apply_schema, drop_schema, ensure_database
from p3.generate import Dataset, costs_csv, generate_dataset
from p3.generate.store import StoreConfig
from p3.ingest.client import StoreClient
from p3.ingest.pipeline import RunResult, run_ingest
from p3.mock_api.app import API_PREFIX, create_app
from p3.mock_api.store import MockStore

TEST_DB = "p3_test"
API_KEY, API_SECRET = "ck_test", "cs_test"
FIXED_NOW = datetime(2026, 11, 9, 9, 0, 0)  # store-local clock for upstream edits made in tests


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = Settings()
    if not s.postgres_password:
        pytest.exit("POSTGRES_PASSWORD not set: run `python3 scripts/setup_env.py` and `docker compose up -d --wait`")
    try:
        ensure_database(s, TEST_DB)
    except psycopg.OperationalError as exc:
        pytest.exit(f"Postgres not reachable on {s.postgres_host}:{s.postgres_port} ({exc}). Run `docker compose up -d --wait`.")
    return s


@pytest.fixture(scope="session")
def dsn(settings: Settings) -> str:
    return settings.dsn(TEST_DB)


@pytest.fixture
def db(dsn: str) -> Iterator[psycopg.Connection]:
    """Fresh raw schema for every test."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        drop_schema(conn)
        apply_schema(conn)
        yield conn


@pytest.fixture(scope="session")
def small_dataset() -> Dataset:
    return generate_dataset(StoreConfig(seed=7, target_orders=400))


@pytest.fixture
def store(small_dataset: Dataset) -> MockStore:
    return MockStore(small_dataset.products, small_dataset.orders, small_dataset.refunds, clock=lambda: FIXED_NOW)


@pytest.fixture
def cost_file(tmp_path: Path, small_dataset: Dataset) -> Path:
    path = tmp_path / "costs.csv"
    path.write_text(costs_csv(small_dataset.costs))
    return path


def make_app(store: MockStore, inject_faults: int = 0) -> FastAPI:
    return create_app(store, API_KEY, API_SECRET, inject_faults=inject_faults)


def make_client(app: FastAPI, per_page: int = 100) -> StoreClient:
    return StoreClient(TestClient(app), f"http://testserver{API_PREFIX}", (API_KEY, API_SECRET),
                       per_page=per_page, backoff_initial=0)


@pytest.fixture
def ingest(db: psycopg.Connection, dsn: str, cost_file: Path) -> Callable[..., RunResult]:
    def _run(app: FastAPI, cost_source: str | None = None) -> RunResult:
        return run_ingest(dsn, make_client(app), cost_source or str(cost_file))
    return _run


def count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]


@pytest.fixture(scope="module")
def built(settings: Settings, dsn: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[psycopg.Connection]:
    """Seed-42 dataset ingested into p3_test, then `dbt build --target test` (all models + dbt tests).
    Module-scoped: other test modules reset the raw schema, so each dbt module rebuilds."""
    from p3.dbt_runner import run_dbt

    ds = generate_dataset(StoreConfig(seed=42))
    cost_file = tmp_path_factory.mktemp("costs") / "costs.csv"
    cost_file.write_text(costs_csv(ds.costs))
    with psycopg.connect(dsn, autocommit=True) as conn:
        drop_schema(conn)
        apply_schema(conn)
        run_ingest(dsn, make_client(make_app(MockStore(ds.products, ds.orders, ds.refunds))), str(cost_file))
        result = run_dbt(["build", "--fail-fast"], settings, target="test", quiet=True)
        assert result.returncode == 0, f"dbt build failed:\n{result.stdout[-6000:]}"
        yield conn
