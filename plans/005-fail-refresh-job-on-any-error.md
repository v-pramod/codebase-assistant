# Plan 005: Fail the refresh job on any error, not only AppError

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: from the repo root,
> `git diff --stat bb9f596..HEAD -- backend/app/api/routes.py backend/tests/test_api_surface.py`
> If either file changed since this plan was written, compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, treat it as a
> STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `bb9f596`, 2026-06-23

## Why this matters

The `refresh_repository` endpoint sets `job.status = "running"` and then does
real work (git commit lookup, staging embeddings, snapshot promotion). It wraps
that work in `try/except AppError` — but the staging path can raise
**non-`AppError`** exceptions: `EmbeddingProviderError` from
`embed_texts`, a `ValueError` from vector-metadata validation, or a
`FileNotFoundError` if the clone directory is missing (raised by `subprocess`
before git runs, so it bypasses the `CalledProcessError`→`AppError` wrapping in
`ingestion/refresh.py`). When one of those escapes, the job is left permanently
in `status="running"`: the UI shows perpetual progress, the user gets a raw 500,
and there is no recorded failure to recover from. PRD user story 19 requires that
"failed refreshes leave the previous successful index active" with the failure
surfaced — the current code surfaces nothing for these error types. The fix adds
a catch-all handler that mirrors the existing `AppError` branch, marking the job
`failed` and returning the standard refresh payload.

## Current state

`backend/app/api/routes.py` — the `refresh_repository` endpoint (lines 110–166):

```python
@router.post("/repositories/{repository_id}/refresh", status_code=202)
async def refresh_repository(repository_id: str) -> dict[str, object]:
    repository = _require_repository(repository_id)
    job = _registry.start_refresh(repository)
    try:
        if repository.active_commit is None:
            raise AppError(
                "repository_not_indexed",
                "Repository must have an indexed commit before refresh.",
                409,
            )
        job.status = "running"
        job.phase = "planning_refresh"
        latest_commit = latest_default_branch_commit(repository.local_path)
        plan = plan_incremental_refresh(
            repository.local_path, repository.active_commit, latest_commit
        )
        # ... staging + promotion ...
        job.status = "succeeded"
        job.phase = "refresh_promoted"
        return _refresh_payload(job, plan.full_rebuild_available)
    except AppError as exc:
        job.status = "failed"
        job.phase = "refresh_failed"
        job.error = exc.message
        return _refresh_payload(job, False)
```

Key facts:
- There is already a helper `_safe_error_message(exc: Exception) -> str`
  (`routes.py:467-470`) that returns `exc.message` for an `AppError` and
  `str(exc) or "Repository ingestion failed."` otherwise. The
  `submit_repository` endpoint already uses this exact pattern for its
  catch-all (`routes.py:73-76`). **Match that pattern.**
- `_refresh_payload(job, full_rebuild_available)` (`routes.py:353-364`) builds
  the response dict and reads `job.status`, `job.phase`, `job.error`, etc.
- `AppError` is an `Exception` subclass, so the `except AppError` clause MUST
  stay **before** the new `except Exception` clause, or the catch-all will
  swallow `AppError`s and lose their specific codes/messages.
- `TrackedRepository` (`app/jobs/ingestion.py:24-33`) is a plain mutable
  `@dataclass` with fields `local_path: Path`, `active_commit: str | None`,
  `active_snapshot_id: str | None`, and `job: IngestionJobState`. Tests mutate
  these fields directly (see `tests/test_api_surface.py:84`, `:120`, `:179`).

Repo conventions: tests use FastAPI's `TestClient(create_app())` and reach into
`routes._registry` to inspect/mutate the in-memory repository. See the existing
tests in `tests/test_api_surface.py` for the exact pattern.

## Commands you will need

| Purpose   | Command (run from `backend/`)                       | Expected on success |
|-----------|-----------------------------------------------------|---------------------|
| Tests     | `uv run pytest -q`                                  | all pass            |
| Tests (focused) | `uv run pytest tests/test_api_surface.py -q`  | all pass            |
| Lint      | `uv run ruff check app/api/routes.py tests/test_api_surface.py` | exit 0 |
| Typecheck | `uv run mypy app`                                   | only the known `app/jobs/queue.py:29` error |

Baseline note: the repo has two known pre-existing tooling errors in
`app/jobs/queue.py` (`ruff` E501 ~line 73; `mypy` `no-untyped-call` line 29).
Your lint/type gate passes when you introduce **no new** errors beyond those.
The full suite is **51 passing** today.

## Scope

**In scope** (the only files you should modify):
- `backend/app/api/routes.py`
- `backend/tests/test_api_surface.py`

**Out of scope** (do NOT touch):
- `app/ingestion/refresh.py` — the git error wrapping there is correct; do not
  change how `_git_text` maps `CalledProcessError` to `AppError`.
- `app/indexing/indexer.py` / `embeddings.py` — the staging/embedding code is
  fine; this plan only fixes how the endpoint *handles* their exceptions.
- The `submit_repository` endpoint and `_safe_error_message` helper — reuse the
  helper, don't modify it.
- Do NOT change the success-path behavior or the SSE/streaming endpoints.

## Git workflow

- Branch: `advisor/005-fail-refresh-job-on-any-error`
- Commit message: short imperative subject, e.g.
  `Mark refresh job failed on any error, not only AppError`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add a catch-all handler to `refresh_repository`

In `backend/app/api/routes.py`, in the `refresh_repository` function, add a new
`except Exception` clause **after** the existing `except AppError` clause:

```python
    except AppError as exc:
        job.status = "failed"
        job.phase = "refresh_failed"
        job.error = exc.message
        return _refresh_payload(job, False)
    except Exception as exc:
        job.status = "failed"
        job.phase = "refresh_failed"
        job.error = _safe_error_message(exc)
        return _refresh_payload(job, False)
```

Do not change anything inside the `try` block.

**Verify**: `uv run ruff check app/api/routes.py` → exit 0; `uv run mypy app` →
only the known `app/jobs/queue.py:29` error.

### Step 2: Add a regression test for the non-AppError failure path

In `backend/tests/test_api_surface.py`, add a test that drives the refresh
endpoint into a non-`AppError` failure and asserts the job is marked `failed`.
The cleanest trigger: point the repository at a clone path that does not exist,
so `latest_default_branch_commit(repository.local_path)` raises
`FileNotFoundError` (a non-`AppError`). Model the setup on the existing
`routes._registry.get_repository(...)` mutation pattern already used in this
file:

```python
def test_refresh_marks_job_failed_when_work_raises_non_app_error(tmp_path: Path) -> None:
    client = TestClient(create_app())
    submitted = client.post(
        "/api/repositories", json={"url": "https://github.com/encode/refresh-fail"}
    )
    repo_id = submitted.json()["repository_id"]

    repository = routes._registry.get_repository(repo_id)
    assert repository is not None
    repository.active_commit = "deadbeef"
    repository.active_snapshot_id = "snap-1"
    repository.local_path = tmp_path / "does-not-exist"

    response = client.post(f"/api/repositories/{repo_id}/refresh")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["phase"] == "refresh_failed"
    assert repository.job.status == "failed"
```

(`Path`, `routes`, `TestClient`, and `create_app` are already imported at the
top of this test file.)

**Verify**: `uv run pytest tests/test_api_surface.py -q` → all pass, including
the new test. Then confirm the test actually exercises the bug: temporarily
revert Step 1, run the focused test, and it should FAIL (the request raises a
500 / the job stays `running`); re-apply Step 1 and it passes. (This is a
sanity check — leave Step 1 applied.)

## Test plan

- New test: `test_refresh_marks_job_failed_when_work_raises_non_app_error` in
  `tests/test_api_surface.py` — proves a non-`AppError` during refresh leaves the
  job `failed` (not `running`) and returns the standard failed payload.
- Existing tests are the regression guard for the success path and the
  `AppError` path (`409 repository_not_indexed` etc.); they must still pass.
- Verification: `uv run pytest -q` → all pass (52 tests: 51 + 1 new).

## Done criteria

ALL must hold:

- [ ] `refresh_repository` in `app/api/routes.py` has an `except Exception`
      clause after the `except AppError` clause that sets `job.status="failed"`,
      `job.phase="refresh_failed"`, `job.error=_safe_error_message(exc)` and
      returns `_refresh_payload(job, False)`
- [ ] `uv run pytest -q` exits 0, all pass, including the new test
- [ ] `uv run ruff check app/api/routes.py tests/test_api_surface.py` exits 0
- [ ] `uv run mypy app` reports only the pre-existing `app/jobs/queue.py:29` error
- [ ] `git status` shows only the two in-scope files changed
- [ ] `plans/README.md` status row for 005 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The "Current state" excerpt of `refresh_repository` doesn't match the live
  code (drift) — in particular if the `except AppError` clause is already
  accompanied by an `except Exception`, this plan is already done; report that.
- `TrackedRepository` turns out to be a frozen dataclass and
  `repository.local_path = ...` raises — then use the alternative trigger
  (override `routes.set_stream_dependencies_for_tests(...)` with an embedding
  provider whose `embed_texts` raises) and report that you switched approaches.
- The new test passes even with Step 1 reverted — that means it isn't hitting
  the bug; stop and report rather than shipping a test that proves nothing.

## Maintenance notes

- For the reviewer: confirm the `except AppError` clause still precedes the new
  `except Exception` (ordering is load-bearing — `AppError` is an `Exception`).
  Confirm the failed payload is *returned* (status 202 with `status="failed"`),
  matching the existing `AppError` branch, rather than re-raised as a 500.
- If the refresh flow is later moved into a background RQ job (like initial
  ingestion), this handler logic should move with it — the worker must record
  the same `failed`/`refresh_failed` state on any exception.
- Deferred (out of scope): structured logging of the swallowed exception. The
  `submit_repository` catch-all also doesn't log; if logging is added, do both
  consistently.
