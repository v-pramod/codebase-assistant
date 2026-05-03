from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
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


def test_repository_submission_exposes_pollable_job_status() -> None:
    client = TestClient(create_app())

    submitted = client.post("/api/repositories", json={"url": "https://github.com/encode/httpx"})

    assert submitted.status_code == 202
    body = submitted.json()
    assert body["status"] == "queued"
    assert body["phase"] == "queued"

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
