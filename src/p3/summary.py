"""Plain-text status summary of the raw layer."""

import psycopg

from p3 import SYNTHETIC_NOTICE
from p3.db.connection import RAW_TABLES

REPRODUCE = """\
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
python3 scripts/setup_env.py                 # .env with random secrets (never overwrites)
docker compose up -d --wait                  # Postgres 16 -> 127.0.0.1:5436
.venv/bin/python -m p3 generate              # deterministic synthetic data -> data/
.venv/bin/python -m p3 init-db               # raw schema (idempotent)
.venv/bin/python -m p3 serve &               # mock store API on 127.0.0.1:8008
.venv/bin/python -m p3 ingest                # run twice: the 2nd run upserts 0 rows
.venv/bin/python -m p3 dbt build             # profit models + dbt tests
.venv/bin/python -m pytest                   # uses the separate p3_test database
.venv/bin/python -m p3 summary
.venv/bin/python -m p3 profit-summary"""


def build_summary(conn: psycopg.Connection) -> str:
    out = [SYNTHETIC_NOTICE, "", "Raw table row counts:"]
    for table in RAW_TABLES:
        n = conn.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
        out.append(f"  raw.{table:<16}{n:>8}")

    out += ["", "Rejects (all runs):"]
    rejects = conn.execute(
        """SELECT entity, split_part(reason, ':', 1) AS field, count(*) FROM raw.etl_rejects
           GROUP BY 1, 2 ORDER BY 1, 3 DESC"""
    ).fetchall()
    out += [f"  {e:<9} {f:<34} {n:>5}" for e, f, n in rejects] or ["  none"]

    out += ["", "Last runs:"]
    runs = conn.execute(
        """SELECT left(run_id::text, 8), to_char(started_at, 'YYYY-MM-DD HH24:MI:SS'), status,
                  rows_read, rows_upserted, rows_rejected, coalesce(left(error, 60), '')
           FROM raw.etl_runs ORDER BY started_at DESC LIMIT 5"""
    ).fetchall()
    out.append(f"  {'run':<9}{'started (UTC)':<21}{'status':<9}{'read':>6}{'upserted':>10}{'rejected':>10}  error")
    out += [f"  {r[0]:<9}{r[1]:<21}{r[2]:<9}{r[3]:>6}{r[4]:>10}{r[5]:>10}  {r[6]}" for r in runs] or ["  none"]

    out += ["", "Watermarks:"]
    wms = conn.execute("SELECT entity, watermark FROM raw.etl_watermarks ORDER BY entity").fetchall()
    out += [f"  {e:<9} {w.isoformat()}" for e, w in wms] or ["  none"]

    missing = conn.execute(
        """SELECT l.sku, count(*) FROM raw.raw_order_lines l
           WHERE NOT EXISTS (SELECT 1 FROM raw.raw_costs c WHERE c.sku = l.sku)
           GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    out += ["", "SKUs sold with NO cost row (to be flagged in the profit models, never treated as 0):"]
    out += [f"  {sku:<9} {n:>5} order lines" for sku, n in missing] or ["  none"]

    out += ["", "Reproduce from zero:", REPRODUCE]
    return "\n".join(out)
