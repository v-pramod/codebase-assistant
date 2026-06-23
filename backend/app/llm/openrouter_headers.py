OPENROUTER_CACHE_HEADERS = {"X-OpenRouter-Cache": "true"}


def openrouter_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        **OPENROUTER_CACHE_HEADERS,
    }
