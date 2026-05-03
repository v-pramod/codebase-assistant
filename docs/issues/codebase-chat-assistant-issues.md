# Codebase Chat Assistant Issues

Source PRD: `docs/prd/codebase-chat-assistant-prd.md`

Status: proposed, pending user approval before GitHub publication.

## Proposed Breakdown

1. **Bootstrap the local monorepo and backend service shell**
   - Type: AFK
   - Blocked by: None
   - User stories covered: 65, 66, 68, 69, 70, 71, 72, 73

2. **Ingest a public GitHub repo as a tracked background job**
   - Type: AFK
   - Blocked by: Bootstrap the local monorepo and backend service shell
   - User stories covered: 1, 2, 3, 4, 5, 6, 11, 13, 14, 67

3. **Filter repository files and report skipped content**
   - Type: AFK
   - Blocked by: Ingest a public GitHub repo as a tracked background job
   - User stories covered: 7, 8, 9, 10, 61

4. **Parse files into citation-ready code chunks**
   - Type: AFK
   - Blocked by: Filter repository files and report skipped content
   - User stories covered: 24, 25, 26, 27, 28, 29, 30, 31, 32

5. **Persist embeddings and keyword index for the active snapshot**
   - Type: AFK
   - Blocked by: Parse files into citation-ready code chunks
   - User stories covered: 33, 34, 35, 36, 37, 52, 54, 55, 56

6. **Answer repo-scoped questions with cited evidence**
   - Type: AFK
   - Blocked by: Persist embeddings and keyword index for the active snapshot
   - User stories covered: 12, 35, 36, 37, 38, 39, 40, 41, 42, 47, 48, 53

7. **Persist chat sessions and stream cited answers over SSE**
   - Type: AFK
   - Blocked by: Answer repo-scoped questions with cited evidence
   - User stories covered: 45, 46, 47, 48, 49, 50, 51

8. **Browse indexed repositories and cited source in the React UI**
   - Type: AFK
   - Blocked by: Persist chat sessions and stream cited answers over SSE
   - User stories covered: 41, 42, 43, 44, 57, 58, 59, 60, 61, 62, 63, 64

9. **Refresh repositories incrementally without breaking active answers**
   - Type: AFK
   - Blocked by: Persist embeddings and keyword index for the active snapshot
   - User stories covered: 15, 16, 17, 18, 19, 20, 21

10. **Preserve and display stale citation snapshots after refresh**
    - Type: AFK
    - Blocked by: Refresh repositories incrementally without breaking active answers; Browse indexed repositories and cited source in the React UI
    - User stories covered: 22, 23, 43, 44

11. **Evaluate the full RAG flow with golden public repositories**
    - Type: HITL
    - Blocked by: Browse indexed repositories and cited source in the React UI; Preserve and display stale citation snapshots after refresh
    - User stories covered: 74, 75

## Issue Bodies

### 1. Bootstrap the local monorepo and backend service shell

## What to build

Create the initial local development foundation for the Codebase Chat Assistant so the backend API, worker, Redis, persistence volumes, configuration, migrations, linting, formatting, and typing checks can be run consistently before feature work begins.

## Acceptance criteria

- [ ] The repo has the planned backend/frontend/docs monorepo structure with backend dependency management, backend service entrypoint, worker entrypoint, and backend-focused Docker Compose services.
- [ ] SQLite, Chroma, and clone storage paths are configured as persistent backend data locations, with secrets kept backend-only.
- [ ] Structured API errors, non-secret diagnostics, Alembic migrations, Ruff, strict mypy for app-owned backend modules, ESLint, and Prettier are configured.
- [ ] A documented backend smoke path verifies the API and worker infrastructure without inventing unsupported application behavior.

## Blocked by

None - can start immediately

### 2. Ingest a public GitHub repo as a tracked background job

## What to build

Let a local user submit a public GitHub HTTPS repository URL, enqueue ingestion through RQ, clone or fetch the repository under an internal repo ID, persist repository/job/snapshot state, and expose polling status to the UI/API.

## Acceptance criteria

- [ ] Strict URL validation accepts only public GitHub HTTPS repo URLs and rejects SSH, credentialed URLs, local paths, custom hosts, and malformed inputs.
- [ ] A submitted repository creates or reuses a repository record, enqueues an RQ job, and exposes status phases and errors through API polling.
- [ ] The worker clones/fetches with Git under a generated internal repo ID, resolves the default branch and commit SHA, and never executes repository code.
- [ ] Backend behavior tests cover URL validation, job state transitions, clone path safety, and visible failure states.

## Blocked by

- Bootstrap the local monorepo and backend service shell

### 3. Filter repository files and report skipped content

## What to build

Before parsing or embedding, classify repository files as indexable or skipped using gitignore rules, explicit deny patterns, binary detection, secret-like file detection, generated/vendored filtering, and configurable size limits, then report skipped-file stats to the user.

## Acceptance criteria

- [ ] Indexing excludes binary, generated, vendored, secret-like, minified, and oversized files according to configured rules.
- [ ] Global repository limits fail ingestion before embedding, while file-level limits skip individual files with persisted reasons.
- [ ] Ingestion status exposes skipped-file counts and reasons for API/UI display.
- [ ] Behavior tests cover representative index/skip decisions and global-versus-file-level limit behavior.

## Blocked by

- Ingest a public GitHub repo as a tracked background job

### 4. Parse files into citation-ready code chunks

## What to build

Convert indexable files into code-aware chunks that preserve file path, language, symbol metadata, line ranges, and deterministic chunk identity, using tree-sitter for first-class languages and fallback splitting for unsupported text files.

## Acceptance criteria

- [ ] Python, JavaScript, TypeScript, Java, and Markdown fixtures produce chunks with correct line ranges and symbol metadata where applicable.
- [ ] Unsupported text files use fallback splitting while preserving path and line ranges.
- [ ] Oversized symbols are split into overlapping line windows that retain parent symbol metadata.
- [ ] Chunk identity is deterministic and changes when relevant content changes.

## Blocked by

- Filter repository files and report skipped content

### 5. Persist embeddings and keyword index for the active snapshot

## What to build

Store chunk embeddings in persistent Chroma and keyword-searchable chunk text in SQLite FTS5, tied to repository and snapshot metadata so retrieval can query only the selected repo's active snapshot.

## Acceptance criteria

- [ ] OpenRouter embeddings are generated in configurable batches and stored in Chroma with repo, snapshot, file, symbol, line, hash, and active metadata.
- [ ] SQLite FTS5 stores searchable chunk text and metadata for keyword retrieval.
- [ ] Embedding failures fail fast with clear errors and never silently switch embedding models or mix dimensions in a collection.
- [ ] Tests or integration checks verify active-snapshot filtering, provider adapter failure behavior, and persisted vector/keyword records.

## Blocked by

- Parse files into citation-ready code chunks

### 6. Answer repo-scoped questions with cited evidence

## What to build

Allow a user to ask a question scoped to one repository and receive a concise answer grounded in hybrid retrieval results, with inline citations and a refusal path when evidence is weak.

## Acceptance criteria

- [ ] Retrieval merges Chroma vector results and SQLite FTS5 keyword results filtered by selected repo and active snapshot.
- [ ] Prompt construction uses fresh retrieved code evidence and a token-bounded recent chat window.
- [ ] Answers include inline path/line citations and source snippets for supported claims.
- [ ] Weak or missing evidence returns a refusal with closest references instead of a guessed answer.

## Blocked by

- Persist embeddings and keyword index for the active snapshot

### 7. Persist chat sessions and stream cited answers over SSE

## What to build

Persist repository-scoped chat sessions and messages while streaming assistant responses over SSE, including source events, token deltas, final saved message metadata, citations, and errors.

## Acceptance criteria

- [ ] Users can create/list chat sessions and retrieve persisted messages for a selected repository.
- [ ] Sending a message streams retrieval start, sources, token deltas, final saved message, citations, and errors over SSE.
- [ ] Full chat history is stored, but only a bounded recent window is sent to the LLM.
- [ ] The final persisted assistant message matches the streamed answer and citation set.

## Blocked by

- Answer repo-scoped questions with cited evidence

### 8. Browse indexed repositories and cited source in the React UI

## What to build

Create the main local React app route for repository submission, repository selection, ingestion status, chat sessions, streamed chat, source references, and read-only repository file browsing.

## Acceptance criteria

- [ ] The UI lets a user submit a repo URL, select an indexed repo, see ingestion status/warnings/errors, and open chat sessions.
- [ ] Chat displays streamed answers with inline citations and a source reference panel.
- [ ] The file explorer shows a read-only tree and text file viewer with syntax highlighting and preview limits.
- [ ] Binary, oversized, and skipped files are visible when appropriate but labeled and blocked from unsafe preview.

## Blocked by

- Persist chat sessions and stream cited answers over SSE

### 9. Refresh repositories incrementally without breaking active answers

## What to build

Add manual repository refresh that compares the previous indexed commit with the latest default-branch commit, re-indexes only changed/added files, handles deletions, stages pending snapshot data, and promotes only after full success.

## Acceptance criteria

- [ ] Refresh fetches exact previous and latest commit SHAs as needed and does not rely on arbitrary shallow clone depth.
- [ ] Changed and added files are re-filtered, re-parsed, re-embedded, and staged under a pending snapshot.
- [ ] Deleted files are removed from active retrieval only during successful promotion.
- [ ] Failed refreshes leave the previous active snapshot queryable and expose failure state.
- [ ] Dependency/config file changes produce a warning and expose a full rebuild option.

## Blocked by

- Persist embeddings and keyword index for the active snapshot

### 10. Preserve and display stale citation snapshots after refresh

## What to build

Keep old chat evidence trustworthy after refresh by storing citation snapshots with snippet text, file path, line range, commit SHA, local reference, and GitHub permalink, then showing stale state when old citations no longer match the latest active snapshot.

## Acceptance criteria

- [ ] Assistant messages persist citation snapshots before old vectors are garbage-collected.
- [ ] Old citations remain viewable even after refresh and vector cleanup.
- [ ] Citations include local explorer references and GitHub permalinks pinned to the indexed commit SHA.
- [ ] The UI marks stale citations when they do not match the latest active snapshot.

## Blocked by

- Refresh repositories incrementally without breaking active answers
- Browse indexed repositories and cited source in the React UI

### 11. Evaluate the full RAG flow with golden public repositories

## What to build

Create and run a golden evaluation set over small-to-medium public repositories from mixed stacks to verify answer correctness, citation relevance, refusal behavior, file explorer flow, and incremental refresh behavior.

## Acceptance criteria

- [ ] The evaluation set includes representative Python/FastAPI, React/TypeScript, and Java public repositories or equivalent mixed-stack fixtures.
- [ ] Golden questions cover exact symbol/path lookup, conceptual code questions, weak-evidence refusal, citations, and refresh behavior.
- [ ] Evaluation output records answer correctness, citation presence, citation relevance, and refusal correctness.
- [ ] A human review pass confirms the demo flow is ready and identifies any follow-up issues.

## Blocked by

- Browse indexed repositories and cited source in the React UI
- Preserve and display stale citation snapshots after refresh
