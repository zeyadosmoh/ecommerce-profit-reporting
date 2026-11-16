"""Render .pptx decks to one PNG per slide with the local LibreOffice Docker image (nothing installed on the host).

    docker build -t p3-deck-preview docker/deck-preview
    python scripts/render_deck_previews.py docs/sample-report/2026-W45 [--out DIR]

Writes <out>/report-01.png ... and report.ar-01.png ... (LibreOffice renders: fonts and line breaks can
differ slightly from PowerPoint).
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

IMAGE = "p3-deck-preview"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("week_dir", type=Path, help="folder that holds report.pptx / report.ar.pptx")
    ap.add_argument("--out", type=Path, default=None, help="PNG output folder (default: WEEK_DIR/deck-preview)")
    args = ap.parse_args()
    week_dir = args.week_dir.resolve()
    out = (args.out or week_dir / "deck-preview").resolve()
    out.mkdir(parents=True, exist_ok=True)
    decks = sorted(p.name for p in week_dir.glob("report*.pptx"))
    if not decks:
        print(f"no report*.pptx in {week_dir}", file=sys.stderr)
        return 1
    for old in out.glob("report*-[0-9][0-9].png"):
        old.unlink()
    cmd = ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
           "-e", "HOME=/tmp", "-v", f"{week_dir}:/in:ro", "-v", f"{out}:/out", IMAGE, "/in", "/out", *decks]
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
