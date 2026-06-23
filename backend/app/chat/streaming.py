from collections.abc import Iterator

from app.chat.store import SQLiteChatStore
from app.indexing.embeddings import EmbeddingProvider
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import ChunkVectorStore
from app.retrieval.answering import (
    AnsweringOptions,
    ChatMessage,
    ChatProvider,
    Citation,
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
    commit_sha: str | None = None,
    repo_url: str | None = None,
) -> Iterator[dict[str, object]]:
    yield {"event": "retrieval_started", "data": {"session_id": session_id}}
    history = [
        ChatMessage(message.role, message.content)
        for message in store.list_messages(session_id)
        if message.role in {"user", "assistant"}
    ]
    history.append(ChatMessage("user", user_message))
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
    store.add_message(session_id, "user", user_message)
    citations = [
        _snapshot_citation(citation, repo_id, commit_sha, repo_url) for citation in result.citations
    ]
    source_payload = [_citation_payload(citation, False) for citation in citations]
    yield {"event": "sources", "data": source_payload}
    for token in _tokenize(result.answer):
        yield {"event": "token", "data": token}
    assistant = store.add_message(
        session_id,
        "assistant",
        result.answer,
        model=chat_provider.model,
        snapshot_id=snapshot_id,
        citations=citations,
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


def _snapshot_citation(
    citation: Citation, repo_id: str, commit_sha: str | None, repo_url: str | None
) -> Citation:
    local_ref = (
        f"/api/repositories/{repo_id}/files/content?path={citation.path}"
        f"#L{citation.start_line}-L{citation.end_line}"
    )
    permalink = None
    if repo_url is not None and commit_sha is not None:
        permalink = (
            f"{repo_url}/blob/{commit_sha}/{citation.path}"
            f"#L{citation.start_line}-L{citation.end_line}"
        )
    return Citation(
        citation.path,
        citation.start_line,
        citation.end_line,
        citation.snippet,
        commit_sha,
        local_ref,
        permalink,
    )


def _citation_payload(citation: Citation, stale: bool) -> dict[str, object]:
    return {
        "path": citation.path,
        "start_line": citation.start_line,
        "end_line": citation.end_line,
        "snippet": citation.snippet,
        "commit_sha": citation.commit_sha,
        "local_ref": citation.local_ref,
        "github_permalink": citation.github_permalink,
        "stale": stale,
    }
