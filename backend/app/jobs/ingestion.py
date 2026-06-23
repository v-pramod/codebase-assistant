import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from app.core.errors import AppError
from app.ingestion.github import GitHubRepositoryURL, validate_github_repo_url


@dataclass
class IngestionJobState:
    job_id: str
    repo_id: str
    status: str = "queued"
    phase: str = "queued"
    progress_current: int = 0
    progress_total: int = 5
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)


@dataclass
class TrackedRepository:
    repo_id: str
    url: str
    owner: str
    name: str
    local_path: Path
    job: IngestionJobState
    active_snapshot_id: str | None = None
    active_commit: str | None = None


class InMemoryRepositoryRegistry:
    def __init__(self, clones_dir: Path, sqlite_path: Path | None = None) -> None:
        self.clones_dir = clones_dir.resolve()
        self._repositories_by_url: dict[str, TrackedRepository] = {}
        self._jobs: dict[str, IngestionJobState] = {}
        if sqlite_path is not None:
            self._restore_indexed_repositories(sqlite_path)

    def submit(self, repo_url: GitHubRepositoryURL) -> TrackedRepository:
        existing = self._repositories_by_url.get(repo_url.canonical_url)
        if existing is not None:
            return existing
        repo_id = str(uuid4())
        job_id = str(uuid4())
        job = IngestionJobState(job_id=job_id, repo_id=repo_id)
        repository = TrackedRepository(
            repo_id=repo_id,
            url=repo_url.canonical_url,
            owner=repo_url.owner,
            name=repo_url.name,
            local_path=self.clones_dir / repo_id,
            job=job,
        )
        self._repositories_by_url[repo_url.canonical_url] = repository
        self._jobs[job_id] = job
        return repository

    def get_job(self, job_id: str) -> IngestionJobState | None:
        return self._jobs.get(job_id)

    def list_repositories(self) -> list[TrackedRepository]:
        return list(self._repositories_by_url.values())

    def get_repository(self, repo_id: str) -> TrackedRepository | None:
        for repository in self._repositories_by_url.values():
            if repository.repo_id == repo_id:
                return repository
        return None

    def start_refresh(self, repository: TrackedRepository) -> IngestionJobState:
        job = IngestionJobState(
            job_id=str(uuid4()),
            repo_id=repository.repo_id,
            status="queued",
            phase="refresh_queued",
        )
        repository.job = job
        self._jobs[job.job_id] = job
        return job

    def _restore_indexed_repositories(self, sqlite_path: Path) -> None:
        if not self.clones_dir.exists() or not sqlite_path.exists():
            return
        clone_paths = sorted(
            [path for path in self.clones_dir.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for clone_path in clone_paths:
            repo_id = clone_path.name
            active_snapshot_id = _active_snapshot_id(sqlite_path, repo_id)
            if active_snapshot_id is None:
                continue
            remote_url = _git_output(clone_path, "config", "--get", "remote.origin.url")
            if remote_url is None:
                continue
            try:
                repo_url = validate_github_repo_url(remote_url)
            except AppError:
                continue
            if repo_url.canonical_url in self._repositories_by_url:
                continue
            active_commit = _git_output(clone_path, "rev-parse", "HEAD")
            job = IngestionJobState(
                job_id=f"restored-{repo_id}",
                repo_id=repo_id,
                status="succeeded",
                phase="indexed",
                progress_current=5,
                progress_total=5,
            )
            repository = TrackedRepository(
                repo_id=repo_id,
                url=repo_url.canonical_url,
                owner=repo_url.owner,
                name=repo_url.name,
                local_path=clone_path,
                job=job,
                active_snapshot_id=active_snapshot_id,
                active_commit=active_commit,
            )
            self._repositories_by_url[repo_url.canonical_url] = repository
            self._jobs[job.job_id] = job


def _active_snapshot_id(sqlite_path: Path, repo_id: str) -> str | None:
    with sqlite3.connect(sqlite_path) as connection:
        row = connection.execute(
            """
            SELECT snapshot_id
            FROM chunk_keyword_index
            WHERE repo_id = ? AND active = 1
            GROUP BY snapshot_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
            (repo_id,),
        ).fetchone()
    return str(row[0]) if row is not None else None


def _git_output(repo_path: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None
