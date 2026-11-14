"""Append keys that exist in .env.example but are missing from .env. Existing lines are never changed.

Secrets (passwords, tokens, API keys) get a random value; other keys copy the example value.
Prints only key names, never values."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_env import REPLACEMENTS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists():
        print(".env does not exist; run scripts/setup_env.py first.")
        return 1
    present = {ln.split("=", 1)[0].strip() for ln in env.read_text().splitlines() if "=" in ln and not ln.lstrip().startswith("#")}
    missing = []
    for line in example.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key not in present:
            missing.append(f"{key}={REPLACEMENTS[key]()}" if key in REPLACEMENTS else line)
    if not missing:
        print("Nothing to append.")
        return 0
    text = env.read_text()
    with env.open("a") as f:
        f.write(("" if text.endswith("\n") else "\n") + "\n# appended by scripts/append_env.py\n" + "\n".join(missing) + "\n")
    print("Appended:", ", ".join(m.split("=", 1)[0] for m in missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
