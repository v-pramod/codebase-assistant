# Plan 003: Fix keyword-hit scoring so refusal & ranking work

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: from the repo root,
> `git diff --stat 91432ab..HEAD -- backend/app/retrieval/answering.py backend/tests/test_answering.py`
> If either file changed since this plan was written, compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, treat it as a
> STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (but see note: plan 004 depends on this one)
- **Category**: bug
- **Planned at**: commit `91432ab`, 2026-06-23

## Why this matters

The PRD's central trust property is: *refuse weakly-evidenced answers instead of
guessing*. That refusal is currently defeated. In `retrieve_evidence`, every
keyword (FTS) hit is assigned a hard-coded score of `1.0`, which then overwrites
the chunk's real semantic similarity. Because `_safe_fts_query` ORs together
every non-stopword token in the question, almost any question produces at least
one keyword hit, so the top evidence score is `1.0` and the refusal gate
(`evidence[0].score < min_evidence_score`, where `min_evidence_score = 0.20`)
**never fires**. The same flat `1.0` also forces all keyword hits above all
semantic hits, so ranking ignores relevance. The fix turns the keyword signal
into a small bounded *boost* on top of the real cosine score instead of a
replacement, restoring both the refusal contract and sane ranking.

## Current state

`backend/app/retrieval/answering.py`:

- Options (lines 49–54):
  ```python
  @dataclass(frozen=True)
  class AnsweringOptions:
      max_evidence: int = 4
      min_evidence_score: float = 0.20
      max_recent_messages: int = 4
  ```
- The refusal gate in `answer_question` (line 88):
  ```python
      if not evidence or evidence[0].score < options.min_evidence_score:
  ```
- The buggy merge in `retrieve_evidence` (lines 102–130):
  ```python
  def retrieve_evidence(
      repo_id: str,
      snapshot_id: str,
      question: str,
      embedding_provider: EmbeddingProvider,
      vector_store: ChunkVectorStore,
      keyword_index: SQLiteKeywordIndex,
      limit: int,
  ) -> list[Evidence]:
      query_embedding = embedding_provider.embed_texts([question])[0]
      merged: dict[str, Evidence] = {}
      for record in vector_store.active_records(repo_id, snapshot_id):
          evidence = _evidence_from_vector(
              record, _cosine_similarity(query_embedding, record.embedding)
          )
          merged[evidence.chunk_id] = evidence
      for hit in keyword_index.search_active(repo_id, snapshot_id, question):
          existing = merged.get(hit.chunk_id)
          keyword_score = 1.0
          if existing is None or keyword_score > existing.score:
              merged[hit.chunk_id] = Evidence(
                  hit.chunk_id,
                  hit.path,
                  hit.start_line,
                  hit.end_line,
                  hit.text,
                  keyword_score,
              )
      return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:limit]
  ```
- `Evidence` is a frozen dataclass (lines 31–39), so updates use
  `dataclasses.replace`.
- `retrieve_evidence` is called once, from `answer_question` (lines 75–83), with
  `options.max_evidence` as `limit`.

Why a small boost is the right model: every indexed chunk is written to **both**
the vector store and the keyword index by `index_chunks`
(`app/indexing/indexer.py:24-61`) with the **same** `chunk_id`. So a keyword hit
is almost always a chunk already present in `merged` with its real cosine score.
Adding a small boost (default `0.1`, below the `0.20` refusal threshold) means a
literal keyword match nudges ranking but cannot, by itself, manufacture
above-threshold "evidence" out of a semantically irrelevant chunk.

## Commands you will need

| Purpose   | Command (run from `backend/`)                    | Expected on success |
|-----------|--------------------------------------------------|---------------------|
| Tests     | `uv run pytest -q`                                | all pass            |
| Tests (focused) | `uv run pytest tests/test_answering.py -q`  | all pass            |
| Lint      | `uv run ruff check app/retrieval/answering.py tests/test_answering.py` | exit 0 |
| Typecheck | `uv run mypy app`                                | only the known `app/jobs/queue.py:29` error |

## Scope

**In scope** (the only files you should modify):
- `backend/app/retrieval/answering.py`
- `backend/tests/test_answering.py`

**Out of scope** (do NOT touch):
- `app/indexing/keyword_index.py` — the FTS query/tokenization is fine.
- `app/indexing/vector_store.py` — leave the stores alone (plan 004 handles them).
- `app/chat/streaming.py` — it calls `answer_question`, whose signature does not
  change here.
- Do NOT change `min_evidence_score` (keep `0.20`).

## Git workflow

- Branch: `advisor/003-fix-keyword-hit-scoring`
- Commit message: short imperative subject, e.g.
  `Make keyword hits boost score instead of replacing it`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add a `keyword_boost` option

In `backend/app/retrieval/answering.py`, add a `keyword_boost` field to
`AnsweringOptions`:

```python
@dataclass(frozen=True)
class AnsweringOptions:
    max_evidence: int = 4
    min_evidence_score: float = 0.20
    max_recent_messages: int = 4
    keyword_boost: float = 0.1
```

### Step 2: Import `replace`

Change the dataclasses import at the top of the file from
`from dataclasses import dataclass` to:

```python
from dataclasses import dataclass, replace
```

### Step 3: Pass the boost into `retrieve_evidence` and make keyword hits a boost

Update the `retrieve_evidence` call inside `answer_question` (around lines
75–83) to pass the boost as a new keyword-only argument:

```python
    evidence = retrieve_evidence(
        repo_id,
        snapshot_id,
        question,
        embedding_provider,
        vector_store,
        keyword_index,
        options.max_evidence,
        keyword_boost=options.keyword_boost,
    )
```

Then change `retrieve_evidence`'s signature to accept it and replace the keyword
merge loop:

```python
def retrieve_evidence(
    repo_id: str,
    snapshot_id: str,
    question: str,
    embedding_provider: EmbeddingProvider,
    vector_store: ChunkVectorStore,
    keyword_index: SQLiteKeywordIndex,
    limit: int,
    keyword_boost: float = 0.1,
) -> list[Evidence]:
    query_embedding = embedding_provider.embed_texts([question])[0]
    merged: dict[str, Evidence] = {}
    for record in vector_store.active_records(repo_id, snapshot_id):
        evidence = _evidence_from_vector(
            record, _cosine_similarity(query_embedding, record.embedding)
        )
        merged[evidence.chunk_id] = evidence
    for hit in keyword_index.search_active(repo_id, snapshot_id, question):
        existing = merged.get(hit.chunk_id)
        if existing is not None:
            merged[hit.chunk_id] = replace(
                existing, score=min(1.0, existing.score + keyword_boost)
            )
        else:
            merged[hit.chunk_id] = Evidence(
                hit.chunk_id,
                hit.path,
                hit.start_line,
                hit.end_line,
                hit.text,
                keyword_boost,
            )
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:limit]
```

**Verify**: `uv run pytest tests/test_answering.py -q` → all pass (the existing
refusal and citation tests still hold).

### Step 4: Add a regression test proving refusal survives a keyword-only match

In `backend/tests/test_answering.py`, add a test with an embedding provider that
makes the query and the (keyword-matching) document point in orthogonal
directions, so cosine similarity is ~0 even though the document literally
contains the query token. Add it after the existing tests:

```python
@dataclass
class OrthogonalEmbeddingProvider:
    model: str = "test-embedding"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # The query embeds one way; any document embeds orthogonally, so cosine
        # similarity is ~0 even when the document contains the query token.
        return [[1.0, 0.0] if text == "config" else [0.0, 1.0] for text in texts]


def test_keyword_match_without_semantic_support_still_refuses(tmp_path: Path) -> None:
    vector_store = InMemoryChunkVectorStore()
    keyword_index = SQLiteKeywordIndex(tmp_path / "index.sqlite3")
    embedding_provider = OrthogonalEmbeddingProvider()
    # Document literally contains "config" (so FTS matches) but embeds orthogonally.
    chunks = chunk_file("repo-1", "snap-1", "settings.py", "def load():\n    return config\n")
    index_chunks("repo-1", "snap-1", chunks, embedding_provider, vector_store, keyword_index)

    result = answer_question(
        "repo-1",
        "snap-1",
        "config",
        [],
        embedding_provider,
        vector_store,
        keyword_index,
        CitedChatProvider(),
    )

    assert result.refused is True
```

This test fails on the old `keyword_score = 1.0` code (it would not refuse) and
passes after the fix (cosine `0.0` + boost `0.1` = `0.1`, below the `0.20`
threshold).

**Verify**: `uv run pytest tests/test_answering.py -q` → all pass, including the
new test.

## Test plan

- New test: `test_keyword_match_without_semantic_support_still_refuses` — proves
  the refusal contract holds when FTS matches but semantics are weak (the exact
  bug).
- Existing tests are the regression guard:
  - `test_answers_use_active_snapshot_evidence_and_inline_citations` (strong
    match still answers),
  - `test_weak_evidence_refuses_without_guessing` (no keyword match → refuses),
  - `test_prompt_uses_fresh_evidence_and_bounded_recent_chat`.
  All must still pass.
- Verification: `uv run pytest -q` → all pass (49 tests: 48 + 1 new).

## Done criteria

ALL must hold:

- [ ] `grep -n "keyword_score = 1.0" backend/app/retrieval/answering.py` returns no matches
- [ ] `uv run pytest -q` exits 0, all pass, including the new refusal test
- [ ] `uv run ruff check app/retrieval/answering.py tests/test_answering.py` exits 0
- [ ] `uv run mypy app` reports only the pre-existing `app/jobs/queue.py:29` error
- [ ] `git status` shows only the two in-scope files changed
- [ ] `plans/README.md` status row for 003 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The "Current state" excerpt doesn't match the live `answering.py` (drift).
- The existing `test_weak_evidence_refuses_without_guessing` starts failing —
  that would mean the boost is being applied where there is no keyword hit;
  re-check Step 3.
- You discover `min_evidence_score` has been changed to a value at or below the
  `keyword_boost` (`0.1`) — then a keyword-only match could clear the threshold
  again; stop and report rather than guessing a new boost value.

## Maintenance notes

- For the reviewer: the invariant is `keyword_boost < min_evidence_score` so a
  literal keyword match alone cannot pass the refusal gate. Keep that
  relationship if either value is tuned later.
- **Plan 004 depends on this plan** and edits the same `retrieve_evidence`
  function to push vector search into the store. Land 003 first.
- Deferred: a proper learned/normalized reranker (the PRD's "light reranking")
  is a larger follow-up; this plan restores correctness, not a full reranker.
