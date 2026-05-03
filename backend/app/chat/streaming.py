from collections.abc import Iterator

from app.chat.store import SQLiteChatStore
from app.indexing.embeddings import EmbeddingProvider
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import ChunkVectorStore
from app.retrieval.answering import (
    AnsweringOptions,
    ChatMessage,
    ChatProvider,
    answer_question,
)


def stream_chat_answer(
    store: SQLiteChatStore,
    session_id: str,
    repo_id: str,
    snapshot_id: str,
    user_message: str,
    embedding_provider: EmbeddingProvider,
    vector_store: ChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
    chat_provider: ChatProvider,
    options: AnsweringOptions | None = None,
) -> Iterator[dict[str, object]]:
    yield {"event": "retrieval_started", "data": {"session_id": session_id}}
    store.add_message(session_id, "user", user_message)
    history = [
        ChatMessage(message.role, message.content)
        for message in store.list_messages(session_id)
        if message.role in {"user", "assistant"}
    ]
    result = answer_question(
        repo_id,
        snapshot_id,
        user_message,
        history,
        embedding_provider,
        vector_store,
        keyword_index,
        chat_provider,
        options,
    )
    source_payload = [
        {
            "path": citation.path,
            "start_line": citation.start_line,
            "end_line": citation.end_line,
            "snippet": citation.snippet,
        }
        for citation in result.citations
    ]
    yield {"event": "sources", "data": source_payload}
    for token in _tokenize(result.answer):
        yield {"event": "token", "data": token}
    assistant = store.add_message(
        session_id,
        "assistant",
        result.answer,
        model=chat_provider.model,
        snapshot_id=snapshot_id,
        citations=result.citations,
    )
    yield {
        "event": "final",
        "data": {
            "message_id": assistant.message_id,
            "content": assistant.content,
            "citations": source_payload,
            "refused": result.refused,
        },
    }


def _tokenize(answer: str) -> list[str]:
    words = answer.split(" ")
    return [word if index == len(words) - 1 else f"{word} " for index, word in enumerate(words)]
