"""Settings loaded from environment / .env."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "p3"
    postgres_password: str = ""
    postgres_db: str = "p3"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5436

    store_api_url: str = "http://127.0.0.1:8008/wp-json/wc/v3"
    store_api_key: str = ""
    store_api_secret: str = ""

    cost_sheet_source: str = "data/costs.csv"

    data_dir: Path = Path("data")
    gen_seed: int = 42
    payment_fee_pct: Decimal = Decimal("2.75")
    payment_fee_fixed: Decimal = Decimal("1.00")

    def dsn(self, dbname: str | None = None) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={dbname or self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
