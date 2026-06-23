# Plan 001: Remove the no-op `X-OpenRouter-Cache` header

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: from the repo root,
> `git diff --stat 91432ab..HEAD -- backend/app/llm/openrouter_headers.py backend/app/llm/openrouter.py backend/app/indexing/embeddings.py backend/tests/test_openrouter_providers.py`
> If any of those files changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `91432ab`, 2026-06-23

## Why this matters

The codebase recently added a helper that attaches `X-OpenRouter-Cache: true` to
every OpenRouter request, with the intent of enabling prompt caching to cut
cost. **OpenRouter has no such header.** Verified against OpenRouter's docs:
prompt caching is either automatic (most providers, including DeepSeek — the
configured default chat model) or controlled by a `cache_control` breakpoint
*inside the message body*, never by a request header. So the header does
nothing, and the accompanying test only asserts the dead header is present —
giving false confidence that caching is active. This plan removes the no-op so
the code honestly reflects what it does (caching for DeepSeek is automatic and
needs no client action), and rewrites the test to stop asserting a fictional
header.

## Current state

- `backend/app/llm/openrouter_headers.py` — the entire file is the no-op helper:
  ```python
  OPENROUTER_CACHE_HEADERS = {"X-OpenRouter-Cache": "true"}


  def openrouter_headers(api_key: str) -> dict[str, str]:
      return {
          "Authorization": f"Bearer {api_key}",
          **OPENROUTER_CACHE_HEADERS,
      }
  ```
- `backend/app/llm/openrouter.py:6` imports it and uses it at line 27:
  ```python
  from app.llm.openrouter_headers import openrouter_headers
  ...
              response = post(
                  f"{self.base_url}/chat/completions",
                  headers=openrouter_headers(self.api_key),
  ```
- `backend/app/indexing/embeddings.py:7` imports it and uses it at line 37:
  ```python
  from app.llm.openrouter_headers import openrouter_headers
  ...
              response = post(
                  f"{self.base_url}/embeddings",
                  headers=openrouter_headers(self.api_key),
  ```
- `backend/tests/test_openrouter_providers.py` — both tests assert the header
  dict equals `{"Authorization": "Bearer test-key", "X-OpenRouter-Cache": "true"}`.

These are the only four references to the header machinery
(`grep -rn "openrouter_headers\|X-OpenRouter-Cache" backend` confirms it).

Repo conventions: providers are frozen dataclasses with an injectable `post`
callable for tests; headers are built inline at the call site. Match that — pass
a plain dict literal directly to `headers=`.

## Commands you will need

| Purpose   | Command (run from `backend/`)                          | Expected on success |
|-----------|--------------------------------------------------------|---------------------|
| Tests     | `uv run pytest -q`                                      | all pass            |
| Tests (focused) | `uv run pytest tests/test_openrouter_providers.py -q` | all pass      |
| Lint      | `uv run ruff check app/llm/openrouter.py app/indexing/embeddings.py tests/test_openrouter_providers.py` | exit 0 |
| Typecheck | `uv run mypy app`                                       | only the known `app/jobs/queue.py:29` error |
| Grep gate | `grep -rn "openrouter_headers\|X-OpenRouter-Cache" backend` | no matches      |

## Scope

**In scope** (the only files you should modify):
- `backend/app/llm/openrouter.py`
- `backend/app/indexing/embeddings.py`
- `backend/tests/test_openrouter_providers.py`
- `backend/app/llm/openrouter_headers.py` (delete this file)

**Out of scope** (do NOT touch):
- Any other provider behavior (timeouts, response parsing, error handling).
- Do NOT add `cache_control` breakpoints — that is a real but separate feature;
  it is not needed for DeepSeek and is not this plan's job.
- `app/jobs/queue.py` (it has pre-existing lint/type errors — leave them).

## Git workflow

- Branch: `advisor/001-remove-noop-openrouter-cache-header`
- Commit message style matches the repo (short imperative subject, see
  `git log --oneline`, e.g. "Improve chat send and streaming UX"). Example:
  `Remove no-op OpenRouter cache header`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Inline the auth-only header in the chat provider

In `backend/app/llm/openrouter.py`:
- Delete the import line `from app.llm.openrouter_headers import openrouter_headers`.
- Replace `headers=openrouter_headers(self.api_key),` (around line 27) with:
  ```python
  headers={"Authorization": f"Bearer {self.api_key}"},
  ```

**Verify**: `uv run ruff check app/llm/openrouter.py` → exit 0.

### Step 2: Inline the auth-only header in the embedding provider

In `backend/app/indexing/embeddings.py`:
- Delete the import line `from app.llm.openrouter_headers import openrouter_headers`.
- Replace `headers=openrouter_headers(self.api_key),` (around line 37) with:
  ```python
  headers={"Authorization": f"Bearer {self.api_key}"},
  ```

**Verify**: `uv run ruff check app/indexing/embeddings.py` → exit 0.

### Step 3: Delete the dead helper file

Delete `backend/app/llm/openrouter_headers.py`.

**Verify**: `grep -rn "openrouter_headers\|X-OpenRouter-Cache" backend` → no
matches (exit 1 from grep, i.e. nothing printed).

### Step 4: Rewrite the test assertions

In `backend/tests/test_openrouter_providers.py`:
- Rename `test_chat_provider_sends_openrouter_cache_header` →
  `test_chat_provider_sends_authorization_header`.
- Rename `test_embedding_provider_sends_openrouter_cache_header` →
  `test_embedding_provider_sends_authorization_header`.
- In both, change the header assertion to:
  ```python
  assert calls[0]["kwargs"]["headers"] == {"Authorization": "Bearer test-key"}
  ```
- Leave the rest of each test (endpoint suffix assertion, return-value assertion)
  unchanged.

**Verify**: `uv run pytest tests/test_openrouter_providers.py -q` → all pass.

## Test plan

- No new test files. The two existing tests in
  `tests/test_openrouter_providers.py` are updated to assert the correct
  (auth-only) header. They still cover: correct endpoint suffix
  (`/chat/completions`, `/embeddings`), correct `Authorization` header, and the
  provider returning the parsed value.
- Verification: `uv run pytest -q` → all pass (still 48 tests; two renamed).

## Done criteria

ALL must hold:

- [ ] `grep -rn "openrouter_headers\|X-OpenRouter-Cache" backend` returns no matches
- [ ] `backend/app/llm/openrouter_headers.py` no longer exists
- [ ] `uv run pytest -q` exits 0, all pass
- [ ] `uv run ruff check app/llm/openrouter.py app/indexing/embeddings.py tests/test_openrouter_providers.py` exits 0
- [ ] `uv run mypy app` reports only the pre-existing `app/jobs/queue.py:29` error
- [ ] `git status` shows only the four in-scope files changed (one deleted)
- [ ] `plans/README.md` status row for 001 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The code at the "Current state" locations doesn't match the excerpts (drift).
- `grep` finds references to `openrouter_headers` in files **not** listed in
  scope — there are more callers than expected; report them.
- You find an existing `cache_control` usage or a deliberate OpenRouter caching
  design doc/ADR — the team may want caching done properly; stop and ask.

## Maintenance notes

- For the reviewer: confirm no caching behavior is lost — there was none; DeepSeek
  prompt caching on OpenRouter is automatic and requires no client header.
- Deferred (out of scope): if the team wants OpenRouter dashboard attribution,
  the *optional* `HTTP-Referer` and `X-Title` headers are the supported
  mechanism — not caching. If they want explicit caching control for an
  Anthropic/Gemini model later, that uses `cache_control` breakpoints in the
  message `content`, a separate change to the request body.
