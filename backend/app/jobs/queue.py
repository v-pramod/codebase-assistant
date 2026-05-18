from typing import Any

from redis import Redis
from rq import Queue, get_current_job

from app.api.runtime import build_stream_dependencies
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.jobs.ingestion import TrackedRepository
from app.jobs.initial_ingestion import ingest_repository_now

INGESTION_QUEUE_NAME = "ingestion"
INGESTION_STATE_META_KEY = "ingestion_state"


def enqueue_repository_ingestion(repository: TrackedRepository, settings: Settings) -> None:
    queue = _ingestion_queue(settings)
    if queue.fetch_job(repository.job.job_id) is not None:
        return
    queue.enqueue(run_repository_ingestion, repository, job_id=repository.job.job_id)


def run_repository_ingestion(repository: TrackedRepository) -> dict[str, Any]:
    def publish(updated: TrackedRepository) -> None:
        current_job = get_current_job()
        if current_job is None:
            return
        current_job.meta[INGESTION_STATE_META_KEY] = _repository_state(updated)
        current_job.save_meta()

    try:
        settings = get_settings()
        dependencies = build_stream_dependencies(settings)
        ingest_repository_now(
            repository,
            settings,
            dependencies.embedding_provider,
            dependencies.vector_store,
            dependencies.keyword_index,
            on_update=publish,
        )
    except Exception as exc:
        repository.job.status = "failed"
        repository.job.phase = "ingestion_failed"
        repository.job.error = _safe_error_message(exc)
        publish(repository)
    return _repository_state(repository)


def sync_repository_from_queue(repository: TrackedRepository, settings: Settings) -> None:
    try:
        queue = _ingestion_queue(settings)
        job = queue.fetch_job(repository.job.job_id)
    except Exception:
        return
    if job is None:
        return
    state = job.meta.get(INGESTION_STATE_META_KEY)
    if not isinstance(state, dict):
        state = job.return_value()
    if isinstance(state, dict):
        apply_repository_state(repository, state)


def apply_repository_state(repository: TrackedRepository, state: dict[str, Any]) -> None:
    if state.get("repository_id") != repository.repo_id:
        return
    repository.job.status = str(state.get("status") or repository.job.status)
    repository.job.phase = str(state.get("phase") or repository.job.phase)
    repository.job.progress_current = int(
        state.get("progress_current") or repository.job.progress_current
    )
    repository.job.progress_total = int(state.get("progress_total") or repository.job.progress_total)
    error = state.get("error")
    repository.job.error = str(error) if error is not None else None
    warnings = state.get("warnings")
    if isinstance(warnings, list):
        repository.job.warnings = [str(warning) for warning in warnings]
    skipped = state.get("skipped")
    if isinstance(skipped, dict):
        repository.job.skipped = {
            str(key): int(value) for key, value in skipped.items() if isinstance(value, int)
        }
    active_snapshot_id = state.get("active_snapshot_id")
    repository.active_snapshot_id = (
        str(active_snapshot_id) if active_snapshot_id is not None else None
    )
    active_commit = state.get("active_commit")
    repository.active_commit = str(active_commit) if active_commit is not None else None


def _ingestion_queue(settings: Settings) -> Queue:
    return Queue(INGESTION_QUEUE_NAME, connection=Redis.from_url(settings.redis_url))


def _repository_state(repository: TrackedRepository) -> dict[str, Any]:
    return {
        "repository_id": repository.repo_id,
        "status": repository.job.status,
        "phase": repository.job.phase,
        "progress_current": repository.job.progress_current,
        "progress_total": repository.job.progress_total,
        "error": repository.job.error,
        "warnings": repository.job.warnings,
        "skipped": repository.job.skipped,
        "active_snapshot_id": repository.active_snapshot_id,
        "active_commit": repository.active_commit,
    }


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.message
    return str(exc) or "Repository ingestion failed."
