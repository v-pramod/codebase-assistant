from dataclasses import dataclass
from hashlib import sha256

from app.chunking.chunker import CodeChunk
from app.indexing.embeddings import EmbeddingProvider
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import ChunkVectorStore, VectorRecord


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
