# Plan 007: Index the chat store's hot-path filter columns

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: from the repo root,
> `git diff --stat bb9f596..HEAD -- backend/app/chat/store.py backend/tests/test_chat_streaming.py`
> If either file changed since this plan was written, compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, treat it as a
> STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: perf
- **Planned at**: commit `bb9f596`, 2026-06-23

## Why this matters

`SQLiteChatStore` is queried on every chat read: `list_messages(session_id)`
filters `chat_messages` by `session_id`, and `list_sessions(repo_id)` filters
`chat_sessions` by `repo_id`. Neither column is indexed — both tables are
created with only a `PRIMARY KEY` on the id column — so SQLite does a full table
scan per call. These run on multiple hot paths (the messages endpoint, the
session sidebar, and the streaming endpoint which lists messages to build
history). At single-user scale this is not catastrophic today, but it is an
unbounded O(n) cost that grows with chat history for zero benefit, and the fix is
two index statements with a clear verification. Adding the indexes makes both
lookups index-backed without changing any behavior.

## Current state

`backend/app/chat/store.py` — `_initialize` (lines 170–196) creates the two
tables with no secondary indexes:

```python
    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions(
                    session_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages(
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model TEXT,
                    snapshot_id TEXT,
                    citations_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES chat_messages(session_id)
                )
                """
            )
```

The queries that scan these columns:
- `list_messages` (lines 137–155): `... FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC`.
- `list_sessions` (around lines 49–62): `... FROM chat_sessions WHERE repo_id = ? ...`.

Facts:
- `_initialize` is called from the `SQLiteChatStore` constructor on every
  instantiation, and every statement uses `CREATE TABLE IF NOT EXISTS`. Using
  `CREATE INDEX IF NOT EXISTS` keeps it idempotent and means existing on-disk
  `chat.sqlite3` files gain the indexes the next time the store is constructed.
- The store stores its path on `self.db_path` and methods open a fresh
  `sqlite3.connect(self.db_path)` per call. Do not change that pattern.

Repo conventions: tests use `tmp_path` for a throwaway DB and construct
`SQLiteChatStore(tmp_path / "chat.sqlite3")`. See the existing usage in
`backend/tests/test_chat_streaming.py` (it already imports `SQLiteChatStore` and
`Path`).

## Commands you will need

| Purpose   | Command (run from `backend/`)                       | Expected on success |
|-----------|-----------------------------------------------------|---------------------|
| Tests     | `uv run pytest -q`                                  | all pass            |
| Tests (focused) | `uv run pytest tests/test_chat_streaming.py -q` | all pass          |
| Lint      | `uv run ruff check app/chat/store.py tests/test_chat_streaming.py` | exit 0 |
| Typecheck | `uv run mypy app`                                   | only the known `app/jobs/queue.py:29` error |

Baseline note: two known pre-existing tooling errors live in
`app/jobs/queue.py` (`ruff` E501 ~line 73; `mypy` `no-untyped-call` line 29).
Your gate passes when you add **no new** errors beyond those. Full suite is
**51 passing** today.

## Scope

**In scope** (the only files you should modify):
- `backend/app/chat/store.py`
- `backend/tests/test_chat_streaming.py`

**Out of scope** (do NOT touch):
- The table schemas themselves (columns, primary keys, the foreign key) — only
  ADD indexes; do not alter or drop existing structures.
- `app/indexing/keyword_index.py` and `app/indexing/vector_store.py` — different
  stores; not part of this plan.
- The per-call `sqlite3.connect` pattern — do not introduce pooling here.
- No Alembic migration is needed: this store is created/initialized directly in
  code via `CREATE ... IF NOT EXISTS`, not through the Alembic-managed schema.

## Git workflow

- Branch: `advisor/007-index-chat-hot-path-columns`
- Commit message: short imperative subject, e.g.
  `Index chat_messages.session_id and chat_sessions.repo_id`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Create the two indexes in `_initialize`

In `backend/app/chat/store.py`, inside `_initialize`, after the two
`CREATE TABLE IF NOT EXISTS` statements (still inside the same
`with sqlite3.connect(self.db_path) as connection:` block), add:

```python
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id "
                "ON chat_messages(session_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_sessions_repo_id "
                "ON chat_sessions(repo_id)"
            )
```

**Verify**: `uv run ruff check app/chat/store.py` → exit 0; `uv run mypy app` →
only the known `app/jobs/queue.py:29` error.

### Step 2: Add a test asserting the indexes exist

In `backend/tests/test_chat_streaming.py`, add `import sqlite3` at the top if it
is not already imported, then add this test (it does not depend on any other
fixture beyond `tmp_path`):

```python
def test_chat_store_indexes_hot_path_columns(tmp_path: Path) -> None:
    SQLiteChatStore(tmp_path / "chat.sqlite3")

    with sqlite3.connect(tmp_path / "chat.sqlite3") as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert "idx_chat_messages_session_id" in names
    assert "idx_chat_sessions_repo_id" in names
```

(`SQLiteChatStore` and `Path` are already imported in this test file. If
`sqlite3.connect` needs a string path on your platform, wrap with
`str(tmp_path / "chat.sqlite3")`.)

**Verify**: `uv run pytest tests/test_chat_streaming.py -q` → all pass, including
the new test. Confirm the test is meaningful: temporarily revert Step 1, run the
focused test — it should FAIL (indexes absent); re-apply Step 1 and it passes.

## Test plan

- New test: `test_chat_store_indexes_hot_path_columns` in
  `tests/test_chat_streaming.py` — asserts both named indexes exist in
  `sqlite_master` after the store initializes.
- Existing chat-streaming and store tests are the regression guard that read/write
  behavior is unchanged; they must still pass.
- Verification: `uv run pytest -q` → all pass (52 tests: 51 + 1 new).

## Done criteria

ALL must hold:

- [ ] `grep -n "CREATE INDEX IF NOT EXISTS" app/chat/store.py` returns 2 matches
- [ ] `uv run pytest -q` exits 0, all pass, including the new index test
- [ ] `uv run ruff check app/chat/store.py tests/test_chat_streaming.py` exits 0
- [ ] `uv run mypy app` reports only the pre-existing `app/jobs/queue.py:29` error
- [ ] `git status` shows only the two in-scope files changed
- [ ] `plans/README.md` status row for 007 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The "Current state" excerpt of `_initialize` doesn't match the live code
  (drift) — e.g. if indexes already exist, report that the plan is already done.
- The new test passes even with Step 1 reverted — that means it isn't actually
  checking the indexes; stop and report.
- You discover the chat schema is actually managed by an Alembic migration
  (look in `backend/alembic/versions/`) rather than by `_initialize` — if so,
  the index belongs in a migration instead; STOP and report so the approach can
  be redirected.

## Maintenance notes

- For the reviewer: confirm the indexes are added with `IF NOT EXISTS` (safe to
  re-run on existing DBs) and that no table schema was altered.
- If the chat store is ever migrated to SQLAlchemy ORM models (the PRD names a
  SQLAlchemy persistence layer; this store is currently hand-rolled sqlite3),
  declare these indexes on the ORM model instead and drop the manual
  `CREATE INDEX` calls.
- Deferred (out of scope): a composite index on
  `chat_messages(session_id, created_at)` could further help the
  `ORDER BY created_at` in `list_messages`; not warranted at current scale —
  revisit only if message volume per session grows large.
