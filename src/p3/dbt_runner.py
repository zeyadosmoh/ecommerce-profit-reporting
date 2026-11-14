"""Run the project-local dbt with credentials taken from .env (via Settings), never from committed files."""

import os
import subprocess
import sys
from pathlib import Path

from p3.config import Settings

DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"


def dbt_env(settings: Settings, target: str = "dev") -> dict[str, str]:
    return {
        **os.environ,
        "POSTGRES_HOST": settings.postgres_host,
        "POSTGRES_PORT": str(settings.postgres_port),
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password,
        "POSTGRES_DB": settings.postgres_db,
        "DBT_TARGET": target,
        "DBT_PROJECT_DIR": str(DBT_DIR),
        "DBT_PROFILES_DIR": str(DBT_DIR),
    }


def run_dbt(args: list[str], settings: Settings, target: str = "dev", quiet: bool = False) -> subprocess.CompletedProcess:
    dbt = Path(sys.executable).with_name("dbt")
    return subprocess.run([str(dbt), *args], env=dbt_env(settings, target), cwd=DBT_DIR,
                          capture_output=quiet, text=True, check=False)
