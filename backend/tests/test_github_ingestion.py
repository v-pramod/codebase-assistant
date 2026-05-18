import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.runtime import StreamDependencies
from app.core.errors import AppError
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import InMemoryChunkVectorStore
from app.ingestion.github import validate_github_repo_url
from app.jobs.ingestion import InMemoryRepositoryRegistry
from app.main import create_app


def test_github_url_validation_accepts_public_https_repo_urls() -> None:
    parsed = validate_github_repo_url("https://github.com/encode/httpx.git")

    assert parsed.canonical_url == "https://github.com/encode/httpx"
    assert parsed.owner == "encode"
    assert parsed.name == "httpx"


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:encode/httpx.git",
        "https://token@github.com/encode/httpx",
        "https://gitlab.com/encode/httpx",
        "file:///tmp/repo",
        "/tmp/repo",
        "https://github.com/encode",
        "https://github.com/encode/httpx/issues/1",
    ],
)
def test_github_url_validation_rejects_unsafe_inputs(url: str) -> None:
    with pytest.raises(AppError):
        validate_github_repo_url(url)


def test_repository_submission_enqueues_pollable_job_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueued: list[str] = []
    monkeypatch.setattr(
        routes,
        "enqueue_repository_ingestion",
        lambda repository, _settings: enqueued.append(repository.repo_id),
    )
    client = TestClient(create_app())

    submitted = client.post("/api/repositories", json={"url": "https://github.com/encode/httpx"})

    assert submitted.status_code == 202
    body = submitted.json()
    assert body["status"] == "queued"
    assert body["phase"] == "queued"
    assert enqueued == [body["repository_id"]]

    polled = client.get(f"/api/ingestion-jobs/{body['job_id']}")
    assert polled.status_code == 200
    assert polled.json()["repository_id"] == body["repository_id"]


def test_clone_paths_use_generated_internal_repo_ids(tmp_path: Path) -> None:
    registry = InMemoryRepositoryRegistry(tmp_path)
    repo_url = validate_github_repo_url("https://github.com/owner/repo-name")

    tracked = registry.submit(repo_url)

    assert tracked.local_path.parent == tmp_path.resolve()
    assert tracked.local_path.name == tracked.repo_id
    assert "repo-name" not in str(tracked.local_path)


class StaticEmbeddingProvider:
    model = "test/embedding"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.0] for text in texts]


class StaticChatProvider:
    model = "test/chat"

    def answer(self, prompt: str, citations: list[object]) -> str:
        return prompt


def test_repository_submission_runs_initial_ingestion_when_provider_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = _init_repo(tmp_path / "remote")
    (remote / "app.py").write_text("def target():\n    return 1\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-m", "initial")
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "keyword.sqlite3")
    routes.set_stream_dependencies_for_tests(
        StreamDependencies(
            embedding_provider=StaticEmbeddingProvider(),
            vector_store=vector_store,
            keyword_index=keyword_index,
            chat_provider=StaticChatProvider(),
        )
    )
    monkeypatch.setattr(routes, "validate_github_repo_url", lambda _url: _RepoURL(str(remote)))
    original_registry = routes._registry
    test_registry = InMemoryRepositoryRegistry(tmp_path / "clones")
    routes._registry = test_registry
    try:
        client = TestClient(create_app())
        submitted = client.post("/api/repositories", json={"url": "https://github.com/owner/repo"})
    finally:
        routes._registry = original_registry
        routes.set_stream_dependencies_for_tests(None)

    body = submitted.json()
    assert body["status"] == "succeeded"
    assert body["phase"] == "indexed"
    repository = test_registry.get_repository(body["repository_id"])
    assert repository is not None
    assert repository.active_snapshot_id is not None
    assert repository.active_commit is not None
    assert vector_store.active_records(repository.repo_id, repository.active_snapshot_id) != []


class _RepoURL:
    def __init__(self, canonical_url: str) -> None:
        self.canonical_url = canonical_url
        self.owner = "owner"
        self.name = "repo"


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    return path


def _git(path: Path, *args: str) -> str:
    command = ["git", *args]
    if args and args[0] == "commit":
        command = [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test User",
            *args,
        ]
    completed = subprocess.run(
        command,
        cwd=path,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()
