"""Command-line interface: python -m p3 <command>."""

import argparse
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
