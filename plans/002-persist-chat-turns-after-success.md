# Plan 002: Persist chat turns only after answering succeeds

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: from the repo root,
> `git diff --stat 91432ab..HEAD -- backend/app/chat/streaming.py backend/tests/test_chat_streaming.py`
> If either file changed since this plan was written, compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, treat it as a
> STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `91432ab`, 2026-06-23

## Why this matters

When a chat message is streamed, the user's message is written to SQLite
*before* retrieval and the LLM call run. If the embedding call, retrieval, or
the chat provider then raises (a transient provider/network error is the common
case), the user turn is already committed but no assistant reply is produced.
The route catches the error and emits an SSE `error` event — but the orphaned
user message stays in history, and when the user re-sends, a **second** copy is
written. Over a few failed attempts the session fills with duplicate dangling
user turns. The fix: compute the answer first, and only persist the user and
assistant turns once answering has succeeded, so a failure leaves history
untouched and a retry is clean.

## Current state

`backend/app/chat/streaming.py` — `stream_chat_answer` (lines 16–71). The
ordering bug is at lines 30–47:

```python
    yield {"event": "retrieval_started", "data": {"session_id": session_id}}
    store.add_message(session_id, "user", user_message)          # <-- persisted too early
    history = [
        ChatMessage(message.role, message.content)
        for message in store.list_messages(session_id)
        if message.role in {"user", "assistant"}
    ]
    result = answer_question(                                    # <-- can raise
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
```

Key facts the fix must preserve:
- `answer_question` receives the current question both as `user_message` (the
  question) **and** as the last entry of `history`. The existing test
  `test_full_history_is_stored_but_prompt_uses_bounded_recent_window`
  (`backend/tests/test_chat_streaming.py:95`) depends on the current turn being
  present in `history` so the bounded recent-window math is unchanged. So the
  current turn must still appear in `history` — but **in memory only**, not
  persisted, until answering succeeds.
- After success, the persisted order must remain `user` then `assistant`
  (asserted by `test_stream_persists_messages_and_final_matches_streamed_answer`
  at line 82–83, and the content-order assertion at line 125–130).

Repo conventions: tests inject fake providers (frozen/`@dataclass` doubles with
an `answer`/`embed_texts` method). Model new tests on the existing ones in this
file.

## Commands you will need

| Purpose   | Command (run from `backend/`)                       | Expected on success |
|-----------|-----------------------------------------------------|---------------------|
| Tests     | `uv run pytest -q`                                   | all pass            |
| Tests (focused) | `uv run pytest tests/test_chat_streaming.py -q` | all pass          |
| Lint      | `uv run ruff check app/chat/streaming.py tests/test_chat_streaming.py` | exit 0 |
| Typecheck | `uv run mypy app`                                   | only the known `app/jobs/queue.py:29` error |

## Scope

**In scope** (the only files you should modify):
- `backend/app/chat/streaming.py`
- `backend/tests/test_chat_streaming.py`

**Out of scope** (do NOT touch):
- `app/retrieval/answering.py` — `answer_question`'s signature and behavior stay
  the same.
- `app/api/routes.py` — the route's error handling already emits the SSE error;
  do not change it.
- The SSE event sequence (`retrieval_started`, `sources`, `token`…, `final`)
  must not change.

## Git workflow

- Branch: `advisor/002-persist-chat-turns-after-success`
- Commit message: short imperative subject, e.g.
  `Persist chat turns only after answering succeeds`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Build history from existing messages, append the current turn in memory

In `backend/app/chat/streaming.py`, replace the block at lines 31–36 (the
`store.add_message(...)` call followed by the `history = [...]` comprehension)
so that the user message is **not** persisted yet, but the current turn is still
included in the in-memory `history` passed to `answer_question`:

```python
    history = [
        ChatMessage(message.role, message.content)
        for message in store.list_messages(session_id)
        if message.role in {"user", "assistant"}
    ]
    history.append(ChatMessage("user", user_message))
```

(Remove the early `store.add_message(session_id, "user", user_message)` line
entirely. `history` is now a plain list you append to — that is fine.)

### Step 2: Persist the user turn after answering succeeds, before the assistant turn

Immediately **after** the `result = answer_question(...)` call returns and
before the `citations = [...]` line, add:

```python
    store.add_message(session_id, "user", user_message)
```

The existing `store.add_message(session_id, "assistant", ...)` call (lines
55–62) stays where it is. Result: nothing is persisted if `answer_question`
raises; on success the user turn is written first, then the assistant turn —
preserving created-at ordering.

**Verify**: `uv run pytest tests/test_chat_streaming.py -q` → all pass.

### Step 3: Add a regression test for the failure path

In `backend/tests/test_chat_streaming.py`, add `import pytest` at the top and a
new test that proves a provider failure leaves no persisted messages. Model it
on `test_stream_persists_messages_and_final_matches_streamed_answer`:

```python
def test_provider_failure_does_not_persist_user_message(tmp_path: Path) -> None:
    store = SQLiteChatStore(tmp_path / "chat.sqlite3")
    session = store.create_session("repo-1", "Target")
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = DeterministicEmbeddingProvider()
    chunks = chunk_file("repo-1", "snap-1", "app.py", "def target():\n    return 1\n")
    index_chunks("repo-1", "snap-1", chunks, embedding_provider, vector_store, keyword_index)

    class FailingChatProvider:
        model = "test-chat"

        def answer(self, prompt: str, citations: list[Citation]) -> str:
            raise RuntimeError("provider boom")

    with pytest.raises(RuntimeError):
        list(
            stream_chat_answer(
                store,
                session.session_id,
                "repo-1",
                "snap-1",
                "target",
                embedding_provider,
                vector_store,
                keyword_index,
                FailingChatProvider(),
            )
        )

    assert store.list_messages(session.session_id) == []
```

**Verify**: `uv run pytest tests/test_chat_streaming.py -q` → all pass,
including the new test.

## Test plan

- New test: `test_provider_failure_does_not_persist_user_message` in
  `tests/test_chat_streaming.py` — covers the bug: a raising chat provider must
  leave the session with zero persisted messages.
- Existing tests in the same file are the regression guard for ordering and the
  bounded-window prompt behavior; they must still pass unchanged.
- Verification: `uv run pytest -q` → all pass (49 tests: 48 + 1 new).

## Done criteria

ALL must hold:

- [ ] `uv run pytest -q` exits 0, all pass, and the new failure-path test exists
- [ ] In `app/chat/streaming.py`, `store.add_message(session_id, "user", ...)`
      appears **after** the `answer_question(...)` call, not before it
- [ ] `uv run ruff check app/chat/streaming.py tests/test_chat_streaming.py` exits 0
- [ ] `uv run mypy app` reports only the pre-existing `app/jobs/queue.py:29` error
- [ ] `git status` shows only the two in-scope files changed
- [ ] `plans/README.md` status row for 002 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The "Current state" excerpt doesn't match the live `streaming.py` (drift).
- Removing the early `add_message` breaks
  `test_full_history_is_stored_but_prompt_uses_bounded_recent_window` — that
  means the in-memory `history.append(...)` step was missed; re-check Step 1 and
  only continue once that test passes.
- `answer_question` turns out to stream tokens incrementally (it currently
  returns a fully-formed answer) — if so, persisting after it returns may no
  longer be correct; stop and report.

## Maintenance notes

- For the reviewer: confirm the current turn is still in the `history` passed to
  `answer_question` (in memory), so the bounded recent-window behavior is
  unchanged — only the *persistence* moved.
- If `answer_question` is later made truly streaming (yields tokens as they
  arrive), revisit: you may want to persist the user turn up front again but add
  explicit rollback/dedupe on failure instead.
