"""Deterministic synthetic dataset: products, orders, refunds (store API) + cost sheet CSV."""

import csv
import hashlib
import io
import json
import random
from dataclasses import dataclass
from pathlib import Path

from p3 import SYNTHETIC_NOTICE
from p3.generate.catalog import product_defs
from p3.generate.costs import COST_COLUMNS, generate_costs
from p3.generate.store import END, START, StoreConfig, generate_store


@dataclass
class Dataset:
    products: list[dict]
    orders: list[dict]
    refunds: list[dict]
    costs: list[dict[str, str]]


def generate_dataset(cfg: StoreConfig) -> Dataset:
    rng = random.Random(cfg.seed)
    defs = product_defs()
    costs = generate_costs(random.Random(f"{cfg.seed}-costs"), defs)
    store = generate_store(defs, cfg, rng)
    return Dataset(store.products, store.orders, store.refunds, costs)


def costs_csv(rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COST_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def write_dataset(ds: Dataset, out_dir: Path, cfg: StoreConfig) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "products.json": json.dumps(ds.products, indent=1, ensure_ascii=False),
        "orders.json": json.dumps(ds.orders, indent=1, ensure_ascii=False),
        "refunds.json": json.dumps(ds.refunds, indent=1, ensure_ascii=False),
        "costs.csv": costs_csv(ds.costs),
    }
    for name, content in files.items():
        (out_dir / name).write_text(content, encoding="utf-8")
    manifest = {
        "notice": SYNTHETIC_NOTICE,
        "seed": cfg.seed,
        "period": {"from": START.isoformat(), "to": END.isoformat()},
        "payment_fee": {"pct": str(cfg.fee_pct), "fixed": str(cfg.fee_fixed)},
        "counts": {
            "products": len(ds.products),
            "orders": len(ds.orders),
            "order_lines": sum(len(o["line_items"]) for o in ds.orders),
            "refunds": len(ds.refunds),
            "cost_rows": len(ds.costs),
        },
        "sha256": {name: hashlib.sha256(c.encode()).hexdigest() for name, c in files.items()},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_store_files(data_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    def read(name: str) -> list[dict]:
        return json.loads((data_dir / name).read_text(encoding="utf-8"))

    return read("products.json"), read("orders.json"), read("refunds.json")
