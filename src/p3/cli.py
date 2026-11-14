"""Command-line interface: python -m p3 <command>."""

import argparse
from datetime import date
from pathlib import Path

from p3 import SYNTHETIC_NOTICE
from p3.config import get_settings
from p3.db.connection import apply_schema, connect, drop_schema


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = get_settings()
    with connect(settings) as conn:
        if args.reset:
            drop_schema(conn)
            print("Dropped schema raw.")
        apply_schema(conn)
    print(f"Schema raw is ready in database {settings.postgres_db}.")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    from p3.generate import generate_dataset, write_dataset
    from p3.generate.store import StoreConfig

    settings = get_settings()
    cfg = StoreConfig(
        seed=args.seed if args.seed is not None else settings.gen_seed,
        fee_pct=settings.payment_fee_pct,
        fee_fixed=settings.payment_fee_fixed,
    )
    out_dir = args.out or settings.data_dir
    manifest = write_dataset(generate_dataset(cfg), out_dir, cfg)
    print(SYNTHETIC_NOTICE)
    print(f"Wrote dataset to {out_dir}/ (seed {cfg.seed}):")
    for name, count in manifest["counts"].items():
        print(f"  {name:<12} {count:>6}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from p3.generate import load_store_files
    from p3.mock_api.app import create_app
    from p3.mock_api.store import MockStore

    settings = get_settings()
    store = MockStore(*load_store_files(settings.data_dir))
    app = create_app(store, settings.store_api_key, settings.store_api_secret, inject_faults=args.inject_faults)
    print(SYNTHETIC_NOTICE)
    if args.inject_faults:
        print(f"WARNING: fault injection ON - {args.inject_faults} orders will be served malformed.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from p3.ingest.client import StoreClient
    from p3.ingest.pipeline import run_ingest

    settings = get_settings()
    client = StoreClient.from_settings(settings)
    try:
        result = run_ingest(settings.dsn(), client, args.cost_source or settings.cost_sheet_source)
    except Exception as exc:  # the run is already logged as failed in raw.etl_runs
        print(f"Ingestion FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(f"Run {result.run_id}: {result.status}")
    print(f"  {'entity':<10}{'read':>7}{'inserted':>10}{'updated':>9}{'unchanged':>11}{'rejected':>10}")
    for entity, s in result.stats.items():
        print(f"  {entity:<10}{s.read:>7}{s.inserted:>10}{s.updated:>9}{s.unchanged:>11}{s.rejected:>10}")
    print(f"  order lines written: {result.stats['orders'].extra.get('lines_written', 0)}")
    print(f"  totals: read={result.rows_read} upserted={result.rows_upserted} rejected={result.rows_rejected}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    from p3.summary import build_summary

    with connect(get_settings()) as conn:
        print(build_summary(conn))
    return 0


def cmd_dbt(args: argparse.Namespace) -> int:
    from p3.dbt_runner import run_dbt

    return run_dbt(args.dbt_args, get_settings(), target=args.target).returncode


def cmd_profit_summary(args: argparse.Namespace) -> int:
    from p3.profit_summary import build_profit_summary

    with connect(get_settings()) as conn:
        print(build_profit_summary(conn, args.week))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from p3.report.build import build_report
    from p3.report.facts import FactsError

    settings = get_settings()
    with connect(settings) as conn:
        try:
            files = build_report(conn, settings, week=args.week, lang=args.lang, use_ai=not args.no_ai,
                                 out_dir=args.out, preview=args.preview)
        except (FactsError, ValueError) as exc:
            print(f"Report failed: {exc}")
            return 1
    s = files.summary
    print(SYNTHETIC_NOTICE)
    print(f"Wrote {files.directory}/: {files.facts.name}, {files.html.name}, {files.pdf.name}"
          + (f", {files.preview.name}" if files.preview else ""))
    print(f"Summary: source={s.source} status={s.status}" + (f" ({s.log.get('reason')})" if s.log.get("reason") else ""))
    if s.notice:
        print(f"  {s.notice}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="p3", description="E-commerce profit reporting (synthetic data)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create the raw schema and tables (idempotent)")
    p.add_argument("--reset", action="store_true", help="drop schema raw first (destroys loaded data)")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("generate", help="write the deterministic synthetic dataset to the data dir")
    p.add_argument("--seed", type=int, default=None, help="override GEN_SEED")
    p.add_argument("--out", type=Path, default=None, help="output directory (default DATA_DIR)")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("serve", help="run the mock WooCommerce-style store API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8008)
    p.add_argument("--inject-faults", type=int, default=0, metavar="N",
                   help="serve N orders malformed to exercise the reject path (default 0 = off)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ingest", help="incremental load from the store API + cost sheet into raw tables")
    p.add_argument("--cost-source", default=None, help="override COST_SHEET_SOURCE (file path or http(s) URL)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("summary", help="row counts, rejects, recent runs and reproduce commands")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("dbt", help="run project-local dbt with credentials from .env, e.g. `p3 dbt build`")
    p.add_argument("--target", default="dev", choices=["dev", "test"])
    p.add_argument("dbt_args", nargs=argparse.REMAINDER, help="arguments passed to dbt")
    p.set_defaults(func=cmd_dbt)

    p = sub.add_parser("profit-summary", help="last full week KPIs, top/bottom products, missing-cost warning")
    p.add_argument("--week", type=date.fromisoformat, default=None, help="ISO week Monday (default: last full week)")
    p.set_defaults(func=cmd_profit_summary)

    p = sub.add_parser("report", help="weekly report: facts.json, report.html, report.pdf (+ AI summary)")
    p.add_argument("--week", type=date.fromisoformat, default=None, help="ISO week Monday (default: last full week)")
    p.add_argument("--lang", choices=["en", "ar"], default=None, help="summary/report language (default REPORT_LANG)")
    p.add_argument("--no-ai", action="store_true", help="use the deterministic template summary")
    p.add_argument("--out", type=Path, default=None, help="output root (default REPORTS_DIR=reports)")
    p.add_argument("--preview", action="store_true", help="also write a PNG preview of page 1")
    p.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
