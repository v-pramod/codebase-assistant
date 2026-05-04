# Golden Evaluation Report

Date: 2026-05-04

## Scope

This evaluation uses equivalent mixed-stack fixtures instead of live public repositories so the suite is deterministic and does not require network access, OpenRouter credentials, or executing cloned code.

Representative stacks:

- Python/FastAPI fixture: `python-fastapi`
- React/TypeScript fixture: `react-typescript`
- Java fixture: `java-service`

## Golden Questions

| ID | Coverage | Expected evidence |
| --- | --- | --- |
| `python-exact-symbol` | Exact symbol/path lookup | `app/main.py` |
| `react-conceptual-streaming` | Conceptual code question | `src/api.ts` |
| `java-refresh-behavior` | Refresh behavior concept | `src/main/java/demo/IngestionService.java` |
| `weak-evidence-refusal` | Weak-evidence refusal | No required path; refusal required |

## Recorded Output

The automated evaluation records these quality dimensions for every question:

- `answer_correct`
- `citation_present`
- `citation_relevant`
- `refusal_correct`
- `cited_paths`

Latest local run:

| ID | Answer correct | Citation present | Citation relevant | Refusal correct |
| --- | --- | --- | --- | --- |
| `python-exact-symbol` | Pass | Pass | Pass | Pass |
| `react-conceptual-streaming` | Pass | Pass | Pass | Pass |
| `java-refresh-behavior` | Pass | Pass | Pass | Pass |
| `weak-evidence-refusal` | Pass | Pass | Pass | Pass |

## Human Review

Demo-readiness pass:

- Repository submission, repository selection, ingestion status, chat, citations, source panel, file explorer, refresh, and stale-citation UI are represented by the current implemented API/UI flow.
- The deterministic golden suite confirms exact lookup, conceptual retrieval, refusal behavior, citation presence/relevance, and refresh-related evidence coverage.
- Follow-up for a later milestone: add a live public-repository smoke run once the ingestion worker is wired to clone/index repositories end-to-end with configured provider credentials.
