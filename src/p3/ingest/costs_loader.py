"""Cost sheet loader. The source is a local CSV path or an http(s) URL (e.g. a Google Sheets CSV export)."""

import csv
import io
from pathlib import Path

import httpx

REQUIRED_COLUMNS = ("sku", "unit_cost", "packaging_cost", "effective_from")


class CostSheetError(Exception):
    """Structural problem with the whole sheet (unreachable, wrong columns): fails the run."""


def read_cost_source(source: str, http: httpx.Client | None = None) -> str:
    if source.startswith(("http://", "https://")):
        client = http or httpx.Client(timeout=30.0, follow_redirects=True)
        resp = client.get(source)
        if resp.status_code != 200:
            raise CostSheetError(f"cost sheet URL returned HTTP {resp.status_code}")
        return resp.text
    path = Path(source)
    if not path.is_file():
        raise CostSheetError(f"cost sheet file not found: {source}")
    return path.read_text(encoding="utf-8-sig")  # tolerate the BOM some spreadsheet exports add


def parse_cost_sheet(text: str) -> list[tuple[int, dict[str, str]]]:
    """Return (sheet row number, row) pairs. Row numbers match what the owner sees in the sheet."""
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise CostSheetError(f"cost sheet is missing column(s): {', '.join(missing)}")
    reader.fieldnames = header
    rows = []
    for rownum, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue  # blank spreadsheet row
        rows.append((rownum, {k: (v or "").strip() for k, v in row.items() if k}))
    return rows
