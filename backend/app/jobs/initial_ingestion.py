from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.chunking.chunker import ChunkingOptions, chunk_file
from app.core.config import Settings
from app.indexing.embeddings import EmbeddingProvider
from app.indexing.indexer import index_chunks
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import ChunkVectorStore
from app.ingestion.filtering import FileFilterLimits, RepositoryFile, filter_repository_files
from app.ingestion.refresh import clone_or_fetch_repository, list_repository_files_at_head
from app.jobs.ingestion import TrackedRepository

_TOTAL_STEPS = 5


def ingest_repository_now(
    repository: TrackedRepository,
    settings: Settings,
    embedding_provider: EmbeddingProvider,
    vector_store: ChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
    on_update: Callable[[TrackedRepository], None] | None = None,
) -> None:
    job = repository.job
    job.status = "running"
    job.progress_total = _TOTAL_STEPS
    job.progress_current = 0
    job.phase = "cloning"
    _publish(repository, on_update)
    commit = clone_or_fetch_repository(repository.url, repository.local_path)

    job.phase = "filtering_files"
    job.progress_current = 1
    _publish(repository, on_update)
    files = [
        _repository_file(repository.local_path, path)
        for path in list_repository_files_at_head(repository.local_path)
    ]
    limits = FileFilterLimits(
        max_file_bytes=settings.max_file_bytes,
        max_repo_bytes=settings.max_repo_bytes,
        max_indexed_files=settings.max_indexed_files,
    )
    report = filter_repository_files(files, limits)
    job.skipped = report.skipped_counts

    job.phase = "chunking"
    job.progress_current = 2
    _publish(repository, on_update)
    content_by_path = {file.path: file.content for file in files}
    snapshot_id = str(uuid4())
    chunks = []
    options = ChunkingOptions(
        max_lines=settings.chunk_max_lines,
        overlap_lines=settings.chunk_overlap_lines,
    )
    for decision in report.decisions:
        if not decision.indexable:
            continue
        content = content_by_path[decision.path].decode("utf-8", errors="replace")
        chunks.extend(chunk_file(repository.repo_id, snapshot_id, decision.path, content, options))

    job.phase = "embedding"
    job.progress_current = 3
    _publish(repository, on_update)
    index_chunks(
        repository.repo_id, snapshot_id, chunks, embedding_provider, vector_store, keyword_index
    )
    repository.active_snapshot_id = snapshot_id
    repository.active_commit = commit
    job.status = "succeeded"
    job.phase = "indexed"
    job.progress_current = _TOTAL_STEPS
    _publish(repository, on_update)


def _repository_file(repo_path: Path, relative_path: str) -> RepositoryFile:
    path = repo_path / relative_path
    return RepositoryFile(relative_path, path.read_bytes())


def _publish(
    repository: TrackedRepository, on_update: Callable[[TrackedRepository], None] | None
) -> None:
    if on_update is not None:
        on_update(repository)
