from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.retrieval.answering import Citation


class ChatProviderError(Exception):
    pass


@dataclass(frozen=True)
class OpenRouterChatProvider:
    api_key: str
    model: str
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 60.0
    post: Callable[..., httpx.Response] | None = None

    def answer(self, prompt: str, citations: list[Citation]) -> str:
        post = self.post or httpx.post
        try:
            response = post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Answer only from supplied evidence and cite paths/lines.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ChatProviderError("OpenRouter chat response did not include choices.")
            first = choices[0]
            if not isinstance(first, dict):
                raise ChatProviderError("OpenRouter chat response shape was invalid.")
            message = first.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise ChatProviderError("OpenRouter chat response did not include content.")
            return str(message["content"])
        except ChatProviderError:
            raise
        except Exception as exc:
            raise ChatProviderError("OpenRouter chat request failed.") from exc
