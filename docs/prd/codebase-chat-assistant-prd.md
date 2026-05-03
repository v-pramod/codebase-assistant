# Codebase Chat Assistant PRD

## Problem Statement

Developers often need to understand unfamiliar repositories quickly, but normal search is too literal and general-purpose chat models are not grounded in the actual code. A developer wants to ask questions like where authentication is handled, how a feature flows through the codebase, or which file owns a behavior, and receive an answer that can be trusted because it cites exact source files and line ranges.

For this project, the user wants a production-style local RAG application for public GitHub repositories. The app should demonstrate real ingestion, code-aware chunking, embeddings, vector search, keyword search, cited answers, incremental refresh, and a usable React interface without expanding into hosted multi-user SaaS, private repository auth, or code execution.

## Solution

Build a local single-user Codebase Chat Assistant. The user submits a public GitHub repository URL, the backend validates and clones the repository, an RQ worker indexes the code into SQLite and Chroma, and the frontend shows ingestion progress. Once indexed, the user can chat with the repository, browse files, inspect cited source snippets, and refresh the repository index when upstream code changes.

The assistant uses code-aware parsing for first-class languages, LangChain fallback splitting for other text files, hybrid retrieval over Chroma and SQLite FTS5, and DeepSeek via OpenRouter for concise answers. Every answer must be grounded in current retrieved code evidence and include citations. If evidence is weak, the assistant should refuse to guess and show the closest references it found.

## User Stories

1. As a local developer, I want to submit a public GitHub repository URL, so that I can index a codebase without manually cloning it.
2. As a local developer, I want invalid repository URLs to be rejected clearly, so that I understand why ingestion cannot start.
3. As a local developer, I want the app to accept only public GitHub HTTPS URLs, so that the system avoids unsafe credential and host handling.
4. As a local developer, I want repository ingestion to run in the background, so that the UI does not hang during cloning, parsing, or embedding.
5. As a local developer, I want to see ingestion status and progress phases, so that I know whether the system is cloning, parsing, embedding, indexing, or failing.
6. As a local developer, I want ingestion failures to show clear errors, so that I can fix configuration or choose a smaller repository.
7. As a local developer, I want skipped-file statistics, so that I understand what was excluded from the index.
8. As a local developer, I want generated, binary, vendored, secret-like, and oversized files excluded from indexing, so that retrieval quality and local resource usage stay predictable.
9. As a local developer, I want global repository limits to fail safely before embedding starts, so that large repositories do not exhaust my machine or provider quota.
10. As a local developer, I want individual oversized files to be skipped instead of failing the whole repository, so that useful partial indexes can still be built.
11. As a local developer, I want multiple repositories indexed locally, so that I can switch between projects without deleting previous work.
12. As a local developer, I want each chat to be scoped to one selected repository, so that answers do not mix evidence from unrelated repositories.
13. As a local developer, I want repository data stored under internal generated IDs, so that repository names cannot cause path collisions or unsafe paths.
14. As a local developer, I want the system to remember the indexed commit SHA, so that citations and refreshes are tied to a precise source version.
15. As a local developer, I want to refresh an indexed repository, so that my local assistant can reflect upstream code changes.
16. As a local developer, I want refreshes to compare the last indexed commit with the latest default-branch commit, so that only changed files need to be reprocessed.
17. As a local developer, I want changed and added files re-indexed during refresh, so that updated code becomes searchable.
18. As a local developer, I want deleted files removed from active retrieval after refresh, so that answers do not cite removed code.
19. As a local developer, I want failed refreshes to leave the previous successful index active, so that the repository remains queryable.
20. As a local developer, I want dependency or config file changes to create a warning and full rebuild option, so that I understand when semantic relationships may have shifted.
21. As a local developer, I want old vectors garbage-collected after successful refresh, so that disk usage stays controlled.
22. As a local developer, I want old chat citations to remain viewable after vector cleanup, so that previous answers keep their evidence.
23. As a local developer, I want stale citations marked when a repository has moved forward, so that I know an old answer may not reflect the latest snapshot.
24. As a local developer, I want Python code split by functions and classes, so that retrieval returns meaningful units instead of arbitrary text windows.
25. As a local developer, I want JavaScript and TypeScript code split by functions, classes, and related symbols, so that frontend code questions retrieve precise evidence.
26. As a local developer, I want Java code split by classes and methods, so that backend Java repositories are searchable at useful boundaries.
27. As a local developer, I want Markdown indexed with useful line ranges, so that documentation can support architecture and usage answers.
28. As a local developer, I want unsupported text files chunked with a safe fallback splitter, so that the assistant can still use relevant non-first-class files.
29. As a local developer, I want very large functions or classes split into overlapping windows, so that large symbols remain searchable without oversized chunks.
30. As a local developer, I want every chunk to preserve file path and line numbers, so that answers can cite exact source locations.
31. As a local developer, I want every chunk to preserve symbol metadata when available, so that retrieval and debugging can identify the relevant function or class.
32. As a local developer, I want chunk identities to be deterministic and content-aware, so that refreshes can safely replace stale chunks.
33. As a local developer, I want embeddings stored persistently, so that I do not need to re-index repositories after restarting the app.
34. As a local developer, I want metadata stored separately from vectors, so that ingestion state, chat history, and citation snapshots are queryable and maintainable.
35. As a local developer, I want keyword search combined with vector search, so that exact symbol names and conceptual questions both work well.
36. As a local developer, I want retrieval filtered by the selected repository, so that answers only use relevant code.
37. As a local developer, I want retrieval filtered by the active snapshot, so that current answers use current indexed code.
38. As a local developer, I want weak retrieval evidence to produce a refusal, so that the assistant does not hallucinate implementation details.
39. As a local developer, I want closest references shown when evidence is weak, so that I can still inspect likely related code.
40. As a local developer, I want concise factual answers, so that I can quickly understand the codebase without reading verbose model output.
41. As a local developer, I want inline citations attached to claims, so that I can verify each important statement.
42. As a local developer, I want source snippets displayed alongside answers, so that I can inspect cited evidence immediately.
43. As a local developer, I want citations to open local file explorer views, so that I can navigate source inside the app.
44. As a local developer, I want citations to include GitHub permalinks pinned to commit SHA, so that I can open stable external references.
45. As a local developer, I want chat sessions persisted per repository, so that I can return to previous investigations.
46. As a local developer, I want full chat history stored, so that past questions and answers are not lost.
47. As a local developer, I want only a bounded recent chat window sent to the LLM when context grows, so that prompts remain reliable and affordable.
48. As a local developer, I want each follow-up question to retrieve fresh code evidence, so that answers stay grounded in current source.
49. As a local developer, I want streamed chat answers, so that responses feel responsive while generation is running.
50. As a local developer, I want streamed source events before or during answer generation, so that the source panel can populate early.
51. As a local developer, I want the final streamed message saved with citations, so that the persisted chat record matches what I saw.
52. As a local developer, I want provider errors surfaced clearly, so that I can fix model, embedding, or API key configuration.
53. As a local developer, I want OpenRouter secrets kept backend-only, so that API keys are not exposed to the frontend.
54. As a local developer, I want provider calls to retry transient failures only a bounded number of times, so that temporary issues are handled without hanging jobs.
55. As a local developer, I want embedding failures to fail fast instead of silently switching models, so that retrieval quality is not corrupted by mixed embedding dimensions.
56. As a local developer, I want embedding batches to be configurable, so that ingestion can balance speed, rate limits, and memory usage.
57. As a local developer, I want a read-only file tree, so that I can browse the cloned repository without leaving the app.
58. As a local developer, I want a read-only text file viewer, so that I can inspect files beyond cited snippets.
59. As a local developer, I want syntax highlighting in the file viewer, so that source code is easier to read.
60. As a local developer, I want binary and oversized files blocked from preview, so that the UI remains safe and responsive.
61. As a local developer, I want skipped files labeled as not indexed, so that I know they will not affect answers.
62. As a local developer, I want a repository selector, so that I can switch active repositories.
63. As a local developer, I want a chat session sidebar, so that I can organize investigations within a repository.
64. As a local developer, I want a source reference panel, so that code evidence remains visible while I continue chatting.
65. As a local developer, I want structured API errors, so that frontend states can be clear and consistent.
66. As a local developer, I want a backend diagnostics endpoint for non-secret settings, so that local setup issues are easier to debug.
67. As a local developer, I want structured backend logs and persisted job events, so that ingestion and retrieval issues are diagnosable.
68. As a local developer, I want Docker Compose to run backend services, worker, Redis, and persistent volumes, so that local infrastructure setup is reproducible.
69. As a local developer, I want the backend and worker containers to include Git and parser dependencies, so that ingestion behaves consistently in Docker.
70. As a project maintainer, I want backend modules with deep testable interfaces, so that core ingestion and retrieval behavior can evolve safely.
71. As a project maintainer, I want database migrations from the start, so that schema changes remain disciplined.
72. As a project maintainer, I want strict typing for app-owned backend modules, so that interface mistakes are caught early.
73. As a project maintainer, I want linting and formatting configured, so that the codebase stays consistent.
74. As a project maintainer, I want golden Q&A evaluations across mixed-stack repositories, so that RAG quality can be measured beyond unit tests.
75. As a project maintainer, I want evaluation questions to check refusal behavior, so that the assistant remains trustworthy when evidence is insufficient.

## Implementation Decisions

- The product is a local single-user app for public GitHub repositories.
- The MVP uses a backend/frontend monorepo.
- The backend uses FastAPI and `uv` with `pyproject.toml`.
- The frontend uses Vite, React, TypeScript, Tailwind, shadcn/ui, React Query, and a handwritten typed fetch layer.
- Docker Compose runs the backend API, RQ worker, Redis, and persistent volumes for SQLite, Chroma, and cloned repositories.
- The frontend dev server is not required in Docker Compose for MVP.
- RQ plus Redis is the required background job mechanism.
- SQLite with SQLAlchemy stores non-vector metadata.
- Alembic manages schema migrations.
- Persistent Chroma stores embeddings and vector metadata.
- SQLite FTS5 stores keyword-searchable chunk text and metadata.
- OpenRouter provides both chat generation and embeddings.
- DeepSeek via OpenRouter is the initial chat model.
- OpenRouter embedding failures fail fast; the system does not silently switch embedding models.
- LangChain is used lightly where useful, not as the central architecture for ingestion, storage, or retrieval orchestration.
- Strict mypy applies to app-owned backend modules, with adapters around weakly typed dependencies.
- API schemas are separate from SQLAlchemy ORM models.
- API errors use structured codes, messages, and optional details.
- Configuration is environment-driven with safe defaults and non-secret diagnostics.
- Only strict GitHub HTTPS repository URLs are accepted.
- The Git CLI performs shallow clone and fetch operations.
- Incremental refresh compares exact previous and latest commit SHAs.
- Repositories are stored under generated internal IDs in a configured data directory.
- The system never executes cloned repository code.
- File access uses path normalization and size limits.
- File filtering uses gitignore plus explicit deny patterns.
- Global repository limits fail ingestion before embedding.
- File-level limits skip individual files and record skipped-file stats.
- Tree-sitter handles first-class and supported languages.
- First-class parser tests cover Python, JavaScript, TypeScript, Java, and Markdown.
- LangChain fallback splitting handles unsupported text files.
- Oversized symbols are split into overlapping line windows while preserving symbol metadata.
- Chunk identity uses repository, snapshot, file, symbol, line range, and content hash metadata.
- Embeddings are generated in configurable batches.
- Refresh data is staged under pending snapshots and promoted only after success.
- Retrieval only uses the active snapshot for a repository.
- Old vectors are garbage-collected after successful refresh.
- Citation snapshots preserve old message evidence after vector cleanup.
- Hybrid retrieval merges Chroma vector results and SQLite FTS5 keyword results.
- Retrieval uses evidence thresholds and refuses weakly supported answers.
- Chat sessions and full messages are persisted per repository.
- The LLM receives a token-bounded recent chat window plus fresh retrieved evidence.
- Chat responses stream over SSE.
- SSE chat events include retrieval start, sources, token deltas, final saved message, citations, and errors.
- Ingestion progress uses polling against persisted RQ job state.
- The UI uses one main app route.
- The UI includes repository submission, repository selector, ingestion status, chat sessions, center chat, source panel, and read-only file explorer.
- The file explorer shows skipped files with labels but blocks unsafe previews.
- Citations link to both local file explorer views and GitHub permalinks pinned to commit SHA.
- Retrieval debug metadata is available only in development.

The major modules to build are:

- API boundary module for REST endpoints, SSE chat, request/response validation, and error mapping.
- Configuration module for environment settings, limits, providers, paths, and diagnostics.
- Persistence module for repositories, snapshots, jobs, files, chunks, chat sessions, messages, and citation snapshots.
- Job module for RQ enqueueing, worker execution, persisted progress, retries, and failure states.
- Git ingestion module for URL validation, clone/fetch, default branch resolution, commit diff planning, and refresh orchestration.
- File filtering module for indexability decisions and skipped-file reporting.
- Parser/chunker module for tree-sitter extraction, fallback splitting, large-symbol windowing, and line metadata.
- Embedding/vector module for OpenRouter embeddings, Chroma writes, staging, active filtering, and garbage collection.
- Retrieval module for hybrid search, merge/rerank, evidence thresholding, and source assembly.
- LLM/chat module for prompt construction, OpenRouter chat streaming, refusal behavior, and final message persistence.
- File explorer module for read-only tree and text file preview behavior.
- Evaluation module for golden Q&A fixtures and retrieval/answer quality checks.

Deep modules that should remain independently testable are:

- URL validator.
- File filter.
- Parser/chunker.
- Chunk identity generator.
- Incremental refresh planner.
- Retrieval orchestrator.
- Citation snapshotter.

## Testing Decisions

- Backend tests use pytest.
- Good tests should assert externally observable behavior and stable contracts, not internal implementation details.
- Parser tests should verify chunk boundaries, symbol metadata, and line ranges from representative source snippets.
- File filtering tests should verify index/skip decisions and skipped reasons.
- URL validation tests should verify accepted GitHub HTTPS URLs and rejected SSH, credentialed, local, custom-host, and malformed URLs.
- Chunk identity tests should verify deterministic identity behavior across stable input and changed content.
- Incremental refresh planner tests should verify added, changed, deleted, unchanged, config-warning, and rebuild-available outcomes.
- Ingestion job tests should verify persisted job phases, failed-job behavior, and successful promotion behavior.
- Snapshot tests should verify failed refreshes do not replace active snapshots.
- Retrieval tests should verify repository/snapshot filtering, hybrid merge behavior, weak-evidence refusal, and citation assembly.
- Citation tests should verify snippet, file path, line range, commit SHA, and permalink persistence.
- Provider adapter tests should mock OpenRouter behavior and verify bounded retries and fail-fast embedding errors.
- Frontend automated tests are not required for MVP.
- Frontend behavior should be manually validated through the demo flow and golden Q&A evaluation.
- There is no prior test suite in the current repository because the codebase currently contains only planning documentation.

Modules selected for backend test coverage:

- URL validator.
- File filter.
- Parser/chunker.
- Chunk identity generator.
- Incremental refresh planner.
- Ingestion job lifecycle.
- Snapshot promotion.
- Retrieval orchestrator.
- Citation snapshotter.
- Provider adapters.

## Out of Scope

- Authentication.
- Private repository support.
- Multi-user hosted deployment.
- SSH Git URLs.
- Custom Git hosts.
- Local path ingestion.
- Git URLs with embedded credentials.
- Code execution inside cloned repositories.
- Running repository tests, builds, package managers, or analyzers.
- Editing files.
- Creating commits or pull requests.
- Cross-repository chat.
- GitHub webhooks.
- Automatic background polling for repository changes.
- Full historical snapshot browsing.
- Keeping all old vectors forever.
- Frontend automated test coverage for MVP.
- Architecture summary mode as a first-priority feature.
- Explain-this-file button as a first-priority feature.

## Further Notes

- The repository currently has no application scaffold, dependency files, ADRs, domain glossary, issue tracker configuration, or existing tests.
- The PRD is therefore based on the planning conversation and the current empty repository state.
- Issue tracker publication was not performed because no issue tracker or triage label configuration exists in the repository.
- A future setup step can configure agent issue-tracker metadata and publish this PRD into GitHub Issues or local markdown issues with a `needs-triage` label.
- The recommended build order is backend-first: scaffold backend infrastructure, implement ingestion and parsing, add vector/keyword indexing, build retrieval/chat, then add the React UI and golden evaluations.
