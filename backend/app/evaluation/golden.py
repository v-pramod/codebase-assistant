from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.chunking.chunker import chunk_file
from app.indexing.embeddings import EmbeddingProvider
from app.indexing.indexer import index_chunks
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import InMemoryChunkVectorStore
from app.retrieval.answering import AnsweringOptions, ChatProvider, Citation, answer_question


@dataclass(frozen=True)
class GoldenFile:
    path: str
    content: str


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    repo_id: str
    question: str
    expected_paths: tuple[str, ...]
    expected_terms: tuple[str, ...]
    should_refuse: bool = False


@dataclass(frozen=True)
class EvaluationResult:
    id: str
    answer_correct: bool
    citation_present: bool
    citation_relevant: bool
    refusal_correct: bool
    cited_paths: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationSummary:
    results: tuple[EvaluationResult, ...]

    @property
    def passed(self) -> bool:
        return all(
            result.answer_correct
            and result.citation_present
            and result.citation_relevant
            and result.refusal_correct
            for result in self.results
        )


class GoldenEmbeddingProvider:
    model = "golden/deterministic"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_features(text) for text in texts]


class GoldenChatProvider:
    model = "golden/local"

    def answer(self, prompt: str, citations: list[Citation]) -> str:
        labels = ", ".join(citation.label for citation in citations)
        return f"Grounded answer from {labels}. {prompt}"


PYTHON_FASTAPI_FIXTURE = (
    "python-fastapi",
    (
        GoldenFile(
            "app/main.py",
            (
                "from fastapi import FastAPI\n\n"
                "app = FastAPI()\n\n"
                "@app.get('/health')\n"
                "def health():\n"
                "    return {'status': 'ok'}\n"
            ),
        ),
        GoldenFile(
            "app/jobs.py",
            "def enqueue_ingestion(repo_url: str) -> str:\n    return f'queued:{repo_url}'\n",
        ),
    ),
)

REACT_TYPESCRIPT_FIXTURE = (
    "react-typescript",
    (
        GoldenFile(
            "src/App.tsx",
            (
                "export function RepositorySelector() {\n"
                "  return <section>Pick repository</section>;\n"
                "}\n"
            ),
        ),
        GoldenFile(
            "src/api.ts",
            (
                "export async function streamChat(sessionId: string) {\n"
                "  return fetch(`/chat/${sessionId}/stream`);\n"
                "}\n"
            ),
        ),
    ),
)

JAVA_FIXTURE = (
    "java-service",
    (
        GoldenFile(
            "src/main/java/demo/IngestionService.java",
            (
                "package demo;\n"
                "public class IngestionService {\n"
                "  public String refresh() { return \"incremental\"; }\n"
                "}\n"
            ),
        ),
        GoldenFile(
            "src/main/java/demo/HealthController.java",
            (
                "package demo;\n"
                "public class HealthController {\n"
                "  public String health() { return \"ok\"; }\n"
                "}\n"
            ),
        ),
    ),
)

QUESTIONS = (
    GoldenQuestion(
        "python-exact-symbol",
        "python-fastapi",
        "health implemented",
        ("app/main.py",),
        ("health", "status"),
    ),
    GoldenQuestion(
        "react-conceptual-streaming",
        "react-typescript",
        "chat streaming start",
        ("src/api.ts",),
        ("streamChat", "fetch"),
    ),
    GoldenQuestion(
        "java-refresh-behavior",
        "java-service",
        "Java service incremental refresh",
        ("src/main/java/demo/IngestionService.java",),
        ("refresh", "incremental"),
    ),
    GoldenQuestion(
        "weak-evidence-refusal",
        "python-fastapi",
        "OAuth provider configured",
        (),
        (),
        should_refuse=True,
    ),
)


def run_golden_evaluation() -> EvaluationSummary:
    provider = GoldenEmbeddingProvider()
    chat_provider = GoldenChatProvider()
    vector_store = InMemoryChunkVectorStore()
    with TemporaryDirectory() as directory:
        keyword_index = SQLiteKeywordIndex(Path(directory) / "keyword.sqlite3")
        for repo_id, files in (PYTHON_FASTAPI_FIXTURE, REACT_TYPESCRIPT_FIXTURE, JAVA_FIXTURE):
            chunks = [
                chunk
                for file in files
                for chunk in chunk_file(repo_id, "snap-1", file.path, file.content)
            ]
            index_chunks(repo_id, "snap-1", chunks, provider, vector_store, keyword_index)

        results = tuple(
            _evaluate_question(question, provider, chat_provider, vector_store, keyword_index)
            for question in QUESTIONS
        )
    return EvaluationSummary(results)


def _evaluate_question(
    question: GoldenQuestion,
    provider: EmbeddingProvider,
    chat_provider: ChatProvider,
    vector_store: InMemoryChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
) -> EvaluationResult:
    result = answer_question(
        question.repo_id,
        "snap-1",
        question.question,
        [],
        provider,
        vector_store,
        keyword_index,
        chat_provider,
        AnsweringOptions(min_evidence_score=0.25),
    )
    cited_paths = tuple(citation.path for citation in result.citations)
    citation_present = bool(result.citations) or question.should_refuse
    citation_relevant = all(path in cited_paths for path in question.expected_paths)
    answer_correct = question.should_refuse or all(
        term.lower() in result.prompt.lower() for term in question.expected_terms
    )
    refusal_correct = result.refused is question.should_refuse
    return EvaluationResult(
        question.id,
        answer_correct,
        citation_present,
        citation_relevant,
        refusal_correct,
        cited_paths,
    )


def _features(text: str) -> list[float]:
    lowered = text.lower()
    return [
        float(sum(lowered.count(token) for token in ("health", "fastapi", "enqueue"))),
        float(sum(lowered.count(token) for token in ("stream", "fetch", "repository"))),
        float(sum(lowered.count(token) for token in ("java", "refresh", "incremental"))),
        float(sum(lowered.count(token) for token in ("oauth", "provider", "login"))),
    ]
