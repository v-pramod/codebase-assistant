from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from app.ingestion.github import GitHubRepositoryURL


@dataclass
class IngestionJobState:
    job_id: str
    repo_id: str
    status: str = "queued"
    phase: str = "queued"
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


class InMemoryRepositoryRegistry:
    def __init__(self, clones_dir: Path) -> None:
        self.clones_dir = clones_dir.resolve()
        self._repositories_by_url: dict[str, TrackedRepository] = {}
        self._jobs: dict[str, IngestionJobState] = {}

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
