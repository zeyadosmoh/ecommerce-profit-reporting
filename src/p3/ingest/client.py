"""Paginated WooCommerce REST client with retries and exponential backoff."""

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from p3.config import Settings


class TransientHTTPError(Exception):
    """429 / 5xx: worth retrying."""


RETRYABLE = (httpx.TransportError, TransientHTTPError)


class StoreClient:
    def __init__(self, http: httpx.Client, base_url: str, auth: tuple[str, str], per_page: int = 100,
                 max_attempts: int = 5, backoff_initial: float = 0.5, backoff_max: float = 8.0) -> None:
        self.http, self.base_url, self.auth, self.per_page = http, base_url.rstrip("/"), auth, per_page
        self.retrying = Retrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=backoff_initial, max=backoff_max),
            retry=retry_if_exception_type(RETRYABLE),
            reraise=True,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "StoreClient":
        http = httpx.Client(timeout=httpx.Timeout(30.0))
        return cls(http, settings.store_api_url, (settings.store_api_key, settings.store_api_secret))

    def _get(self, path: str, params: dict) -> httpx.Response:
        for attempt in self.retrying:
            with attempt:
                resp = self.http.get(f"{self.base_url}/{path}", params=params, auth=self.auth)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise TransientHTTPError(f"GET {path} -> HTTP {resp.status_code}")
                resp.raise_for_status()  # other 4xx are not retryable (bad auth, bad params)
                return resp
        raise AssertionError("unreachable")

    def iter_records(self, entity: str, modified_after: datetime | None = None) -> Iterator[dict]:
        """Yield every record of `entity`, walking pages in stable id order until X-WP-TotalPages."""
        params: dict[str, str | int] = {"per_page": self.per_page, "orderby": "id", "order": "asc"}
        if modified_after is not None:
            params["modified_after"] = modified_after.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
            params["dates_are_gmt"] = "true"
        page = 1
        while True:
            resp = self._get(entity, {**params, "page": page})
            records = resp.json()
            if not isinstance(records, list):
                raise ValueError(f"{entity} page {page}: expected a JSON list")
            yield from records
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
            if page >= total_pages:
                return
            page += 1
