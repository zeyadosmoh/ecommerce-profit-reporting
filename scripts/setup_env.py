"""Create .env from .env.example with random secrets. Refuses to overwrite an existing .env."""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPLACEMENTS = {
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(24),
    "STORE_API_KEY": lambda: "ck_" + secrets.token_hex(20),
    "STORE_API_SECRET": lambda: "cs_" + secrets.token_hex(20),
    "RUNNER_API_TOKEN": lambda: secrets.token_urlsafe(32),
}


def main() -> int:
    env = ROOT / ".env"
    if env.exists():
        print(".env already exists; leaving it untouched.")
        return 0
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={REPLACEMENTS[key]()}" if key in REPLACEMENTS else line)
    env.write_text("\n".join(lines) + "\n")
    env.chmod(0o600)
    print("Wrote .env with random secrets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
