# Plan 006: Retry transient OpenRouter failures a bounded number of times

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: from the repo root,
> `git diff --stat bb9f596..HEAD -- backend/app/llm/openrouter.py backend/app/indexing/embeddings.py backend/tests/test_openrouter_providers.py`
> If any of those files changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `bb9f596`, 2026-06-23

## Why this matters

PRD implementation decision (item 54) requires: *"provider calls to retry
transient failures only a bounded number of times, so temporary issues are
handled without hanging jobs."* The two OpenRouter adapters
(`OpenRouterChatProvider`, `OpenRouterEmbeddingProvider`) currently make a
**single** HTTP POST and wrap any failure into a provider error — there is no
retry. A single transient blip (HTTP 429 rate-limit, 502/503/504 from the
gateway, or a network timeout) therefore fails the whole operation: for
ingestion that means one flaky embedding batch aborts the entire repository
index; for chat it means a transient error surfaces straight to the user. This
plan adds a small shared bounded-retry helper and wires both providers through
it, retrying only **transient** errors (timeouts, transport errors, and 429/5xx
responses) with exponential backoff, up to a bounded number of attempts. PRD
item 55 ("embedding failures fail fast instead of silently switching models")
is preserved — we never switch models, and non-transient/shape errors still
raise immediately.

## Current state

Both providers are frozen dataclasses with an injectable `post` callable used by
tests. They call `post(...)`, then `response.raise_for_status()`, then parse.

`backend/app/llm/openrouter.py` (full file, 55 lines):

```python
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
```

`backend/app/indexing/embeddings.py` — `OpenRouterEmbeddingProvider.embed_texts`
follows the same shape (single `post`, then `response.raise_for_status()`, then
parse `payload["data"]`), raising `EmbeddingProviderError` on shape errors and
wrapping any other exception into `EmbeddingProviderError`.

`backend/tests/test_openrouter_providers.py` — two existing tests inject a fake
`post` that returns a 200 `httpx.Response` and assert the parsed return value and
the `Authorization` header. The fake `post` signature is
`def post(*args: Any, **kwargs: Any) -> httpx.Response`, and it attaches a
`request=httpx.Request("POST", str(args[0]))` to each response (required for
`raise_for_status()` to work).

Repo conventions:
- Strict mypy (`[tool.mypy] strict = true`) — everything must be fully typed.
- Ruff selects `E,F,I,UP,B,SIM` with line-length 100.
- Providers are frozen dataclasses with injectable seams for tests (`post`). Add
  the new injectable seams the same way.

## Commands you will need

| Purpose   | Command (run from `backend/`)                       | Expected on success |
|-----------|-----------------------------------------------------|---------------------|
| Tests     | `uv run pytest -q`                                  | all pass            |
| Tests (focused) | `uv run pytest tests/test_openrouter_providers.py -q` | all pass     |
| Lint      | `uv run ruff check app/llm tests/test_openrouter_providers.py` | exit 0 |
| Typecheck | `uv run mypy app`                                   | only the known `app/jobs/queue.py:29` error |

Baseline note: two known pre-existing tooling errors live in
`app/jobs/queue.py` (`ruff` E501 ~line 73; `mypy` `no-untyped-call` line 29).
Your gate passes when you add **no new** errors beyond those. Full suite is
**51 passing** today.

## Scope

**In scope** (the only files you should modify / create):
- `backend/app/llm/retry.py` (create — the shared retry helper)
- `backend/app/llm/openrouter.py`
- `backend/app/indexing/embeddings.py`
- `backend/tests/test_openrouter_providers.py`

**Out of scope** (do NOT touch):
- The response-parsing logic in either provider — keep the shape checks and the
  `ChatProviderError`/`EmbeddingProviderError` messages exactly as they are.
- `app/api/routes.py`, `app/api/runtime.py`, `app/jobs/*` — callers of these
  providers do not change; the retry is internal.
- Do NOT add any model-switching or fallback behavior (PRD forbids it).
- Do NOT change the default `timeout_seconds` values.

## Git workflow

- Branch: `advisor/006-retry-transient-provider-failures`
- Commit message: short imperative subject, e.g.
  `Retry transient OpenRouter failures with bounded backoff`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Create the shared retry helper

Create `backend/app/llm/retry.py`:

```python
import time
from collections.abc import Callable

import httpx

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        # httpx.TimeoutException subclasses TransportError, so timeouts are covered.
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return False


def request_with_retries(
    send: Callable[[], httpx.Response],
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Call `send`, raising for HTTP status, retrying only transient failures.

    Retries on connection/timeout errors and 429/5xx responses, up to
    `max_attempts` total attempts, sleeping `backoff_seconds * 2**(attempt-1)`
    between tries. Non-transient errors (4xx other than 429, bad shapes raised by
    the caller) propagate immediately.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    attempt = 0
    while True:
        try:
            response = send()
            response.raise_for_status()
            return response
        except Exception as exc:
            attempt += 1
            if attempt >= max_attempts or not _is_transient(exc):
                raise
            sleep(backoff_seconds * (2 ** (attempt - 1)))
```

**Verify**: `uv run ruff check app/llm/retry.py` → exit 0; `uv run mypy app` →
only the known `app/jobs/queue.py:29` error.

### Step 2: Add retry seams to the chat provider and route through the helper

In `backend/app/llm/openrouter.py`:
- Add `import time` and `from app.llm.retry import request_with_retries` to the
  imports.
- Add three fields to `OpenRouterChatProvider` (after `post`):
  ```python
      max_attempts: int = 3
      retry_backoff_seconds: float = 0.5
      sleep: Callable[[float], None] = time.sleep
  ```
- Replace the `response = post(...)` call **and** the following
  `response.raise_for_status()` line with a single call through the helper. The
  body inside `try:` becomes:
  ```python
          post = self.post or httpx.post
          response = request_with_retries(
              lambda: post(
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
              ),
              max_attempts=self.max_attempts,
              backoff_seconds=self.retry_backoff_seconds,
              sleep=self.sleep,
          )
          payload = response.json()
  ```
  Leave the rest of the method (the `choices`/`message` parsing and the
  `except ChatProviderError` / `except Exception` handlers) unchanged. The helper
  now performs `raise_for_status()`, so there must be **no** standalone
  `response.raise_for_status()` line left in this method.

**Verify**: `uv run ruff check app/llm/openrouter.py` → exit 0; `uv run mypy app`
→ only the known `app/jobs/queue.py:29` error.

### Step 3: Add the same retry seams to the embedding provider

In `backend/app/indexing/embeddings.py`, apply the identical change to
`OpenRouterEmbeddingProvider`:
- Add `import time` and `from app.llm.retry import request_with_retries`.
- Add the same three fields (`max_attempts`, `retry_backoff_seconds`, `sleep`)
  after `post`.
- Replace the `response = post(...)` call and the following
  `response.raise_for_status()` with the `request_with_retries(lambda: post(...))`
  form (same keyword arguments as Step 2), keeping the
  `f"{self.base_url}/embeddings"` URL, the `json={"model": self.model, "input": texts}`
  body, and `timeout=self.timeout_seconds`. Leave the `if not texts: return []`
  guard and all `payload["data"]` parsing / `EmbeddingProviderError` handling
  unchanged. No standalone `response.raise_for_status()` line should remain.

**Verify**: `uv run ruff check app/indexing/embeddings.py` → exit 0;
`uv run mypy app` → only the known `app/jobs/queue.py:29` error.

### Step 4: Add regression tests for retry behavior

In `backend/tests/test_openrouter_providers.py`, add `import pytest` at the top
and import the error type:
`from app.llm.openrouter import ChatProviderError, OpenRouterChatProvider`
(extend the existing import line). Then add three tests. Pass
`sleep=lambda _: None` so tests don't actually sleep:

```python
def test_chat_provider_retries_transient_failure_then_succeeds() -> None:
    attempts = {"n": 0}

    def post(*args: Any, **kwargs: Any) -> httpx.Response:
        attempts["n"] += 1
        request = httpx.Request("POST", str(args[0]))
        if attempts["n"] == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "answer"}}]},
            request=request,
        )

    provider = OpenRouterChatProvider("test-key", "test-model", post=post, sleep=lambda _: None)

    assert provider.answer("question", []) == "answer"
    assert attempts["n"] == 2


def test_chat_provider_raises_after_exhausting_retries() -> None:
    attempts = {"n": 0}

    def post(*args: Any, **kwargs: Any) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, request=httpx.Request("POST", str(args[0])))

    provider = OpenRouterChatProvider(
        "test-key", "test-model", post=post, sleep=lambda _: None, max_attempts=3
    )

    with pytest.raises(ChatProviderError):
        provider.answer("question", [])
    assert attempts["n"] == 3


def test_embedding_provider_retries_transient_failure_then_succeeds() -> None:
    attempts = {"n": 0}

    def post(*args: Any, **kwargs: Any) -> httpx.Response:
        attempts["n"] += 1
        request = httpx.Request("POST", str(args[0]))
        if attempts["n"] == 1:
            return httpx.Response(429, request=request)
        return httpx.Response(
            200, json={"data": [{"embedding": [1.0, 2.0, 3.0]}]}, request=request
        )

    provider = OpenRouterEmbeddingProvider(
        "test-key", "test-model", post=post, sleep=lambda _: None
    )

    assert provider.embed_texts(["hello"]) == [[1.0, 2.0, 3.0]]
    assert attempts["n"] == 2
```

**Verify**: `uv run pytest tests/test_openrouter_providers.py -q` → all pass
(the two original tests plus the three new ones).

## Test plan

- New tests in `tests/test_openrouter_providers.py`:
  - `test_chat_provider_retries_transient_failure_then_succeeds` — a 503 then
    200 succeeds after exactly 2 attempts.
  - `test_chat_provider_raises_after_exhausting_retries` — persistent 503 raises
    `ChatProviderError` after exactly `max_attempts` (3) attempts.
  - `test_embedding_provider_retries_transient_failure_then_succeeds` — a 429
    then 200 succeeds after 2 attempts.
- The two existing provider tests are the regression guard for the happy path
  and the `Authorization` header; they must still pass unchanged.
- Verification: `uv run pytest -q` → all pass (54 tests: 51 + 3 new).

## Done criteria

ALL must hold:

- [ ] `backend/app/llm/retry.py` exists and exports `request_with_retries`
- [ ] `grep -n "request_with_retries" app/llm/openrouter.py app/indexing/embeddings.py`
      shows the helper used in both providers
- [ ] `grep -n "raise_for_status" app/llm/openrouter.py app/indexing/embeddings.py`
      returns no matches (the helper now owns it)
- [ ] `uv run pytest -q` exits 0, all pass, including the 3 new retry tests
- [ ] `uv run ruff check app/llm app/indexing/embeddings.py tests/test_openrouter_providers.py` exits 0
- [ ] `uv run mypy app` reports only the pre-existing `app/jobs/queue.py:29` error
- [ ] `git status` shows only the four in-scope files changed (one created)
- [ ] `plans/README.md` status row for 006 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The "Current state" excerpts don't match the live provider files (drift) — in
  particular, if either provider already contains retry logic, this plan may be
  partly done; report what you find.
- `uv run mypy app` flags the `lambda` passed to `request_with_retries` as an
  untyped/incompatible callable — this means the `post` field's type
  (`Callable[..., httpx.Response] | None`) isn't narrowing as expected. Re-check
  that you assigned `post = self.post or httpx.post` to a local before the
  lambda; do not add `# type: ignore`. If it still fails, stop and report.
- A new ruff error appears that is NOT one of the two known `queue.py` baselines
  — fix it if it's about your code; if it's about an unrelated file, stop and
  report.

## Maintenance notes

- For the reviewer: the invariant is that **only transient** failures retry —
  confirm `_is_transient` returns `False` for non-429 4xx (e.g. a 401 bad key
  must fail fast, not retry 3×). Confirm embeddings still fail fast on shape
  errors (those are raised *after* the helper returns, so they are never
  retried) — PRD item 55.
- The retry parameters (`max_attempts`, `retry_backoff_seconds`) are dataclass
  fields with safe defaults; if the team wants them configurable via settings,
  thread them through `app/api/runtime.py` where the providers are constructed
  — that is a deliberate follow-up, not part of this plan.
- If a third OpenRouter call site is ever added, route it through
  `app/llm/retry.py` too rather than re-implementing the loop.
