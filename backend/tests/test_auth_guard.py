from fastapi.testclient import TestClient

from app.main import create_app

# Mirrors the seeded user in conftest.py's `client` fixture.
TEST_EMAIL = "tester@example.com"
TEST_PASSWORD = "test-password"


def test_protected_route_without_token_is_401() -> None:
    raw = TestClient(create_app())
    assert raw.get("/api/diagnostics").status_code == 401
    assert raw.get("/api/repositories").status_code == 401


def test_protected_route_with_valid_token_is_200(client: TestClient) -> None:
    assert client.get("/api/diagnostics").status_code == 200
    assert client.get("/api/repositories").status_code == 200


def test_health_is_public() -> None:
    raw = TestClient(create_app())
    assert raw.get("/api/health").status_code == 200


def test_login_is_public(client: TestClient) -> None:
    # The `client` fixture has repointed the user store at a seeded user.
    no_auth = TestClient(create_app())
    response = no_auth.post(
        "/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200


def test_malformed_token_is_401() -> None:
    raw = TestClient(create_app())
    raw.headers.update({"Authorization": "Bearer not-a-real-token"})
    assert raw.get("/api/diagnostics").status_code == 401


def test_auth_me_returns_current_user(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == TEST_EMAIL
