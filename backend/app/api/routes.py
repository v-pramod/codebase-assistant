from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import AppError
from app.ingestion.github import validate_github_repo_url
from app.jobs.ingestion import InMemoryRepositoryRegistry

router = APIRouter()
_registry = InMemoryRepositoryRegistry(Path("../data/backend/clones"))


class RepositorySubmission(BaseModel):
    url: str


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/diagnostics")
def diagnostics() -> dict[str, str | int]:
    return get_settings().diagnostics()


@router.post("/repositories", status_code=202)
def submit_repository(payload: RepositorySubmission) -> dict[str, str]:
    repo_url = validate_github_repo_url(payload.url)
    repository = _registry.submit(repo_url)
    return {
        "repository_id": repository.repo_id,
        "url": repository.url,
        "job_id": repository.job.job_id,
        "status": repository.job.status,
        "phase": repository.job.phase,
    }


@router.get("/ingestion-jobs/{job_id}")
def get_ingestion_job(job_id: str) -> dict[str, object]:
    job = _registry.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "Ingestion job was not found.", status_code=404)
    return {
        "job_id": job.job_id,
        "repository_id": job.repo_id,
        "status": job.status,
        "phase": job.phase,
        "error": job.error,
        "warnings": job.warnings,
        "skipped": job.skipped,
    }
