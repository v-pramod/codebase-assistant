import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.chunking.chunker import chunk_file
from app.core.errors import AppError
from app.indexing.indexer import index_chunks, promote_snapshot, stage_incremental_refresh
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import InMemoryChunkVectorStore
from app.ingestion.filtering import FileFilterLimits
from app.ingestion.refresh import plan_incremental_refresh, read_file_at_commit
from app.main import create_app


class StaticEmbeddingProvider:
    model = "openrouter/test-embedding"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.0] for text in texts]


def test_refresh_plan_compares_exact_commits_and_classifies_file_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text("def kept():\n    return 1\n")
    (repo / "old.py").write_text("OLD = True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    previous = _git(repo, "rev-parse", "HEAD")

    (repo / "app.py").write_text("def kept():\n    return 2\n")
    (repo / "new.py").write_text("NEW = True\n")
    (repo / "package.json").write_text('{"scripts": {}}\n')
    (repo / "old.py").unlink()
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "refresh")
    latest = _git(repo, "rev-parse", "HEAD")

    plan = plan_incremental_refresh(repo, previous, latest)

    assert plan.previous_commit == previous
    assert plan.latest_commit == latest
    assert plan.added == ["new.py", "package.json"]
    assert plan.changed == ["app.py"]
    assert plan.deleted == ["old.py"]
    assert plan.full_rebuild_available is True
    assert plan.warnings == [
        "Dependency or config files changed; review retrieval quality and consider a full rebuild."
    ]


def test_read_file_at_commit_rejects_unsafe_paths(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text("VALUE = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    commit = _git(repo, "rev-parse", "HEAD")

    assert read_file_at_commit(repo, commit, "app.py") == b"VALUE = 1\n"

    with pytest.raises(AppError):
        read_file_at_commit(repo, commit, "../app.py")


def test_failed_refresh_leaves_previous_snapshot_queryable(tmp_path: Path) -> None:
    provider = StaticEmbeddingProvider()
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "keyword.sqlite3")
    active_chunks = chunk_file("repo-1", "snap-active", "app.py", "def target():\n    return 1\n")
    pending_chunks = chunk_file("repo-1", "snap-pending", "app.py", "def target():\n    return 2\n")

    index_chunks("repo-1", "snap-active", active_chunks, provider, vector_store, keyword_index)
    index_chunks("repo-1", "snap-pending", pending_chunks, provider, vector_store, keyword_index)

    assert [hit.text for hit in keyword_index.search_active("repo-1", "snap-active", "target")] == [
        "def target():\n    return 1"
    ]
    assert vector_store.active_records("repo-1", "snap-active") != []


def test_incremental_refresh_stages_unchanged_and_reindexed_files_before_promotion(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "kept.py").write_text("def kept():\n    return 1\n")
    (repo / "changed.py").write_text("def changed():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    previous = _git(repo, "rev-parse", "HEAD")

    provider = StaticEmbeddingProvider()
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "keyword.sqlite3")
    index_chunks(
        "repo-1",
        "snap-active",
        chunk_file("repo-1", "snap-active", "kept.py", "def kept():\n    return 1\n"),
        provider,
        vector_store,
        keyword_index,
    )
    index_chunks(
        "repo-1",
        "snap-active",
        chunk_file("repo-1", "snap-active", "changed.py", "def changed():\n    return 1\n"),
        provider,
        vector_store,
        keyword_index,
    )

    (repo / "changed.py").write_text("def changed():\n    return 2\n")
    (repo / "added.py").write_text("def added():\n    return 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "refresh")
    latest = _git(repo, "rev-parse", "HEAD")
    plan = plan_incremental_refresh(repo, previous, latest)

    skipped = stage_incremental_refresh(
        "repo-1",
        "snap-active",
        "snap-pending",
        repo,
        plan,
        provider,
        vector_store,
        keyword_index,
        FileFilterLimits(max_file_bytes=1000),
    )

    assert skipped == {}
    pending_paths = {
        record.metadata["path"]
        for record in vector_store.active_records("repo-1", "snap-pending")
    }
    assert pending_paths == {
        "added.py",
        "changed.py",
        "kept.py",
    }
    assert [hit.path for hit in keyword_index.search_active("repo-1", "snap-pending", "kept")] == [
        "kept.py"
    ]
    assert "return 2" in keyword_index.search_active("repo-1", "snap-pending", "changed")[0].text


def test_successful_refresh_promotion_deactivates_previous_snapshot(tmp_path: Path) -> None:
    provider = StaticEmbeddingProvider()
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "keyword.sqlite3")
    index_chunks(
        "repo-1",
        "snap-active",
        chunk_file("repo-1", "snap-active", "app.py", "def target():\n    return 1\n"),
        provider,
        vector_store,
        keyword_index,
    )
    index_chunks(
        "repo-1",
        "snap-new",
        chunk_file("repo-1", "snap-new", "app.py", "def target():\n    return 2\n"),
        provider,
        vector_store,
        keyword_index,
    )

    promoted = promote_snapshot("repo-1", "snap-active", "snap-new", vector_store, keyword_index)

    assert promoted == "snap-new"
    assert vector_store.active_records("repo-1", "snap-active") == []
    assert keyword_index.search_active("repo-1", "snap-active", "target") == []
    assert vector_store.active_records("repo-1", "snap-new") != []


def test_refresh_endpoint_exposes_failure_state_before_initial_index() -> None:
    client = TestClient(create_app())
    submitted = client.post("/api/repositories", json={"url": "https://github.com/encode/httpx"})
    repo_id = submitted.json()["repository_id"]

    refreshed = client.post(f"/api/repositories/{repo_id}/refresh")

    assert refreshed.status_code == 202
    assert refreshed.json()["status"] == "failed"
    assert refreshed.json()["phase"] == "refresh_failed"
    polled = client.get(f"/api/ingestion-jobs/{refreshed.json()['job_id']}")
    assert polled.json()["status"] == "failed"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    return repo


def _git(repo: Path, *args: str) -> str:
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
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()
