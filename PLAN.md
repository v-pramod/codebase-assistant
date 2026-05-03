# Codebase Chat Assistant Architecture Plan

## Product Scope

Build a production-style local RAG assistant for public GitHub repositories. A user runs the backend and frontend locally, submits a public GitHub repository URL, indexes the repository, asks codebase questions, and receives concise cited answers grounded in retrieved source code.

The MVP optimizes for a trustworthy flow: paste repo URL, watch ingestion progress, ask where or how something is implemented, receive an answer with inline file and line citations, inspect source snippets, browse repository files, and refresh the index when the upstream repository changes.

## MVP Goals

- Ingest public GitHub repositories through strict HTTPS GitHub URL validation.
- Clone repositories locally with Git CLI shallow clone behavior.
- Support incremental refresh by comparing the last indexed commit with the latest default-branch commit.
- Parse and chunk code by functions/classes where possible.
- Store embeddings in persistent Chroma and lifecycle metadata in SQLite.
- Support hybrid retrieval using vector similarity plus SQLite FTS5 keyword search.
- Generate concise factual answers through DeepSeek via OpenRouter.
- Refuse weakly evidenced answers instead of guessing.
- Return inline citations, source snippets, local explorer links, and GitHub permalinks pinned to commit SHA.
- Provide a React UI with repo/session navigation, chat, source panel, and read-only file explorer.
- Run ingestion through RQ plus Redis with status polling.
- Keep the system local, single-user, and public-repo only.

## Non-Goals

- Authentication.
- Private repositories.
- Multi-user hosting or tenancy.
- SSH Git URLs, local paths, custom Git hosts, or URLs containing credentials.
- Executing cloned repository code.
- Running tests, build scripts, static analyzers, or package managers inside cloned repositories.
- Code editing, PR creation, or agentic code modification.
- Cross-repository chat.
- GitHub webhooks or background polling.
- Full historical snapshot browsing.
- Token-by-token ingestion progress streaming.

## Repository Structure

Use a monorepo:

- `backend`: FastAPI app, RQ worker entrypoint, ingestion, parsing, retrieval, LLM, persistence, and evaluation code.
- `frontend`: Vite, React, TypeScript, Tailwind, and shadcn/ui.
- `docs`: architecture notes, PRDs, evaluation fixtures, and future ADRs.
- `docker-compose.yml`: backend API, RQ worker, Redis, and persistent backend data volumes.

The frontend dev server is not required in Docker Compose for MVP. Backend services and backend data persistence are the Compose priority.

## Tech Stack

- Backend: FastAPI.
- Backend packaging: `uv` with `pyproject.toml`.
- Background jobs: RQ plus Redis.
- Metadata DB: SQLite with SQLAlchemy.
- Migrations: Alembic.
- Vector DB: persistent Chroma.
- Keyword index: SQLite FTS5.
- Parsing: tree-sitter for first-class and supported languages; LangChain fallback splitters for unsupported text files.
- LLM provider: OpenRouter.
- Chat model: DeepSeek via OpenRouter, configured by environment variable.
- Embeddings: OpenRouter embeddings, configured by environment variable.
- Frontend: Vite, React, TypeScript.
- Frontend data fetching: handwritten typed fetch helpers/hooks with React Query.
- Frontend styling: Tailwind plus shadcn/ui.
- Lint/format: Ruff, ESLint, Prettier.
- Typing: strict mypy for app-owned backend modules, with typed adapters around weakly typed third-party libraries.

## Backend Module Boundaries

Use layered domain modules with small external interfaces and deep internal behavior:

- API module: REST and SSE endpoints, schema validation, and structured errors.
- Core/config module: environment-driven settings, limits, model names, data paths, and non-secret diagnostics.
- DB/models module: SQLAlchemy ORM models and Alembic schema lifecycle.
- Repository persistence module: repo records, snapshots, jobs, files, chunks, chat sessions, messages, and citation snapshots.
- Jobs module: RQ enqueueing, worker entrypoints, job status persistence, retries, and progress events.
- Git ingestion module: URL validation, clone storage, shallow fetch, commit diff calculation, refresh orchestration.
- File filtering module: gitignore handling, deny patterns, binary detection, size limits, and skipped-file stats.
- Parsing/chunking module: tree-sitter symbol extraction, line ranges, fallback splitting, large-symbol windowing, and chunk identity.
- Embedding/vector module: OpenRouter embedding calls, batching, Chroma writes, active snapshot filtering, and garbage collection.
- Retrieval module: hybrid vector plus FTS5 retrieval, score merging, light reranking, evidence thresholds, and citation assembly.
- LLM module: prompt construction, OpenRouter chat calls, SSE token streaming, refusal behavior, and final message persistence.
- Files module: read-only repository tree and text file viewer APIs with path normalization and preview limits.
- Evaluation module: golden Q&A fixtures, retrieval/debug outputs, and success checks.

## Deep Modules

The main deep modules should be testable without the full web app:

- URL validator: accepts only safe public GitHub HTTPS repo URLs and rejects unsafe alternatives.
- File filter: decides whether files are indexable, skipped, oversized, binary, generated, vendored, or secret-like.
- Parser/chunker: converts file contents into symbol-aware chunks with stable metadata and fallback behavior.
- Chunk identity generator: produces deterministic identities from repo, snapshot, file, symbol, line range, and content hash.
- Incremental planner: compares commits and decides added, changed, deleted, unchanged, warning, and rebuild-available outcomes.
- Retrieval orchestrator: merges vector and FTS results, applies repo/snapshot filters, enforces evidence thresholds, and returns citation-ready chunks.
- Citation snapshotter: stores durable citation evidence for old chat messages after old vectors are garbage-collected.

## Data Model

Persist non-vector state in SQLite:

- Repositories: URL, owner, name, default branch, local clone path, active snapshot, status, timestamps.
- Snapshots: repo, commit SHA, embedding model, parser version, active flag, status, created time.
- Ingestion jobs: repo, target commit, status, phase, progress counts, warnings, errors, timestamps.
- Files: repo, snapshot, path, hash, size, language, indexability, skipped reason, line count.
- Chunks: repo, snapshot, file path, symbol name, symbol type, start/end lines, content hash, Chroma ID, active state.
- Chat sessions: repo, title, timestamps.
- Messages: session, role, content, model, snapshot used, timestamps.
- Citation snapshots: message, file path, start/end lines, snippet text, commit SHA, local explorer reference, GitHub permalink.

Persist embeddings in Chroma with metadata sufficient for repo, snapshot, file, symbol, line range, content hash, active state, and citation retrieval.

## Ingestion Lifecycle

1. User submits a public GitHub HTTPS URL.
2. Backend validates URL and creates or finds a repository record.
3. Backend enqueues an RQ ingestion job.
4. Worker clones or fetches the repository under an internal generated repo ID in the configured data directory.
5. Worker resolves latest default-branch commit.
6. Initial ingestion indexes the current snapshot.
7. Refresh ingestion fetches the previous indexed commit and latest commit as needed, then computes a commit diff.
8. Changed and added files are filtered, parsed, chunked, embedded, and staged under a pending snapshot.
9. Deleted files are removed from the active snapshot during promotion.
10. Config/dependency file changes produce a warning and expose a full rebuild option.
11. The new snapshot is promoted only after the entire job succeeds.
12. Failed refreshes leave the previous successful active snapshot queryable.
13. Old vectors are garbage-collected after successful promotion, while stored citation snapshots preserve old chat evidence.

## Snapshot Staging

Use snapshot and active flags to isolate pending refresh data:

- Retrieval only uses the active snapshot for a repo.
- Pending chunks are tagged with snapshot/job metadata and are not retrieved.
- On success, the pending snapshot becomes active.
- On failure, pending vectors and metadata can be cleaned safely.
- Old chat citations retain stored snippets and commit SHAs even after vector cleanup.

## File Filtering And Limits

Use gitignore rules plus explicit deny patterns and hard limits:

- Exclude binary files, generated files, vendored dependencies, minified files, lockfiles where appropriate, secret-like files, and oversized files from indexing.
- Enforce configurable limits for maximum repo size, indexed file count, file size, and chunk count.
- Fail ingestion before embedding when global repo limits are exceeded.
- Skip individual files when file-level limits are exceeded.
- Record skipped-file counts and reasons in ingestion stats.
- Show skipped files in the file explorer if they exist in the clone, but label them as not indexed and disable unsafe previews.

## Parsing And Chunking

First-class parser test coverage includes Python, JavaScript, TypeScript, Java, and Markdown.

For first-class and tree-sitter-supported languages:

- Split by functions, classes, and equivalent language symbols.
- Preserve file path, symbol name, symbol type, start line, and end line.
- Split oversized symbols into line windows with overlap while preserving symbol metadata.

For unsupported text files:

- Use LangChain fallback splitters.
- Preserve file path and line ranges.
- Avoid indexing files that exceed configured limits.

## Retrieval And Answering

Use hybrid retrieval:

- Query Chroma vector similarity filtered by repo and active snapshot.
- Query SQLite FTS5 over chunk text and metadata.
- Merge vector and keyword results.
- Lightly rerank merged candidates.
- Apply evidence thresholds before prompting.
- Build prompts from the current question, a token-bounded recent chat window, and current retrieved code evidence.

Answer contract:

- Answers are concise and factual.
- Claims must be grounded in citations.
- Inline citations use path and line ranges.
- UI shows expandable source snippets.
- Weak or missing evidence produces a refusal with closest searched references instead of a guessed answer.
- Retrieval debug metadata is available only in development.

## Chat And History

- Persist chat sessions and messages per repository in SQLite.
- Store full chat history.
- Send only a token-bounded recent window to the LLM when context is large.
- Always retrieve fresh code evidence for each answer.
- Retrieval uses the latest active snapshot.
- Old citations keep their original commit SHA and display stale-snapshot state when they no longer match the latest active snapshot.

## API Surface

Expose REST-style endpoints plus SSE for chat streaming:

- Create/list/get repositories.
- Refresh a repository.
- Poll ingestion job status.
- List repository tree.
- Read repository file content with preview limits.
- Create chat sessions.
- List chat messages.
- Send chat messages and stream assistant responses over SSE.

SSE chat events should include retrieval started, sources, token deltas, final saved message, citations, and errors.

Use consistent JSON errors with code, message, and optional details. Explicitly map validation, ingestion, provider, and not-found failures.

## Frontend Scope

Build a single app route with:

- Repository submission form.
- Repository selector.
- Ingestion job status and warnings.
- Chat session list.
- Center chat interface.
- Right source reference panel.
- Read-only repository file explorer.
- Citation interactions that open local source snippets and provide GitHub permalinks.

The file explorer supports read-only tree browsing and text file viewing with syntax highlighting, size limits, binary/oversized preview blocking, and not-indexed labels.

## Operations And Security

- Docker Compose runs backend API, RQ worker, Redis, and persistent backend volumes for SQLite, Chroma, and clones.
- Backend and worker images include Git and parser/runtime dependencies.
- Backend reads OpenRouter and runtime configuration from environment variables.
- Secrets are never exposed to the frontend.
- Provider errors are logged without secret values.
- Repository paths are normalized and constrained to internal repo ID directories.
- The system never executes cloned code.
- Provider calls use bounded retries for transient network or rate-limit failures.
- Embeddings are batched with configurable batch size.
- OpenRouter embedding failures fail fast with clear setup errors and do not silently switch embedding models.

## Testing Strategy

Backend testing uses pytest with focused coverage:

- URL validation behavior.
- File filtering decisions and skipped-file stats.
- Parser/chunker behavior for Python, JavaScript, TypeScript, Java, and Markdown.
- Fallback chunking for unsupported text files.
- Oversized symbol windowing.
- Chunk identity generation.
- Incremental refresh planning.
- Ingestion job state transitions.
- Snapshot promotion and failed-refresh behavior.
- Retrieval threshold/refusal behavior.
- Citation snapshot persistence.

Frontend tests are not required for MVP. Frontend behavior is validated manually during golden Q&A evaluation and demo flow testing.

## Evaluation Strategy

Use a golden Q&A set across two to three small-to-medium public repositories with mixed stacks:

- A Python/FastAPI-style repository.
- A React/TypeScript repository.
- A Java repository.

Each evaluation checks:

- Answer correctness.
- Citation presence.
- Citation relevance.
- Refusal behavior for unsupported questions.
- Retrieval quality for exact symbol/path questions and conceptual questions.
- Incremental refresh behavior after repository changes.

## Build Order

1. Backend project scaffold, config, database, migrations, structured errors, and Docker Compose backend services.
2. Git URL validation, clone storage, RQ job state, and repository ingestion skeleton.
3. File filtering, parser/chunker, chunk identity, and tests.
4. Chroma embeddings, SQLite FTS5, snapshot staging, and incremental refresh.
5. Hybrid retrieval, citation assembly, answer/refusal contract, and chat persistence.
6. SSE chat streaming and REST API completion.
7. React UI with repo ingestion/status, chat, source panel, and file explorer.
8. Golden Q&A evaluation fixtures and demo hardening.
