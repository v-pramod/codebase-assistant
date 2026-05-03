from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import AppError
from app.indexing.embeddings import EmbeddingProvider, OpenRouterEmbeddingProvider
from app.indexing.keyword_index import SQLiteKeywordIndex
from app.indexing.vector_store import ChromaChunkVectorStore, ChunkVectorStore
from app.llm.openrouter import OpenRouterChatProvider
from app.retrieval.answering import ChatProvider


@dataclass(frozen=True)
class StreamDependencies:
    embedding_provider: EmbeddingProvider
    vector_store: ChunkVectorStore
    keyword_index: SQLiteKeywordIndex
    chat_provider: ChatProvider


def build_stream_dependencies(settings: Settings) -> StreamDependencies:
    if not settings.openrouter_api_key:
        raise AppError(
            "provider_not_configured",
            "OpenRouter API key is required for live chat streaming.",
            status_code=503,
        )
    return StreamDependencies(
        embedding_provider=OpenRouterEmbeddingProvider(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_embedding_model,
        ),
        vector_store=ChromaChunkVectorStore(str(settings.chroma_dir)),
        keyword_index=SQLiteKeywordIndex(settings.sqlite_path),
        chat_provider=OpenRouterChatProvider(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_chat_model,
        ),
    )
