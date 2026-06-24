from pathlib import Path

from fastapi.testclient import TestClient

from app.api import routes
from app.auth.passwords import hash_password
from app.auth.store import SQLiteUserStore
from app.auth.tokens import decode_access_token
from app.core.config import get_settings
from app.main import create_app


def _client_with_user(
    tmp_path: Path, username: str, password: str, *, is_active: bool = True
) -> TestClient:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user(username, hash_password(password))
    if not is_active:
        store.set_active(username, False)
    routes._user_store = store
    return TestClient(create_app())


def test_valid_credentials_return_token(tmp_path: Path) -> None:
    client = _client_with_user(tmp_path, "user", "s3cret-pass")

    response = client.post(
        "/api/auth/login", json={"username": "user", "password": "s3cret-pass"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert decode_access_token(body["access_token"], get_settings()) == "user"


def test_wrong_password_is_generic_401(tmp_path: Path) -> None:
    client = _client_with_user(tmp_path, "user", "s3cret-pass")

    response = client.post(
        "/api/auth/login", json={"username": "user", "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid username or password."


def test_unknown_username_is_generic_401(tmp_path: Path) -> None:
    client = _client_with_user(tmp_path, "user", "s3cret-pass")

    response = client.post(
        "/api/auth/login", json={"username": "nobody", "password": "s3cret-pass"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid username or password."


def test_inactive_user_is_401(tmp_path: Path) -> None:
    client = _client_with_user(
        tmp_path, "user", "s3cret-pass", is_active=False
    )

    response = client.post(
        "/api/auth/login", json={"username": "user", "password": "s3cret-pass"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid username or password."
