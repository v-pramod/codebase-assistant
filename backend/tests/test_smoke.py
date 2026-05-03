from fastapi.testclient import TestClient

from app.main import create_app


def test_backend_exposes_health_and_non_secret_diagnostics() -> None:
    client = TestClient(create_app())

    health = client.get("/api/health")
    diagnostics = client.get("/api/diagnostics")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert diagnostics.status_code == 200
    assert "clones_dir" in diagnostics.json()
    assert "OPENROUTER_API_KEY" not in diagnostics.text
