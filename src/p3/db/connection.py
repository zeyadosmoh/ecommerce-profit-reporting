"""Database helpers: connect, apply schema, reset."""

from importlib.resources import files

import psycopg

from p3.config import Settings

RAW_TABLES = (
    "raw_products",
    "raw_orders",
    "raw_order_lines",
    "raw_refunds",
    "raw_costs",
    "etl_runs",
    "etl_rejects",
    "etl_watermarks",
)


def connect(settings: Settings, dbname: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(settings.dsn(dbname), autocommit=autocommit)


def schema_sql() -> str:
    return files("p3.db").joinpath("schema.sql").read_text()


def apply_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(schema_sql())


def drop_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute("DROP SCHEMA IF EXISTS raw CASCADE")


def ensure_database(settings: Settings, dbname: str) -> None:
    """Create `dbname` in the same server if missing (used for the p3_test database)."""
    with connect(settings, settings.postgres_db, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{dbname}"')
