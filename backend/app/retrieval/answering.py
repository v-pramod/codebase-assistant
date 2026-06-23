from dataclasses import dataclass, replace
from typing import Protocol

from app.indexing.embeddings import EmbeddingProvider
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import ChunkVectorStore, VectorRecord


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class Citation:
    path: str
    start_line: int
    end_line: int
    snippet: str
    commit_sha: str | None = None
    local_ref: str | None = None
    github_permalink: str | None = None

    @property
    def label(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True)
class Evidence:
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    text: str
    score: float


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    citations: list[Citation]
    refused: bool
    prompt: str


@dataclass(frozen=True)
class AnsweringOptions:
    max_evidence: int = 4
    min_evidence_score: float = 0.20
    max_recent_messages: int = 4
    keyword_boost: float = 0.1


class ChatProvider(Protocol):
    @property
    def model(self) -> str: ...

    def answer(self, prompt: str, citations: list[Citation]) -> str: ...


def answer_question(
    repo_id: str,
    snapshot_id: str,
    question: str,
    recent_messages: list[ChatMessage],
    embedding_provider: EmbeddingProvider,
    vector_store: ChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
    chat_provider: ChatProvider,
    options: AnsweringOptions | None = None,
) -> AnswerResult:
    options = options or AnsweringOptions()
    evidence = retrieve_evidence(
        repo_id,
        snapshot_id,
        question,
        embedding_provider,
        vector_store,
        keyword_index,
        options.max_evidence,
        keyword_boost=options.keyword_boost,
    )
    citations = [
        Citation(item.path, item.start_line, item.end_line, item.text) for item in evidence
    ]
    prompt = build_prompt(question, recent_messages, evidence, options.max_recent_messages)
    if not evidence or evidence[0].score < options.min_evidence_score:
        closest = ", ".join(citation.label for citation in citations) or "no close references"
        return AnswerResult(
            answer=(
                f"I do not have enough indexed evidence to answer. Closest references: {closest}."
            ),
            citations=citations,
            refused=True,
            prompt=prompt,
        )
    answer = chat_provider.answer(prompt, citations)
    return AnswerResult(answer=answer, citations=citations, refused=False, prompt=prompt)


def retrieve_evidence(
    repo_id: str,
    snapshot_id: str,
    question: str,
    embedding_provider: EmbeddingProvider,
    vector_store: ChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
    limit: int,
    keyword_boost: float = 0.1,
) -> list[Evidence]:
    query_embedding = embedding_provider.embed_texts([question])[0]
    merged: dict[str, Evidence] = {}
    for record, score in vector_store.query_similar(repo_id, snapshot_id, query_embedding, limit):
        evidence = _evidence_from_vector(record, score)
        merged[evidence.chunk_id] = evidence
    for hit in keyword_index.search_active(repo_id, snapshot_id, question):
        existing = merged.get(hit.chunk_id)
        if existing is not None:
            merged[hit.chunk_id] = replace(
                existing, score=min(1.0, existing.score + keyword_boost)
            )
        else:
            merged[hit.chunk_id] = Evidence(
                hit.chunk_id,
                hit.path,
                hit.start_line,
                hit.end_line,
                hit.text,
                keyword_boost,
            )
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:limit]


def build_prompt(
    question: str,
    recent_messages: list[ChatMessage],
    evidence: list[Evidence],
    max_recent_messages: int,
) -> str:
    bounded_messages = recent_messages[-max_recent_messages:]
    history = "\n".join(f"{message.role}: {message.content}" for message in bounded_messages)
    sources = "\n\n".join(
        f"[{item.path}:{item.start_line}-{item.end_line}]\n{item.text}" for item in evidence
    )
    return (
        "Answer using only the provided code evidence. Include inline path:line citations.\n"
        f"Question: {question}\n"
        f"Recent chat:\n{history}\n"
        f"Evidence:\n{sources}"
    )


def _evidence_from_vector(record: VectorRecord, score: float) -> Evidence:
    start_line = record.metadata["start_line"]
    end_line = record.metadata["end_line"]
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        raise ValueError("Vector metadata must include integer line ranges.")
    return Evidence(
        chunk_id=record.chunk_id,
        path=str(record.metadata["path"]),
        start_line=start_line,
        end_line=end_line,
        text=record.text,
        score=score,
    )
