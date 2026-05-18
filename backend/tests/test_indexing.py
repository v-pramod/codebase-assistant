from dataclasses import dataclass
from pathlib import Path

import pytest

from app.chunking.chunker import chunk_file
from app.indexing.embeddings import EmbeddingProviderError
from app.indexing.indexer import IndexingOptions, index_chunks
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import (
    ChromaChunkVectorStore,
    InMemoryChunkVectorStore,
    VectorStoreError,
)


@dataclass
class RecordingEmbeddingProvider:
    model: str = "openrouter/test-embedding"
    dimensions: int = 3
    fail: bool = False
    batches: list[list[str]] | None = None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise EmbeddingProviderError("provider failed")
        if self.batches is not None:
            self.batches.append(texts)
        return [[float(len(text)), float(index), 1.0] for index, text in enumerate(texts)]


def test_indexing_stores_vectors_and_keyword_text_for_active_snapshot(tmp_path: Path) -> None:
    chunks = chunk_file("repo-1", "snap-1", "app.py", "def target():\n    return 'needle'\n")
    provider = RecordingEmbeddingProvider(batches=[])
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")

    index_chunks(
        "repo-1",
        "snap-1",
        chunks,
        provider,
        vector_store,
        keyword_index,
        IndexingOptions(embedding_batch_size=1),
    )

    assert provider.batches == [[chunks[0].content]]
    vector_records = vector_store.active_records("repo-1", "snap-1")
    assert vector_records[0].metadata["path"] == "app.py"
    assert vector_records[0].metadata["symbol_name"] == "target"
    assert vector_records[0].metadata["active"] is True
    assert "content_hash" in vector_records[0].metadata

    hits = keyword_index.search_active("repo-1", "snap-1", "needle")
    assert [(hit.path, hit.start_line, hit.end_line) for hit in hits] == [("app.py", 1, 2)]


def test_keyword_search_accepts_natural_language_questions(tmp_path: Path) -> None:
    chunks = chunk_file(
        "repo-1",
        "snap-1",
        "pom.xml",
        "<!-- java version 17 -->\n<maven.compiler.source>17</maven.compiler.source>\n",
    )
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    keyword_index.add_chunks("repo-1", "snap-1", chunks)

    hits = keyword_index.search_active(
        "repo-1", "snap-1", "what is the java version used in this project?"
    )

    assert [hit.path for hit in hits] == ["pom.xml"]


def test_keyword_and_vector_queries_are_filtered_to_active_snapshot(tmp_path: Path) -> None:
    active = chunk_file("repo-1", "snap-active", "active.py", "def needle():\n    pass\n")
    stale = chunk_file("repo-1", "snap-stale", "stale.py", "def needle():\n    pass\n")
    provider = RecordingEmbeddingProvider()
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")

    index_chunks("repo-1", "snap-active", active, provider, vector_store, keyword_index)
    index_chunks("repo-1", "snap-stale", stale, provider, vector_store, keyword_index)

    assert [
        record.metadata["path"] for record in vector_store.active_records("repo-1", "snap-active")
    ] == ["active.py"]
    assert [hit.path for hit in keyword_index.search_active("repo-1", "snap-active", "needle")] == [
        "active.py"
    ]


def test_embedding_failures_fail_fast_without_writing_indexes(tmp_path: Path) -> None:
    chunks = chunk_file("repo-1", "snap-1", "app.py", "def target():\n    pass\n")
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")

    with pytest.raises(EmbeddingProviderError):
        index_chunks(
            "repo-1",
            "snap-1",
            chunks,
            RecordingEmbeddingProvider(fail=True),
            vector_store,
            keyword_index,
        )

    assert vector_store.active_records("repo-1", "snap-1") == []
    assert keyword_index.search_active("repo-1", "snap-1", "target") == []


def test_vector_store_rejects_model_or_dimension_mixing(tmp_path: Path) -> None:
    chunks = chunk_file("repo-1", "snap-1", "app.py", "def target():\n    pass\n")
    vector_store = InMemoryChunkVectorStore()

    index_chunks(
        "repo-1",
        "snap-1",
        chunks,
        RecordingEmbeddingProvider(model="model-a"),
        vector_store,
        SQLiteKeywordIndex(tmp_path / "first.sqlite3"),
    )

    with pytest.raises(VectorStoreError):
        index_chunks(
            "repo-1",
            "snap-2",
            chunks,
            RecordingEmbeddingProvider(model="model-b"),
            vector_store,
            SQLiteKeywordIndex(tmp_path / "second.sqlite3"),
        )


def test_chroma_indexing_accepts_chunks_without_symbol_metadata(tmp_path: Path) -> None:
    chunks = chunk_file("repo-1", "snap-1", "pom.xml", "<project>\n</project>\n")
    assert chunks[0].symbol_name is None
    vector_store = ChromaChunkVectorStore(str(tmp_path / "chroma"))
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")

    index_chunks(
        "repo-1",
        "snap-1",
        chunks,
        RecordingEmbeddingProvider(),
        vector_store,
        keyword_index,
    )

    records = vector_store.active_records("repo-1", "snap-1")
    assert [record.metadata["path"] for record in records] == ["pom.xml"]
    assert "symbol_name" not in records[0].metadata
