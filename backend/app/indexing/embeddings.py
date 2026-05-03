from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx


class EmbeddingProviderError(Exception):
    pass


class EmbeddingProvider(Protocol):
    model: str

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class OpenRouterEmbeddingProvider:
    api_key: str
    model: str
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 30.0
    post: Callable[..., httpx.Response] | None = None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        post = self.post or httpx.post
        try:
            response = post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data")
            if not isinstance(data, list):
                raise EmbeddingProviderError("OpenRouter embedding response did not include data.")
            embeddings = [item.get("embedding") for item in data if isinstance(item, dict)]
            if len(embeddings) != len(texts) or not all(
                isinstance(item, list) for item in embeddings
            ):
                raise EmbeddingProviderError("OpenRouter embedding response shape was invalid.")
            return [
                [float(value) for value in embedding]
                for embedding in embeddings
                if isinstance(embedding, list)
            ]
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise EmbeddingProviderError("OpenRouter embedding request failed.") from exc
