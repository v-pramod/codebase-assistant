from dataclasses import dataclass
from pathlib import Path

from app.chunking.chunker import chunk_file
from app.indexing.indexer import index_chunks
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import InMemoryChunkVectorStore, VectorRecord
from app.retrieval.answering import ChatMessage, Citation, answer_question


@dataclass
class DeterministicEmbeddingProvider:
    model: str = "openrouter/test-embedding"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] if "target" in text else [0.0, 1.0, 0.0] for text in texts]


@dataclass
class CitedChatProvider:
    model: str = "test-chat"
    prompts: list[str] | None = None

    def answer(self, prompt: str, citations: list[Citation]) -> str:
        if self.prompts is not None:
            self.prompts.append(prompt)
        return f"The target behavior is implemented in `{citations[0].label}`."


def test_answers_use_active_snapshot_evidence_and_inline_citations(tmp_path: Path) -> None:
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = DeterministicEmbeddingProvider()
    active = chunk_file("repo-1", "snap-active", "active.py", "def target():\n    return 1\n")
    stale = chunk_file("repo-1", "snap-stale", "stale.py", "def target():\n    return 2\n")
    index_chunks("repo-1", "snap-active", active, embedding_provider, vector_store, keyword_index)
    index_chunks("repo-1", "snap-stale", stale, embedding_provider, vector_store, keyword_index)

    result = answer_question(
        "repo-1",
        "snap-active",
        "target",
        [],
        embedding_provider,
        vector_store,
        keyword_index,
        CitedChatProvider(),
    )

    assert result.refused is False
    assert result.answer == "The target behavior is implemented in `active.py:1-2`."
    assert [
        (citation.path, citation.start_line, citation.end_line) for citation in result.citations
    ] == [("active.py", 1, 2)]


def test_prompt_uses_fresh_evidence_and_bounded_recent_chat(tmp_path: Path) -> None:
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = DeterministicEmbeddingProvider()
    chunks = chunk_file("repo-1", "snap-1", "active.py", "def target():\n    return 1\n")
    index_chunks("repo-1", "snap-1", chunks, embedding_provider, vector_store, keyword_index)
    prompts: list[str] = []

    result = answer_question(
        "repo-1",
        "snap-1",
        "target",
        [
            ChatMessage("user", "oldest"),
            ChatMessage("assistant", "older"),
            ChatMessage("user", "recent"),
        ],
        embedding_provider,
        vector_store,
        keyword_index,
        CitedChatProvider(prompts=prompts),
    )

    assert result.refused is False
    assert "active.py:1-2" in prompts[0]
    assert "recent" in prompts[0]
    assert "oldest" in prompts[0]


def test_weak_evidence_refuses_without_guessing(tmp_path: Path) -> None:
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = DeterministicEmbeddingProvider()
    chunks = chunk_file("repo-1", "snap-1", "other.py", "def unrelated():\n    pass\n")
    index_chunks("repo-1", "snap-1", chunks, embedding_provider, vector_store, keyword_index)

    result = answer_question(
        "repo-1",
        "snap-1",
        "target",
        [],
        embedding_provider,
        vector_store,
        keyword_index,
        CitedChatProvider(),
    )

    assert result.refused is True
    assert result.answer.startswith("I do not have enough indexed evidence")
    assert result.citations[0].path == "other.py"


@dataclass
class OrthogonalEmbeddingProvider:
    model: str = "test-embedding"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # The query embeds one way; any document embeds orthogonally, so cosine
        # similarity is ~0 even when the document contains the query token.
        return [[1.0, 0.0] if text == "config" else [0.0, 1.0] for text in texts]


def test_keyword_match_without_semantic_support_still_refuses(tmp_path: Path) -> None:
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = OrthogonalEmbeddingProvider()
    # Document literally contains "config" (so FTS matches) but embeds orthogonally.
    chunks = chunk_file("repo-1", "snap-1", "settings.py", "def load():\n    return config\n")
    index_chunks("repo-1", "snap-1", chunks, embedding_provider, vector_store, keyword_index)

    result = answer_question(
        "repo-1",
        "snap-1",
        "config",
        [],
        embedding_provider,
        vector_store,
        keyword_index,
        CitedChatProvider(),
    )

    assert result.refused is True


def test_query_similar_returns_top_matches_by_cosine() -> None:
    store = InMemoryChunkVectorStore()
    store.add_records(
        [
            VectorRecord(
                "a", [1.0, 0.0, 0.0], "alpha",
                {"repo_id": "r", "snapshot_id": "s", "active": True,
                 "path": "a.py", "start_line": 1, "end_line": 1},
            ),
            VectorRecord(
                "b", [0.0, 1.0, 0.0], "beta",
                {"repo_id": "r", "snapshot_id": "s", "active": True,
                 "path": "b.py", "start_line": 1, "end_line": 1},
            ),
        ],
        "test-embedding",
    )

    results = store.query_similar("r", "s", [1.0, 0.0, 0.0], 1)

    assert [record.chunk_id for record, _ in results] == ["a"]
    assert results[0][1] == 1.0
