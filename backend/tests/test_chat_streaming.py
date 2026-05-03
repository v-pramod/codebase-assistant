from dataclasses import dataclass
from pathlib import Path

from app.chat.store import SQLiteChatStore
from app.chat.streaming import stream_chat_answer
from app.chunking.chunker import chunk_file
from app.indexing.indexer import index_chunks
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import InMemoryChunkVectorStore
from app.retrieval.answering import AnsweringOptions, Citation


@dataclass
class DeterministicEmbeddingProvider:
    model: str = "openrouter/test-embedding"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] if "target" in text else [0.0, 1.0, 0.0] for text in texts]


@dataclass
class RecordingChatProvider:
    model: str = "test-chat"
    prompts: list[str] | None = None

    def answer(self, prompt: str, citations: list[Citation]) -> str:
        if self.prompts is not None:
            self.prompts.append(prompt)
        return f"See `{citations[0].label}`."


def test_users_can_create_list_sessions_and_retrieve_messages(tmp_path: Path) -> None:
    store = SQLiteChatStore(tmp_path / "chat.sqlite3")

    session = store.create_session("repo-1", "Investigate target")
    store.add_message(session.session_id, "user", "Where is target?")

    assert store.list_sessions("repo-1") == [session]
    assert [message.content for message in store.list_messages(session.session_id)] == [
        "Where is target?"
    ]
    assert store.list_sessions("repo-2") == []


def test_stream_persists_messages_and_final_matches_streamed_answer(tmp_path: Path) -> None:
    store = SQLiteChatStore(tmp_path / "chat.sqlite3")
    session = store.create_session("repo-1", "Target")
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = DeterministicEmbeddingProvider()
    chunks = chunk_file("repo-1", "snap-1", "app.py", "def target():\n    return 1\n")
    index_chunks("repo-1", "snap-1", chunks, embedding_provider, vector_store, keyword_index)

    events = list(
        stream_chat_answer(
            store,
            session.session_id,
            "repo-1",
            "snap-1",
            "target",
            embedding_provider,
            vector_store,
            keyword_index,
            RecordingChatProvider(),
        )
    )

    assert [event["event"] for event in events] == [
        "retrieval_started",
        "sources",
        "token",
        "token",
        "final",
    ]
    streamed_answer = "".join(str(event["data"]) for event in events if event["event"] == "token")
    final = events[-1]["data"]
    assert isinstance(final, dict)
    assert final["content"] == streamed_answer

    persisted = store.list_messages(session.session_id)
    assert [message.role for message in persisted] == ["user", "assistant"]
    assert persisted[-1].content == streamed_answer
    assert persisted[-1].citations[0].path == "app.py"


def test_full_history_is_stored_but_prompt_uses_bounded_recent_window(tmp_path: Path) -> None:
    store = SQLiteChatStore(tmp_path / "chat.sqlite3")
    session = store.create_session("repo-1", "Target")
    store.add_message(session.session_id, "user", "oldest")
    store.add_message(session.session_id, "assistant", "older")
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = DeterministicEmbeddingProvider()
    chunks = chunk_file("repo-1", "snap-1", "app.py", "def target():\n    return 1\n")
    index_chunks("repo-1", "snap-1", chunks, embedding_provider, vector_store, keyword_index)
    prompts: list[str] = []

    list(
        stream_chat_answer(
            store,
            session.session_id,
            "repo-1",
            "snap-1",
            "target",
            embedding_provider,
            vector_store,
            keyword_index,
            RecordingChatProvider(prompts=prompts),
            AnsweringOptions(max_recent_messages=1),
        )
    )

    assert "target" in prompts[0]
    assert "older" not in prompts[0]
    assert "oldest" not in prompts[0]
    assert [message.content for message in store.list_messages(session.session_id)] == [
        "oldest",
        "older",
        "target",
        "See `app.py:1-2`.",
    ]
