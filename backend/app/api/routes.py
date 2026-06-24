import json
import logging
from collections.abc import Iterator
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.runtime import StreamDependencies, build_stream_dependencies
from app.auth.dependencies import get_current_user
from app.auth.passwords import verify_password
from app.auth.store import SQLiteUserStore, UserRecord
from app.auth.tokens import create_access_token
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
from app.jobs.initial_ingestion import ingest_repository_now
from app.jobs.queue import enqueue_repository_ingestion, sync_repository_from_queue

router = APIRouter()
public_router = APIRouter()
logger = logging.getLogger(__name__)
_settings = get_settings()
_registry = InMemoryRepositoryRegistry(
    _settings.clones_dir,
    _settings.sqlite_path if _settings.sqlite_path.is_absolute() else None,
)
_chat_store = SQLiteChatStore(_settings.data_dir / "chat.sqlite3")
_user_store = SQLiteUserStore(_settings.data_dir / "auth.sqlite3")
_stream_dependencies_override: StreamDependencies | None = None


class RepositorySubmission(BaseModel):
    url: str


class ChatSessionSubmission(BaseModel):
    title: str


class ChatMessageSubmission(BaseModel):
    content: str
    snapshot_id: str | None = None


class LoginSubmission(BaseModel):
    username: str
    password: str


@public_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@public_router.post("/auth/login")
def login(payload: LoginSubmission) -> dict[str, str]:
    invalid = AppError("unauthorized", "Invalid username or password.", 401)
    user = _user_store.get_by_username(payload.username)
    if user is None or not user.is_active:
        raise invalid
    if not verify_password(payload.password, user.password_hash):
        raise invalid
    token = create_access_token(user.username, get_settings())
    return {"access_token": token, "token_type": "bearer"}


@router.get("/auth/me")
def auth_me(user: UserRecord = Depends(get_current_user)) -> dict[str, object]:
    return {"username": user.username, "is_active": user.is_active}


@router.get("/diagnostics")
def diagnostics() -> dict[str, str | int]:
    return get_settings().diagnostics()


@router.post("/repositories", status_code=202)
async def submit_repository(payload: RepositorySubmission) -> dict[str, str]:
    repo_url = validate_github_repo_url(payload.url)
    repository = _registry.submit(repo_url)
    if repository.active_snapshot_id is None and repository.job.status == "queued":
        if _stream_dependencies_override is not None:
            try:
                dependencies = _stream_dependencies()
                ingest_repository_now(
                    repository,
                    get_settings(),
                    dependencies.embedding_provider,
                    dependencies.vector_store,
                    dependencies.keyword_index,
                )
            except Exception as exc:
                repository.job.status = "failed"
                repository.job.phase = "ingestion_failed"
                repository.job.error = _safe_error_message(exc)
        else:
            try:
                enqueue_repository_ingestion(repository, get_settings())
            except Exception as exc:
                repository.job.status = "failed"
                repository.job.phase = "ingestion_failed"
                repository.job.error = _safe_error_message(exc)
    return {
        "repository_id": repository.repo_id,
        "url": repository.url,
        "job_id": repository.job.job_id,
        "status": repository.job.status,
        "phase": repository.job.phase,
    }


@router.get("/repositories")
def list_repositories() -> dict[str, list[dict[str, object]]]:
    repositories = _registry.list_repositories()
    for repository in repositories:
        _sync_repository_from_queue(repository)
    return {"repositories": [_repository_payload(repo) for repo in repositories]}


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: str) -> dict[str, object]:
    repository = _registry.get_repository(repository_id)
    if repository is None:
        raise AppError("repository_not_found", "Repository was not found.", 404)
    _sync_repository_from_queue(repository)
    return _repository_payload(repository)


@router.post("/repositories/{repository_id}/refresh", status_code=202)
async def refresh_repository(repository_id: str) -> dict[str, object]:
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
    _sync_repository_for_job(job_id)
    job = _registry.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "Ingestion job was not found.", status_code=404)
    return {
        "job_id": job.job_id,
        "repository_id": job.repo_id,
        "status": job.status,
        "phase": job.phase,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
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


@router.patch("/chat-sessions/{session_id}")
def update_chat_session(session_id: str, payload: ChatSessionSubmission) -> dict[str, str]:
    session = _chat_store.update_session_title(session_id, payload.title)
    if session is None:
        raise AppError("chat_session_not_found", "Chat session was not found.", 404)
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
    session = _chat_store.get_session(session_id)
    repository = _registry.get_repository(session.repo_id) if session is not None else None
    return {
        "messages": [
            _message_payload(message, repository.active_snapshot_id if repository else None)
            for message in _chat_store.list_messages(session_id)
        ]
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
            if (
                repository.active_snapshot_id is not None
                and snapshot_id != repository.active_snapshot_id
            ):
                raise AppError(
                    "snapshot_not_active",
                    "Chat stream snapshot does not match the repository's active snapshot.",
                    409,
                    {
                        "active_snapshot_id": repository.active_snapshot_id,
                        "requested_snapshot_id": snapshot_id,
                    },
                )
            dependencies = _stream_dependencies()
            if not _chat_store.list_messages(session_id):
                generated_title = _summarize_question_as_title(
                    dependencies.chat_provider, payload.content
                )
                if generated_title:
                    _chat_store.update_session_title(session_id, generated_title)
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
                commit_sha=repository.active_commit,
                repo_url=repository.url,
            ):
                yield _sse(str(event["event"]), event["data"])
        except AppError as exc:
            yield _sse(
                "error",
                {"code": exc.code, "message": exc.message, "details": exc.details},
            )
        except Exception:
            logger.exception("Chat streaming failed before completion.")
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
        "progress_current": repo.job.progress_current,
        "progress_total": repo.job.progress_total,
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
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "warnings": job.warnings,
        "skipped": job.skipped,
        "full_rebuild_available": full_rebuild_available,
    }


def _summarize_question_as_title(chat_provider: object, question: str) -> str | None:
    answer = getattr(chat_provider, "answer", None)
    if not callable(answer):
        return _fallback_title(question)
    prompt = (
        "Summarize this user question as a concise chat title.\n"
        "Return only the title, 3 to 7 words, no quotes, no trailing punctuation.\n"
        f"Question: {question}"
    )
    try:
        raw = str(answer(prompt, []))
    except Exception:
        return _fallback_title(question)
    return _clean_title(raw) or _fallback_title(question)


def _fallback_title(question: str) -> str:
    words = question.strip().replace("\n", " ").split()
    return _clean_title(" ".join(words[:7])) or "Codebase Question"


def _clean_title(raw: str) -> str:
    title = raw.strip().strip("\"'`").strip()
    title = title.rstrip(".!?")
    words = title.split()
    if len(words) > 7:
        title = " ".join(words[:7])
    return title[:80]


def _message_payload(
    message: ChatMessageRecord, active_snapshot_id: str | None = None
) -> dict[str, object]:
    stale = bool(
        message.snapshot_id is not None
        and active_snapshot_id is not None
        and message.snapshot_id != active_snapshot_id
    )
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
                "commit_sha": citation.commit_sha,
                "local_ref": citation.local_ref,
                "github_permalink": citation.github_permalink,
                "stale": stale,
            }
            for citation in message.citations
        ],
    }


def _require_repository(repository_id: str) -> TrackedRepository:
    repository = _registry.get_repository(repository_id)
    if repository is None:
        raise AppError("repository_not_found", "Repository was not found.", 404)
    _sync_repository_from_queue(repository)
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


def _sync_repository_for_job(job_id: str) -> None:
    for repository in _registry.list_repositories():
        if repository.job.job_id == job_id:
            _sync_repository_from_queue(repository)
            return


def _sync_repository_from_queue(repository: TrackedRepository) -> None:
    sync_repository_from_queue(repository, get_settings())


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.message
    return str(exc) or "Repository ingestion failed."
