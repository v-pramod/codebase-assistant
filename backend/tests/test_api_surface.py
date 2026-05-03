from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.files.browser import list_file_tree, read_file_preview
from app.ingestion.filtering import FileFilterLimits
from app.main import create_app


def test_repository_selection_and_chat_session_endpoints_are_available() -> None:
    client = TestClient(create_app())

    submitted = client.post("/api/repositories", json={"url": "https://github.com/encode/starlette"})
    repo_id = submitted.json()["repository_id"]

    repositories = client.get("/api/repositories")
    assert repositories.status_code == 200
    assert repo_id in [repo["repository_id"] for repo in repositories.json()["repositories"]]

    created = client.post(
        f"/api/repositories/{repo_id}/chat-sessions", json={"title": "Understand routing"}
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    sessions = client.get(f"/api/repositories/{repo_id}/chat-sessions")
    assert sessions.json()["sessions"][0]["session_id"] == session_id

    messages = client.get(f"/api/chat-sessions/{session_id}/messages")
    assert messages.status_code == 200
    assert messages.json() == {"messages": []}


def test_chat_stream_endpoint_returns_sse_error_until_live_dependencies_are_configured() -> None:
    client = TestClient(create_app())

    submitted = client.post("/api/repositories", json={"url": "https://github.com/encode/httpcore"})
    repo_id = submitted.json()["repository_id"]
    created = client.post(f"/api/repositories/{repo_id}/chat-sessions", json={"title": "Chat"})

    response = client.post(
        f"/api/chat-sessions/{created.json()['session_id']}/messages/stream",
        json={"content": "Where is target?"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text
    assert "chat_stream_unavailable" in response.text


def test_file_browser_lists_skipped_files_and_blocks_unsafe_previews(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def target():\n    return 1\n")
    (tmp_path / ".env").write_text("SECRET=value\n")
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02")

    tree = list_file_tree(tmp_path, FileFilterLimits(max_file_bytes=100))
    by_path = {entry.path: entry for entry in tree}

    assert by_path["src/app.py"].indexable is True
    assert by_path[".env"].skipped_reason == "secret"
    assert by_path["image.bin"].skipped_reason == "binary"

    preview = read_file_preview(tmp_path, "src/app.py", FileFilterLimits(max_file_bytes=100))
    assert preview.previewable is True
    assert "def target" in (preview.content or "")

    blocked = read_file_preview(tmp_path, ".env", FileFilterLimits(max_file_bytes=100))
    assert blocked.previewable is False
    assert blocked.reason == "secret"

    with pytest.raises(AppError):
        read_file_preview(tmp_path, "../outside.py", FileFilterLimits(max_file_bytes=100))
