"""Serve the mock store in-process (ASGI, no HTTP server) for CI and the runner's demo mode."""

import secrets

from fastapi.testclient import TestClient

from p3.config import Settings
from p3.generate import load_store_files
from p3.ingest.client import StoreClient
from p3.mock_api.app import API_PREFIX, create_app
from p3.mock_api.store import MockStore


def in_process_store_client(settings: Settings) -> StoreClient:
    key = settings.store_api_key or "ck_" + secrets.token_hex(8)
    secret = settings.store_api_secret or "cs_" + secrets.token_hex(8)
    app = create_app(MockStore(*load_store_files(settings.data_dir)), key, secret)
    return StoreClient(TestClient(app), f"http://testserver{API_PREFIX}", (key, secret))
