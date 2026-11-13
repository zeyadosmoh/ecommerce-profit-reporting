"""Command-line interface: python -m p3 <command>."""

import argparse

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="p3", description="E-commerce profit reporting (synthetic data)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create the raw schema and tables (idempotent)")
    p.add_argument("--reset", action="store_true", help="drop schema raw first (destroys loaded data)")
    p.set_defaults(func=cmd_init_db)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
