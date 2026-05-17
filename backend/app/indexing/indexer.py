from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.chunking.chunker import CodeChunk, chunk_file
from app.core.errors import AppError
from app.indexing.embeddings import EmbeddingProvider
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import ChunkVectorStore, VectorRecord
from app.ingestion.filtering import (
    FileFilterLimits,
    RepositoryFile,
    filter_repository_files,
    gitignore_spec_from_bytes,
)
from app.ingestion.refresh import RefreshPlan, read_file_at_commit


@dataclass(frozen=True)
class IndexingOptions:
    embedding_batch_size: int = 32


def index_chunks(
    repo_id: str,
    snapshot_id: str,
    chunks: list[CodeChunk],
    embedding_provider: EmbeddingProvider,
    vector_store: ChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
    options: IndexingOptions | None = None,
) -> None:
    options = options or IndexingOptions()
    for start in range(0, len(chunks), options.embedding_batch_size):
        batch = chunks[start : start + options.embedding_batch_size]
        embeddings = embedding_provider.embed_texts([chunk.content for chunk in batch])
        if len(embeddings) != len(batch):
            raise ValueError("Embedding provider returned the wrong number of embeddings.")
        vector_store.add_records(
            [
                VectorRecord(
                    chunk_id=chunk.chunk_id,
                    embedding=embeddings[index],
                    text=chunk.content,
                    metadata={
                        "repo_id": repo_id,
                        "snapshot_id": snapshot_id,
                        "path": chunk.path,
                        "symbol_name": chunk.symbol_name,
                        "symbol_type": chunk.symbol_type,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "content_hash": sha256(chunk.content.encode()).hexdigest(),
                        "active": True,
                    },
                )
                for index, chunk in enumerate(batch)
            ],
            embedding_provider.model,
        )
    keyword_index.add_chunks(repo_id, snapshot_id, chunks)


def promote_snapshot(
    repo_id: str,
    previous_snapshot_id: str | None,
    new_snapshot_id: str,
    vector_store: ChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
) -> str:
    if previous_snapshot_id is not None and previous_snapshot_id != new_snapshot_id:
        vector_store.deactivate_snapshot(repo_id, previous_snapshot_id)
        keyword_index.deactivate_snapshot(repo_id, previous_snapshot_id)
    return new_snapshot_id


def stage_incremental_refresh(
    repo_id: str,
    previous_snapshot_id: str,
    pending_snapshot_id: str,
    repo_path: Path,
    plan: RefreshPlan,
    embedding_provider: EmbeddingProvider,
    vector_store: ChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
    limits: FileFilterLimits,
    options: IndexingOptions | None = None,
) -> dict[str, int]:
    replaced_paths = set(plan.added) | set(plan.changed) | set(plan.deleted)
    vector_store.copy_active_records(
        repo_id, previous_snapshot_id, pending_snapshot_id, replaced_paths
    )
    keyword_index.copy_active_chunks(
        repo_id, previous_snapshot_id, pending_snapshot_id, replaced_paths
    )
    candidates = [
        RepositoryFile(path, read_file_at_commit(repo_path, plan.latest_commit, path))
        for path in [*plan.added, *plan.changed]
    ]
    gitignore_content = _read_optional_file_at_commit(repo_path, plan.latest_commit, ".gitignore")
    gitignore_spec = (
        gitignore_spec_from_bytes(gitignore_content) if gitignore_content is not None else None
    )
    report = filter_repository_files(candidates, limits, gitignore_spec)
    chunks: list[CodeChunk] = []
    content_by_path = {file.path: file.content for file in candidates}
    for decision in report.decisions:
        if not decision.indexable:
            continue
        content = content_by_path[decision.path].decode("utf-8", errors="replace")
        chunks.extend(chunk_file(repo_id, pending_snapshot_id, decision.path, content))
    index_chunks(
        repo_id,
        pending_snapshot_id,
        chunks,
        embedding_provider,
        vector_store,
        keyword_index,
        options,
    )
    return report.skipped_counts


def _read_optional_file_at_commit(repo_path: Path, commit: str, path: str) -> bytes | None:
    try:
        return read_file_at_commit(repo_path, commit, path)
    except AppError:
        return None
