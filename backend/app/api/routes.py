import json
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.runtime import StreamDependencies, build_stream_dependencies
from app.chat.store import ChatMessageRecord, SQLiteChatStore
from app.chat.streaming import stream_chat_answer
from app.core.config import get_settings
from app.core.errors import AppError
from app.files.browser import list_file_tree, read_file_preview
from app.indexing.indexer import promote_snapshot, stage_incremental_refresh
from app.ingestion.filtering import FileFilterLimits
from app.ingestion.github import validate_github_repo_url
from app.ingestion.refresh import latest_default_branch_commit, plan_incremental_refresh
from app.jobs.ingestion import IngestionJobState, InMemoryRepositoryRegistry, TrackedRepository

router = APIRouter()
_registry = InMemoryRepositoryRegistry(Path("../data/backend/clones"))
_chat_store = SQLiteChatStore(Path("../data/backend/chat.sqlite3"))
_stream_dependencies_override: StreamDependencies | None = None


class RepositorySubmission(BaseModel):
    url: str


class ChatSessionSubmission(BaseModel):
    title: str


class ChatMessageSubmission(BaseModel):
    content: str
    snapshot_id: str | None = None


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


@router.get("/repositories")
def list_repositories() -> dict[str, list[dict[str, object]]]:
    return {"repositories": [_repository_payload(repo) for repo in _registry.list_repositories()]}


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: str) -> dict[str, object]:
    repository = _registry.get_repository(repository_id)
    if repository is None:
        raise AppError("repository_not_found", "Repository was not found.", 404)
    return _repository_payload(repository)


@router.post("/repositories/{repository_id}/refresh", status_code=202)
def refresh_repository(repository_id: str) -> dict[str, object]:
    repository = _require_repository(repository_id)
    job = _registry.start_refresh(repository)
    try:
        if repository.active_commit is None:
            raise AppError(
                "repository_not_indexed",
                "Repository must have an indexed commit before refresh.",
                409,
            )
        job.status = "running"
        job.phase = "planning_refresh"
        latest_commit = latest_default_branch_commit(repository.local_path)
        plan = plan_incremental_refresh(
            repository.local_path, repository.active_commit, latest_commit
        )
        job.warnings = plan.warnings
        job.skipped = {
            "added": len(plan.added),
            "changed": len(plan.changed),
            "deleted": len(plan.deleted),
            "unchanged": len(plan.unchanged),
        }
        if repository.active_snapshot_id is not None and latest_commit != repository.active_commit:
            job.phase = "staging_refresh"
            pending_snapshot_id = f"pending-{uuid4()}"
            dependencies = _stream_dependencies()
            skipped = stage_incremental_refresh(
                repository.repo_id,
                repository.active_snapshot_id,
                pending_snapshot_id,
                repository.local_path,
                plan,
                dependencies.embedding_provider,
                dependencies.vector_store,
                dependencies.keyword_index,
                _file_limits(),
            )
            job.skipped.update(skipped)
            job.phase = "promoting_refresh"
            repository.active_snapshot_id = promote_snapshot(
                repository.repo_id,
                repository.active_snapshot_id,
                pending_snapshot_id,
                dependencies.vector_store,
                dependencies.keyword_index,
            )
            repository.active_commit = latest_commit
        job.status = "succeeded"
        job.phase = "refresh_promoted"
        return _refresh_payload(job, plan.full_rebuild_available)
    except AppError as exc:
        job.status = "failed"
        job.phase = "refresh_failed"
        job.error = exc.message
        return _refresh_payload(job, False)


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


@router.post("/repositories/{repository_id}/chat-sessions", status_code=201)
def create_chat_session(repository_id: str, payload: ChatSessionSubmission) -> dict[str, str]:
    _require_repository(repository_id)
    session = _chat_store.create_session(repository_id, payload.title)
    return {
        "session_id": session.session_id,
        "repository_id": session.repo_id,
        "title": session.title,
    }


@router.get("/repositories/{repository_id}/chat-sessions")
def list_chat_sessions(repository_id: str) -> dict[str, list[dict[str, str]]]:
    _require_repository(repository_id)
    return {
        "sessions": [
            {
                "session_id": session.session_id,
                "repository_id": session.repo_id,
                "title": session.title,
            }
            for session in _chat_store.list_sessions(repository_id)
        ]
    }


@router.get("/chat-sessions/{session_id}/messages")
def list_chat_messages(session_id: str) -> dict[str, list[dict[str, object]]]:
    return {
        "messages": [_message_payload(message) for message in _chat_store.list_messages(session_id)]
    }


@router.post("/chat-sessions/{session_id}/messages/stream")
def stream_chat_message(session_id: str, payload: ChatMessageSubmission) -> StreamingResponse:
    def events() -> Iterator[str]:
        try:
            session = _chat_store.get_session(session_id)
            if session is None:
                raise AppError("chat_session_not_found", "Chat session was not found.", 404)
            repository = _require_repository(session.repo_id)
            snapshot_id = payload.snapshot_id or repository.active_snapshot_id
            if snapshot_id is None:
                raise AppError(
                    "repository_not_indexed",
                    "Repository has no active snapshot for chat streaming.",
                    409,
                )
            dependencies = _stream_dependencies()
            for event in stream_chat_answer(
                _chat_store,
                session_id,
                repository.repo_id,
                snapshot_id,
                payload.content,
                dependencies.embedding_provider,
                dependencies.vector_store,
                dependencies.keyword_index,
                dependencies.chat_provider,
            ):
                yield _sse(str(event["event"]), event["data"])
        except AppError as exc:
            yield _sse(
                "error",
                {"code": exc.code, "message": exc.message, "details": exc.details},
            )
        except Exception:
            yield _sse(
                "error",
                {
                    "code": "chat_stream_failed",
                    "message": "Chat streaming failed before completion.",
                },
            )

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/repositories/{repository_id}/files/tree")
def get_file_tree(repository_id: str) -> dict[str, list[dict[str, object]]]:
    repository = _require_repository(repository_id)
    limits = _file_limits()
    return {
        "entries": [
            {
                "path": entry.path,
                "kind": entry.kind,
                "indexable": entry.indexable,
                "skipped_reason": entry.skipped_reason,
                "size": entry.size,
            }
            for entry in list_file_tree(repository.local_path, limits)
        ]
    }


@router.get("/repositories/{repository_id}/files/content")
def get_file_content(repository_id: str, path: str) -> dict[str, object]:
    repository = _require_repository(repository_id)
    preview = read_file_preview(repository.local_path, path, _file_limits())
    return {
        "path": preview.path,
        "content": preview.content,
        "previewable": preview.previewable,
        "reason": preview.reason,
        "size": preview.size,
    }


def _repository_payload(repo: TrackedRepository) -> dict[str, object]:
    return {
        "repository_id": repo.repo_id,
        "url": repo.url,
        "owner": repo.owner,
        "name": repo.name,
        "status": repo.job.status,
        "phase": repo.job.phase,
        "warnings": repo.job.warnings,
        "skipped": repo.job.skipped,
        "active_snapshot_id": repo.active_snapshot_id,
        "active_commit": repo.active_commit,
    }


def _refresh_payload(job: IngestionJobState, full_rebuild_available: bool) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "repository_id": job.repo_id,
        "status": job.status,
        "phase": job.phase,
        "warnings": job.warnings,
        "skipped": job.skipped,
        "full_rebuild_available": full_rebuild_available,
    }


def _message_payload(message: ChatMessageRecord) -> dict[str, object]:
    return {
        "message_id": message.message_id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "model": message.model,
        "snapshot_id": message.snapshot_id,
        "citations": [
            {
                "path": citation.path,
                "start_line": citation.start_line,
                "end_line": citation.end_line,
                "snippet": citation.snippet,
            }
            for citation in message.citations
        ],
    }


def _require_repository(repository_id: str) -> TrackedRepository:
    repository = _registry.get_repository(repository_id)
    if repository is None:
        raise AppError("repository_not_found", "Repository was not found.", 404)
    return repository


def _file_limits() -> FileFilterLimits:
    settings = get_settings()
    return FileFilterLimits(max_file_bytes=settings.max_file_bytes)


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _stream_dependencies() -> StreamDependencies:
    if _stream_dependencies_override is not None:
        return _stream_dependencies_override
    return build_stream_dependencies(get_settings())


def set_stream_dependencies_for_tests(dependencies: StreamDependencies | None) -> None:
    global _stream_dependencies_override
    _stream_dependencies_override = dependencies
