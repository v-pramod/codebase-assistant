# Plan 008: Self-hosted email + password auth (invite-only)

> **Executor instructions**: Follow this plan step by step. This is a **TDD**
> plan — for every phase, write the failing test(s) first, run the suite and
> confirm it is **RED for the expected reason**, then implement until **GREEN**,
> then run the lint/type gate. Do not write implementation code before its test
> exists. If anything in the "STOP conditions" section occurs, stop and report.
> When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: from the repo root,
> `git diff --stat bb9f596..HEAD -- backend/app/api/routes.py backend/app/main.py backend/app/core/config.py backend/app/chat/store.py backend/pyproject.toml`
> If any of these changed since this plan was written, compare the "Current state"
> excerpts below against the live code before proceeding; on a material mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P1 (blocks the deployment in `docs/deployment.md`)
- **Effort**: L
- **Risk**: MEDIUM (touches every route; introduces a security surface)
- **Depends on**: none
- **Category**: feature / security
- **Planned at**: commit `bb9f596`, 2026-06-24

## Why this matters

`docs/deployment.md` puts the API on a public AWS host. Today **every route is
unauthenticated** (`app/main.py` includes one router with no dependencies; the
only "gate" is that `frontend/vite.config.ts` proxies `/api` locally). Once
deployed, anyone who finds the URL can submit repos, run ingestion (which clones
arbitrary public repos and spends OpenRouter credits), and read chat history.

The requirement is **invite-only**: a fixed handful of users (1–10), created by
the operator, no public signup, no third-party SaaS, free. This plan adds
self-hosted email + password auth: a `users` table, a `POST /api/auth/login`
endpoint returning a signed **JWT**, a `get_current_user` dependency guarding
**all** routes except `/health` and `/auth/login`, and an admin CLI
(`codebase-assistant-adduser`) as the only way to create users. "Invite-only"
is enforced structurally: **there is no signup endpoint**.

> **Scope-change note**: `plans/README.md` previously recorded "Missing CORS /
> auth" as a *rejected* finding ("authentication is an explicit PRD non-goal …
> same-origin local single-user tool"). That rationale held while the tool was
> local-only. Deploying it publicly reverses the premise, so this plan
> intentionally supersedes that rejection. CORS is handled separately by the
> reverse-proxy approach in `docs/deployment.md` §6.

## Design decisions (read before starting)

1. **Token type: JWT bearer (HS256), not a cookie.** The frontend (Vercel/
   Netlify) and backend (AWS) are cross-origin; bearer tokens avoid
   `SameSite=None`/credentialed-CORS complications. The chat stream uses `fetch`
   (not `EventSource`), so it can send an `Authorization` header — verified in
   `frontend/src/api.ts`. Trade-off: a token in `localStorage` is XSS-exposed;
   acceptable for a small hobby tool. Revisit cookies if/when the same-origin
   proxy is adopted.
2. **Storage: a self-initializing `SQLiteUserStore`, mirroring
   `app/chat/store.py::SQLiteChatStore`** — raw `sqlite3`, creates its own table
   in `__init__`, lives at `data_dir / "auth.sqlite3"`. The chat store already
   sets this precedent (it is **not** Alembic-managed). This keeps the store
   trivially testable (point it at a temp path) and avoids touching the Alembic
   chain. *Alternative considered & rejected for now*: a `users` table via the
   SQLAlchemy `Base` + Alembic `0002` migration — more "correct" per the PRD data
   model, but inconsistent with the chat store and heavier to test. If a future
   plan migrates the chat store into Alembic, fold users in then.
3. **Password hashing: the `bcrypt` library directly**, not `passlib`. `passlib`
   is effectively unmaintained and emits version-detection warnings against
   `bcrypt>=4`; calling `bcrypt` directly is fewer moving parts and cleaner under
   strict mypy.
4. **Secret config**: `CODEBASE_ASSISTANT_JWT_SECRET`. Ships with an insecure
   dev default so tests/local run without setup; `docs/deployment.md` must
   instruct setting a strong value in prod (added in Phase 8).

## Tooling baseline (from plans/README.md — unchanged)

There are **two known pre-existing errors in `app/jobs/queue.py`** (`ruff` E501
~line 73–74; `mypy` `no-untyped-call` at line 29). A gate passes when it adds
**no new** errors beyond those two. All backend commands run from `backend/`.
Full suite at `bb9f596`: **51 passed**. Each phase below should leave the suite
green and growing.

If strict `mypy` reports missing stubs for `jwt` or `bcrypt`, add an
`ignore_missing_imports` override for those modules in `pyproject.toml`
(`[[tool.mypy.overrides]]`) — do **not** loosen `strict` globally.

---

## Phase 0 — Dependencies & config (foundation)

No new behavior; everything else builds on this.

1. In `backend/pyproject.toml` add to `dependencies`: `bcrypt>=4.2.0`,
   `pyjwt>=2.10.0`. Run `uv sync`.
2. In `app/core/config.py` `Settings`, add fields:
   ```python
   jwt_secret: str = "dev-insecure-change-me"
   jwt_algorithm: str = "HS256"
   access_token_ttl_minutes: int = 720  # 12h
   ```
   (Do **not** add `jwt_secret` to `diagnostics()` — never echo the secret.)
3. **Gate**: `uv run pytest -q` still **51 passed**; `uv run ruff check app/core/config.py`
   clean; `uv run mypy app` reports only the known `queue.py:29` error.

---

## Phase 1 — Password hashing (TDD)

**Goal**: `hash_password` / `verify_password` in `app/auth/passwords.py`.

- **RED** — create `tests/test_auth_passwords.py`:
  ```python
  from app.auth.passwords import hash_password, verify_password

  def test_hash_is_not_plaintext_and_verifies():
      h = hash_password("correct horse")
      assert h != "correct horse"
      assert verify_password("correct horse", h) is True

  def test_wrong_password_fails():
      assert verify_password("nope", hash_password("secret")) is False

  def test_hashes_are_salted_and_unique():
      assert hash_password("same") != hash_password("same")
  ```
  Run `uv run pytest tests/test_auth_passwords.py` → RED (module missing).
- **GREEN** — `app/auth/__init__.py` (empty) and `app/auth/passwords.py`:
  ```python
  import bcrypt

  def hash_password(plain: str) -> str:
      return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

  def verify_password(plain: str, hashed: str) -> bool:
      return bcrypt.checkpw(plain.encode(), hashed.encode())
  ```
- **Gate**: targeted test green; `ruff`/`mypy` clean on `app/auth/`.

---

## Phase 2 — Token create/decode (TDD)

**Goal**: `app/auth/tokens.py` — `create_access_token(subject, settings)` and
`decode_access_token(token, settings) -> str` (returns the subject/email; raises
a typed error on invalid/expired/tampered).

- **RED** — `tests/test_auth_tokens.py`: round-trip returns the subject; an
  expired token (construct with `access_token_ttl_minutes` overridden negative,
  or monkeypatch time) raises; a token signed with a different secret raises;
  garbage string raises. Use `get_settings()` with a test secret.
- **GREEN** — implement with `jwt.encode`/`jwt.decode` (HS256), `exp` claim from
  `access_token_ttl_minutes`, `sub` = email. Define an `InvalidTokenError`
  (subclass of `AppError("unauthorized", …, 401)` or a small local exception
  translated to `AppError` in Phase 5 — pick one and be consistent; recommended:
  raise a local `AuthError` and map to `AppError` in the dependency).
- **Gate**: targeted test green; `ruff`/`mypy` clean.

---

## Phase 3 — User store (TDD)

**Goal**: `app/auth/store.py::SQLiteUserStore`, mirroring `SQLiteChatStore`.

- **RED** — `tests/test_auth_store.py` (use `tmp_path`):
  - `create_user(email, password_hash)` then `get_by_email` returns it with
    `is_active=True`.
  - `get_by_email` for an unknown email returns `None`.
  - creating a duplicate email raises (UNIQUE) — or is rejected explicitly.
  - email is normalized (lowercased/trimmed) on write and lookup.
- **GREEN** — `UserRecord` dataclass (`email`, `password_hash`, `is_active`,
  `created_at`); `SQLiteUserStore(db_path)` that, in `__init__`, `mkdir`s the
  parent and `CREATE TABLE IF NOT EXISTS users(email TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_at
  TEXT NOT NULL)`. Methods: `create_user`, `get_by_email`, `list_users`,
  optionally `set_active`. Follow the exact `sqlite3.connect(...)` + context
  manager style of `SQLiteChatStore`.
- **Gate**: targeted test green; `ruff`/`mypy` clean.

---

## Phase 4 — Login endpoint (TDD)

**Goal**: `POST /api/auth/login` issues a JWT; failures are generic 401s.

- **RED** — `tests/test_auth_login.py`. Build a client whose user store points at
  a temp path with one seeded user (monkeypatch `routes._user_store` to a
  `SQLiteUserStore(tmp_path/"auth.sqlite3")` and create a user via
  `hash_password`). Assert:
  - valid credentials → 200, body has `access_token` + `token_type == "bearer"`,
    and the token decodes to the user's email.
  - wrong password → 401 with a **generic** message (does not reveal which field
    was wrong).
  - unknown email → 401, same generic message.
  - inactive user → 401.
- **GREEN** — add a `LoginSubmission(BaseModel)` (`email`, `password`) and a
  public route handler that looks up the user, `verify_password`s, checks
  `is_active`, and returns `create_access_token(user.email, settings)`. On any
  failure raise `AppError("unauthorized", "Invalid email or password.", 401)`.
  Add module-level `_user_store = SQLiteUserStore(_settings.data_dir /
  "auth.sqlite3")` near the existing `_chat_store` (routes.py:31).
- **Gate**: targeted test green; suite still green; `ruff`/`mypy` clean.

---

## Phase 5 — Guard all routes + fix existing tests (TDD) ⚠ highest-risk phase

**Goal**: `get_current_user` dependency; everything except `/health` and
`/auth/login` requires a valid bearer token. This phase **will turn existing API
tests RED** until they authenticate — that is expected and is the first signal.

### 5a. Restructure routers (so the gate is enforced centrally)

- In `app/api/routes.py`, move `/health` and `/auth/login` onto a new
  `public_router = APIRouter()`. Keep everything else (`/diagnostics`,
  `/repositories…`, `/chat-sessions…`, `/files…`, and the new `/auth/me`) on the
  existing `router`. (`/diagnostics` leaks config — keep it **protected**.)
- **Current state** (`app/main.py`, lines 1–14):
  ```python
  from app.api.routes import router
  ...
  app.include_router(router, prefix="/api")
  ```
  Change to include both, applying the dependency only to the protected one:
  ```python
  from app.api.routes import public_router, router
  from app.auth.dependencies import get_current_user
  ...
  app.include_router(public_router, prefix="/api")
  app.include_router(router, prefix="/api", dependencies=[Depends(get_current_user)])
  ```

### 5b. The dependency (TDD)

- **RED** — `tests/test_auth_guard.py`:
  - `GET /api/diagnostics` (and e.g. `GET /api/repositories`) **without** a token
    → 401.
  - the same **with** `Authorization: Bearer <valid token>` → 200.
  - `GET /api/health` and `POST /api/auth/login` work **without** a token.
  - a malformed/expired token → 401.
- **GREEN** — `app/auth/dependencies.py`:
  ```python
  def get_current_user(authorization: str | None = Header(default=None)) -> UserRecord:
      # parse "Bearer <token>", decode_access_token, load via _user_store,
      # check is_active; raise AppError("unauthorized", ..., 401) on any failure
  ```
  Resolve the user store the same way the route module does (import the module
  singleton, or expose a small accessor) so tests that monkeypatch
  `routes._user_store` are honored. Return the `UserRecord`.

### 5c. Re-green the existing suite (the real work of this phase)

Adding the dependency makes `test_api_surface.py` and `test_chat_streaming.py`
fail (401). Fix by adding shared fixtures in **`tests/conftest.py`**:
- a `client` fixture that creates the app, repoints `routes._user_store` at a
  `tmp_path` store, seeds one user, logs in, and returns a `TestClient` with the
  `Authorization` header pre-set on the session
  (`client.headers.update({"Authorization": f"Bearer {token}"})`).
- Update existing API tests to depend on this `client` fixture instead of
  constructing `TestClient(create_app())` inline.

Prefer the real-token fixture over `app.dependency_overrides[get_current_user]`
so the tests keep exercising the genuine auth path; reserve `dependency_overrides`
for any test that specifically needs to bypass auth.

- **Gate**: **full** suite green again and now **larger** than 51 (new auth
  tests). `ruff` clean on touched files; `mypy app` reports only `queue.py:29`.

### STOP conditions for Phase 5

- If any **protected** route is still reachable without a token after 5b, stop.
- If you cannot re-green an existing test without weakening the assertion it was
  written to make, stop and report — do not delete or `xfail` it to go green.

---

## Phase 6 — Admin CLI to create users (TDD)

**Goal**: `codebase-assistant-adduser <email>` (prompts for password) is the
only way to create a user. This is the "invite" mechanism.

- **RED** — `tests/test_auth_cli.py`: call the CLI entrypoint with a temp store
  (inject the store / settings, or monkeypatch `get_settings().data_dir` to
  `tmp_path`) and `email` + password; assert the user now exists in the store and
  that `verify_password` accepts the password. Add a duplicate-email case → exits
  non-zero / prints a clear error.
- **GREEN** — `app/auth/cli.py::main()` parses `argv` for the email, reads the
  password via `getpass` (or `--password` for non-interactive/tests), hashes it,
  and inserts via `SQLiteUserStore(get_settings().data_dir / "auth.sqlite3")`.
  Register in `pyproject.toml`:
  ```toml
  [project.scripts]
  codebase-assistant-worker = "app.worker:main"
  codebase-assistant-adduser = "app.auth.cli:main"
  ```
  Run `uv sync` so the script installs.
- **Gate**: targeted test green; full suite green; `ruff`/`mypy` clean.
  Manual smoke: `uv run codebase-assistant-adduser you@example.com` then
  `curl -s localhost:8000/api/auth/login -d '{"email":"you@example.com","password":"…"}'`
  returns a token (run with the API up).

---

## Phase 7 — Frontend login + token wiring

The frontend has **no JS test runner** (`frontend/package.json` has no
vitest/jest). Two sub-options — pick per the decision in "Open question" below.
This plan assumes **7-lite** (pure-logic unit tests + manual verification);
upgrade to full component tests only if the operator wants vitest added.

1. `src/auth.ts` — `getToken()`/`setToken()`/`clearToken()` over `localStorage`
   (key `auth_token`), plus `login(email, password)` calling `/api/auth/login`.
   *(If vitest is added: TDD these pure functions first.)*
2. `src/api.ts` — in `request()` (line ~97) attach
   `Authorization: Bearer ${getToken()}` when a token exists; on a `401`
   response, `clearToken()` and surface an `UnauthorizedError`. Do the same for
   the SSE `fetch` in the chat-stream path (it already uses `fetch`, so the
   header attaches normally). Keep `API_BASE` (`VITE_API_BASE_URL ?? "/api"`)
   unchanged.
3. `src/components/Login.tsx` — email + password form; on submit call `login`,
   store the token, re-render the app.
4. `src/App.tsx` — if `getToken()` is absent, render `<Login>`; otherwise the
   app. A global 401 handler (React Query `onError`, or a small wrapper) clears
   the token and returns to `<Login>`. Add a "Sign out" affordance that calls
   `clearToken()`.
5. **Verify**: `bun run build` (or `npm run build`) succeeds; `bun run lint`
   clean. Manual: with the backend up and a user created, confirm the app is
   unreachable until login, that calls carry the bearer header (Network tab), and
   that chat streaming still streams token-by-token (header passes through).

---

## Phase 8 — Docs & index

1. `docs/deployment.md`:
   - In §4.3 (secrets), add **`CODEBASE_ASSISTANT_JWT_SECRET`** to `backend/.env`
     with a strong value, e.g. generated via `openssl rand -hex 32`. Note that
     changing it invalidates all issued tokens.
   - After §4.5 (migrations), add a "Create the first user" step:
     `docker compose exec api codebase-assistant-adduser you@example.com`.
   - In §6, note the API is now authenticated (bearer token); the proxy approach
     still works unchanged.
2. `plans/README.md`: add the row
   `| 008 | Self-hosted email + password auth (invite-only) | P1 | L | — | DONE |`
   and a one-line reconcile note.

---

## Definition of done (run from `backend/`)

- `uv run pytest -q` → green, strictly more than 51 tests (auth phases added
  password, token, store, login, guard, and CLI tests).
- `uv run ruff check .` → no **new** errors beyond the known `queue.py` E501.
- `uv run mypy app` → only the known `queue.py:29` error (plus any documented
  `ignore_missing_imports` overrides added for `jwt`/`bcrypt`).
- Frontend: `bun run build` and `bun run lint` clean.
- Manual end-to-end: unauthenticated `GET /api/diagnostics` → 401;
  `/api/health` → 200; `adduser` → `login` → token → authenticated request → 200;
  there is **no** route that creates a user without the operator running the CLI.

## STOP conditions (whole plan)

- Any protected route reachable without a valid token.
- An existing test made to pass by weakening/removing its assertion.
- `jwt_secret` printed by `/api/diagnostics` or any log line.
- A signup/registration HTTP endpoint introduced (would break invite-only).
