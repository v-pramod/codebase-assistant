from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.runtime import StreamDependencies
from app.chunking.chunker import chunk_file
from app.core.errors import AppError
from app.files.browser import list_file_tree, read_file_preview
from app.indexing.indexer import index_chunks
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import InMemoryChunkVectorStore
from app.ingestion.filtering import FileFilterLimits
from app.retrieval.answering import Citation


@dataclass
class DeterministicEmbeddingProvider:
    model: str = "openrouter/test-embedding"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] if "target" in text else [0.0, 1.0, 0.0] for text in texts]


@dataclass
class CitedChatProvider:
    model: str = "test-chat"

    def answer(self, prompt: str, citations: list[Citation]) -> str:
        return f"See `{citations[0].label}`."


def test_repository_selection_and_chat_session_endpoints_are_available(
    client: TestClient,
) -> None:
    submitted = client.post(
        "/api/repositories", json={"url": "https://github.com/encode/starlette"}
    )
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


def test_chat_stream_endpoint_returns_indexing_error_before_active_snapshot(
    client: TestClient,
) -> None:
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
    assert "repository_not_indexed" in response.text


def test_chat_stream_endpoint_uses_runtime_dependencies_for_sse(
    client: TestClient, tmp_path: Path
) -> None:
    submitted = client.post("/api/repositories", json={"url": "https://github.com/encode/sse"})
    repo_id = submitted.json()["repository_id"]
    repository = routes._registry.get_repository(repo_id)
    assert repository is not None
    repository.active_snapshot_id = "snap-1"
    created = client.post(f"/api/repositories/{repo_id}/chat-sessions", json={"title": "Chat"})
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = DeterministicEmbeddingProvider()
    chunks = chunk_file(repo_id, "snap-1", "app.py", "def target():\n    return 1\n")
    index_chunks(repo_id, "snap-1", chunks, embedding_provider, vector_store, keyword_index)
    routes.set_stream_dependencies_for_tests(
        StreamDependencies(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            keyword_index=keyword_index,
            chat_provider=CitedChatProvider(),
        )
    )
    try:
        response = client.post(
            f"/api/chat-sessions/{created.json()['session_id']}/messages/stream",
            json={"content": "what is target?"},
        )
    finally:
        routes.set_stream_dependencies_for_tests(None)

    assert "event: retrieval_started" in response.text
    assert "event: sources" in response.text
    assert "event: token" in response.text
    assert "event: final" in response.text
    assert "app.py" in response.text


def test_chat_stream_rejects_snapshot_from_another_repository(client: TestClient) -> None:
    submitted = client.post("/api/repositories", json={"url": "https://github.com/encode/active"})
    repo_id = submitted.json()["repository_id"]
    repository = routes._registry.get_repository(repo_id)
    assert repository is not None
    repository.active_snapshot_id = "snap-active"
    created = client.post(f"/api/repositories/{repo_id}/chat-sessions", json={"title": "Chat"})

    response = client.post(
        f"/api/chat-sessions/{created.json()['session_id']}/messages/stream",
        json={"content": "what is target?", "snapshot_id": "snap-other"},
    )

    assert "event: error" in response.text
    assert "snapshot_not_active" in response.text
    assert "event: retrieval_started" not in response.text


def test_file_browser_lists_skipped_files_and_blocks_unsafe_previews(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "compiled.class").write_bytes(b"\x00\x01")
    (tmp_path / ".gitignore").write_text("target/\n")
    (tmp_path / "src" / "app.py").write_text("def target():\n    return 1\n")
    (tmp_path / ".env").write_text("SECRET=value\n")
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02")

    tree = list_file_tree(tmp_path, FileFilterLimits(max_file_bytes=100))
    by_path = {entry.path: entry for entry in tree}

    assert by_path["src/app.py"].indexable is True
    assert by_path[".env"].skipped_reason == "secret"
    assert by_path["image.bin"].skipped_reason == "binary"
    assert ".git" not in by_path
    assert ".git/HEAD" not in by_path
    assert "target" not in by_path
    assert "target/compiled.class" not in by_path

    preview = read_file_preview(tmp_path, "src/app.py", FileFilterLimits(max_file_bytes=100))
    assert preview.previewable is True
    assert "def target" in (preview.content or "")

    blocked = read_file_preview(tmp_path, ".env", FileFilterLimits(max_file_bytes=100))
    assert blocked.previewable is False
    assert blocked.reason == "secret"

    ignored = read_file_preview(
        tmp_path, "target/compiled.class", FileFilterLimits(max_file_bytes=100)
    )
    assert ignored.previewable is False
    assert ignored.reason == "gitignored"

    with pytest.raises(AppError):
        read_file_preview(tmp_path, "../outside.py", FileFilterLimits(max_file_bytes=100))


def test_chat_message_payload_marks_old_citation_snapshots_stale(client: TestClient) -> None:
    submitted = client.post("/api/repositories", json={"url": "https://github.com/encode/stale"})
    repo_id = submitted.json()["repository_id"]
    repository = routes._registry.get_repository(repo_id)
    assert repository is not None
    repository.active_snapshot_id = "snap-new"
    created = client.post(f"/api/repositories/{repo_id}/chat-sessions", json={"title": "Chat"})
    session_id = created.json()["session_id"]
    routes._chat_store.add_message(
        session_id,
        "assistant",
        "See app.py",
        snapshot_id="snap-old",
        citations=[
            Citation(
                "app.py",
                1,
                2,
                "def old(): pass",
                "abc123",
                "/api/repositories/repo/files/content?path=app.py#L1-L2",
                "https://github.com/encode/stale/blob/abc123/app.py#L1-L2",
            )
        ],
    )

    messages = client.get(f"/api/chat-sessions/{session_id}/messages")

    citation = messages.json()["messages"][0]["citations"][0]
    assert citation["stale"] is True
    assert citation["commit_sha"] == "abc123"
    assert citation["github_permalink"].endswith("/blob/abc123/app.py#L1-L2")
