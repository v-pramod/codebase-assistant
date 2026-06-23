from typing import Any

import httpx

from app.indexing.embeddings import OpenRouterEmbeddingProvider
from app.llm.openrouter import OpenRouterChatProvider


def test_chat_provider_sends_authorization_header() -> None:
    calls: list[dict[str, Any]] = []

    def post(*args: Any, **kwargs: Any) -> httpx.Response:
        calls.append({"args": args, "kwargs": kwargs})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "answer"}}]},
            request=httpx.Request("POST", str(args[0])),
        )

    provider = OpenRouterChatProvider("test-key", "test-model", post=post)

    assert provider.answer("question", []) == "answer"
    assert calls[0]["args"][0].endswith("/chat/completions")
    assert calls[0]["kwargs"]["headers"] == {"Authorization": "Bearer test-key"}


def test_embedding_provider_sends_authorization_header() -> None:
    calls: list[dict[str, Any]] = []

    def post(*args: Any, **kwargs: Any) -> httpx.Response:
        calls.append({"args": args, "kwargs": kwargs})
        return httpx.Response(
            200,
            json={"data": [{"embedding": [1.0, 2.0, 3.0]}]},
            request=httpx.Request("POST", str(args[0])),
        )

    provider = OpenRouterEmbeddingProvider("test-key", "test-model", post=post)

    assert provider.embed_texts(["hello"]) == [[1.0, 2.0, 3.0]]
    assert calls[0]["args"][0].endswith("/embeddings")
    assert calls[0]["kwargs"]["headers"] == {"Authorization": "Bearer test-key"}
