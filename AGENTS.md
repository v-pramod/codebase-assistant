# AGENTS.md

## Current State

- This repo currently contains planning docs only; no backend, frontend, manifests, CI, or executable commands exist yet.
- Treat `PLAN.md` as the architecture source of truth and `docs/prd/codebase-chat-assistant-prd.md` as the product requirements source.
- Do not invent setup/test commands until the relevant manifests or scripts exist.

## Product Constraints

- MVP is a local, single-user codebase RAG assistant for public GitHub repositories only.
- Explicit non-goals: auth, private repos, hosted multi-user tenancy, code execution, code editing/PR creation, cross-repo chat, webhooks, local path ingestion, SSH/custom Git hosts, and full historical snapshot browsing.
- Never execute cloned repository code; ingestion may run Git and read files as text only.

## Planned Architecture

- Monorepo shape: `backend` FastAPI app, `frontend` Vite React TS app, `docs`, and Docker Compose for backend API, RQ worker, Redis, SQLite/Chroma/clones volumes.
- Frontend dev server is intentionally outside Docker Compose for MVP; Compose should cover backend services and persistent backend data.
- Backend stack: `uv` + `pyproject.toml`, FastAPI, RQ/Redis, SQLite/SQLAlchemy, Alembic, Chroma, SQLite FTS5, OpenRouter, light LangChain usage.
- Frontend stack: Vite, React, TypeScript, Tailwind, shadcn/ui, React Query, handwritten typed fetch helpers/hooks.
- Use strict mypy for app-owned backend modules; isolate weakly typed third-party libraries behind typed adapters.

## Critical Implementation Rules

- Accept only strict HTTPS GitHub repo URLs matching public repo ingestion; reject SSH, credentials, local paths, and custom hosts.
- Store clones under generated internal repo IDs, not user-provided path names.
- Incremental refresh compares exact previous indexed commit and latest default-branch commit; do not rely on arbitrary shallow clone depth.
- Stage refresh data under pending snapshots and promote only after full success; failed refreshes must leave the previous active snapshot queryable.
- Retrieval must use only the selected repo's active snapshot.
- Preserve citation snapshots with snippet text, file path, line range, commit SHA, and GitHub permalink before old vectors are garbage-collected.
- OpenRouter embedding failures should fail fast; do not silently switch embedding models or mix embedding dimensions in a collection.

## Testing Expectations

- Backend tests are planned with pytest; prioritize behavior tests for URL validation, file filtering, parser/chunker, chunk identity, incremental refresh planning, snapshot promotion, retrieval/refusal, citation snapshots, and provider adapters.
- Frontend automated tests are explicitly not required for MVP; validate frontend behavior manually through the demo flow and golden Q&A evaluation.
