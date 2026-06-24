from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.auth.passwords import hash_password
from app.auth.store import SQLiteUserStore
from app.main import create_app

TEST_USERNAME = "tester"
TEST_PASSWORD = "test-password"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """An authenticated TestClient backed by a seeded temp user store."""
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user(TEST_USERNAME, hash_password(TEST_PASSWORD))
    original = routes._user_store
    routes._user_store = store
    test_client = TestClient(create_app())
    response = test_client.post(
        "/api/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    token = response.json()["access_token"]
    test_client.headers.update({"Authorization": f"Bearer {token}"})
    try:
        yield test_client
    finally:
        routes._user_store = original
