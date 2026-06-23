# Plan 004: Push vector search into the store (ANN, no full scan)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: from the repo root,
> `git diff --stat 91432ab..HEAD -- backend/app/retrieval/answering.py backend/app/indexing/vector_store.py backend/tests/test_answering.py`
> This plan **depends on plan 003**, which already edits `answering.py`. Before
> starting, confirm plan 003 has landed: `AnsweringOptions` in
> `app/retrieval/answering.py` must contain a `keyword_boost` field. If it does
> not, STOP — execute plan 003 first.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/003-fix-keyword-hit-scoring.md
- **Category**: perf
- **Planned at**: commit `91432ab`, 2026-06-23

## Why this matters

Vector retrieval currently ignores the vector database's whole purpose. For
every question, `retrieve_evidence` calls `vector_store.active_records(...)`,
which on the Chroma-backed store issues a `collection.get(...)` that pulls
**every** active embedding for the repo into memory, then computes cosine
similarity in Python one record at a time. At the configured ceiling
(`max_indexed_files = 20000`) that is a full scan and a large in-memory load on
each query, instead of an approximate-nearest-neighbour search. The fix moves
the similarity search behind a `query_similar` method on the store: the Chroma
implementation uses its native `collection.query(...)` ANN index, and the
in-memory test double keeps a simple brute-force search. `retrieve_evidence`
just asks for the top-`limit` candidates.

## Current state

`backend/app/indexing/vector_store.py`:

- The `ChunkVectorStore` Protocol (lines 21–34) declares `add_records`,
  `active_records`, `deactivate_snapshot`, `copy_active_records`.
- `InMemoryChunkVectorStore.active_records` (lines 57–64) filters
  `self.records` by repo/snapshot/active.
- `ChromaChunkVectorStore.__init__` (lines 99–104) creates the collection:
  ```python
      self._collection: Any = self._client.get_or_create_collection(collection_name)
  ```
- `ChromaChunkVectorStore.active_records` (lines 122–139) calls
  `self._collection.get(where=..., include=["embeddings","documents","metadatas"])`
  and rebuilds `VectorRecord`s — this is the full scan.
- `VectorRecord` is defined at lines 13–18 with fields
  `chunk_id, embedding, text, metadata`.

`backend/app/retrieval/answering.py` (**after plan 003 has landed**):

- The vector loop inside `retrieve_evidence` looks like:
  ```python
      query_embedding = embedding_provider.embed_texts([question])[0]
      merged: dict[str, Evidence] = {}
      for record in vector_store.active_records(repo_id, snapshot_id):
          evidence = _evidence_from_vector(
              record, _cosine_similarity(query_embedding, record.embedding)
          )
          merged[evidence.chunk_id] = evidence
  ```
- `_cosine_similarity` is a module-level helper at the bottom of the file
  (lines ~167–175 at commit 91432ab):
  ```python
  def _cosine_similarity(left: list[float], right: list[float]) -> float:
      if not left or not right or len(left) != len(right):
          return 0.0
      numerator = sum(left[index] * right[index] for index in range(len(left)))
      left_norm = sqrt(sum(value * value for value in left))
      right_norm = sqrt(sum(value * value for value in right))
      if left_norm == 0 or right_norm == 0:
          return 0.0
      return numerator / (left_norm * right_norm)
  ```
- The file imports `from math import sqrt` at the top.

All existing tests use `InMemoryChunkVectorStore` (Chroma is never exercised in
the test suite), so the new `query_similar` is covered through the in-memory
implementation. The Chroma path is verified by typecheck only — consistent with
the rest of this module.

## Commands you will need

| Purpose   | Command (run from `backend/`)                    | Expected on success |
|-----------|--------------------------------------------------|---------------------|
| Tests     | `uv run pytest -q`                                | all pass            |
| Tests (focused) | `uv run pytest tests/test_answering.py -q`  | all pass            |
| Lint      | `uv run ruff check app/indexing/vector_store.py app/retrieval/answering.py tests/test_answering.py` | exit 0 |
| Typecheck | `uv run mypy app`                                | only the known `app/jobs/queue.py:29` error |

## Scope

**In scope** (the only files you should modify):
- `backend/app/indexing/vector_store.py`
- `backend/app/retrieval/answering.py`
- `backend/tests/test_answering.py`

**Out of scope** (do NOT touch):
- The keyword merge logic added by plan 003 — keep it exactly as is; only the
  vector half of `retrieve_evidence` changes.
- `add_records`, `deactivate_snapshot`, `copy_active_records`,
  `active_records` — leave these methods intact (`active_records` is still used
  elsewhere; do not delete it).
- `app/api/runtime.py`, `app/indexing/indexer.py`, `app/chat/streaming.py`.

## Git workflow

- Branch: `advisor/004-push-vector-search-into-store`
- Commit message: short imperative subject, e.g.
  `Add query_similar so vector search uses the store's index`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add a module-level cosine helper to the vector store

In `backend/app/indexing/vector_store.py`, add `from math import sqrt` to the
imports and add a module-level helper (place it near the other module-level
helpers at the bottom, e.g. after `_chroma_metadata`):

```python
def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(left[index] * right[index] for index in range(len(left)))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
```

### Step 2: Declare `query_similar` on the Protocol

Add this method signature to the `ChunkVectorStore` Protocol (after
`active_records`):

```python
    def query_similar(
        self,
        repo_id: str,
        snapshot_id: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[VectorRecord, float]]: ...
```

### Step 3: Implement `query_similar` on the in-memory store

Add to `InMemoryChunkVectorStore` (after `active_records`):

```python
    def query_similar(
        self,
        repo_id: str,
        snapshot_id: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[VectorRecord, float]]:
        scored = [
            (record, _cosine_similarity(query_embedding, record.embedding))
            for record in self.active_records(repo_id, snapshot_id)
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]
```

### Step 4: Implement `query_similar` on the Chroma store with native ANN

First, make the collection use cosine space so distances map to cosine
similarity. Change the `__init__` line:

```python
        self._collection: Any = self._client.get_or_create_collection(
            collection_name, metadata={"hnsw:space": "cosine"}
        )
```

Then add the method (after `active_records`):

```python
    def query_similar(
        self,
        repo_id: str,
        snapshot_id: str,
        query_embedding: list[float],
        limit: int,
    ) -> list[tuple[VectorRecord, float]]:
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where={"$and": [{"repo_id": repo_id}, {"snapshot_id": snapshot_id}, {"active": True}]},
            include=["embeddings", "documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        embeddings = result.get("embeddings", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        scored: list[tuple[VectorRecord, float]] = []
        for index in range(len(ids)):
            record = VectorRecord(
                chunk_id=str(ids[index]),
                embedding=[float(value) for value in embeddings[index]],
                text=str(documents[index]),
                metadata=dict(metadatas[index]),
            )
            scored.append((record, 1.0 - float(distances[index])))
        return scored
```

(With cosine space, Chroma returns `distance = 1 - cosine_similarity`, so
`similarity = 1 - distance`.)

### Step 5: Use `query_similar` in `retrieve_evidence`

In `backend/app/retrieval/answering.py`, replace the vector loop (the
`for record in vector_store.active_records(...)` block) with:

```python
    query_embedding = embedding_provider.embed_texts([question])[0]
    merged: dict[str, Evidence] = {}
    for record, score in vector_store.query_similar(
        repo_id, snapshot_id, query_embedding, limit
    ):
        evidence = _evidence_from_vector(record, score)
        merged[evidence.chunk_id] = evidence
```

Leave the keyword merge loop and the final `sorted(...)[:limit]` (from plan 003)
unchanged.

### Step 6: Remove the now-dead cosine helper from `answering.py`

Delete the `_cosine_similarity` function from `app/retrieval/answering.py` and
remove the now-unused `from math import sqrt` import.

**Verify**: `uv run ruff check app/retrieval/answering.py` → exit 0 (no F401 /
unused-import or undefined-name errors).

### Step 7: Add a direct unit test for `query_similar` ordering

In `backend/tests/test_answering.py`, add `VectorRecord` to the existing
`from app.indexing.vector_store import ...` line, then add:

```python
def test_query_similar_returns_top_matches_by_cosine() -> None:
    store = InMemoryChunkVectorStore()
    store.add_records(
        [
            VectorRecord(
                "a", [1.0, 0.0, 0.0], "alpha",
                {"repo_id": "r", "snapshot_id": "s", "active": True,
                 "path": "a.py", "start_line": 1, "end_line": 1},
            ),
            VectorRecord(
                "b", [0.0, 1.0, 0.0], "beta",
                {"repo_id": "r", "snapshot_id": "s", "active": True,
                 "path": "b.py", "start_line": 1, "end_line": 1},
            ),
        ],
        "test-embedding",
    )

    results = store.query_similar("r", "s", [1.0, 0.0, 0.0], 1)

    assert [record.chunk_id for record, _ in results] == ["a"]
    assert results[0][1] == 1.0
```

**Verify**: `uv run pytest tests/test_answering.py -q` → all pass.

## Test plan

- New test: `test_query_similar_returns_top_matches_by_cosine` — directly checks
  the in-memory `query_similar` returns the closest record first, with the right
  score, honoring `limit`.
- The existing answering and streaming tests are the regression guard: they all
  route through `query_similar` now (via the in-memory store) and must still
  pass with identical results — the cosine math is unchanged, only relocated.
- The Chroma `query_similar` path is not unit-tested (Chroma is not used in the
  suite); it is covered by `mypy` and manual/golden evaluation, consistent with
  the rest of the module.
- Verification: `uv run pytest -q` → all pass.

## Done criteria

ALL must hold:

- [ ] `grep -n "_cosine_similarity" backend/app/retrieval/answering.py` returns no matches
- [ ] `grep -n "def query_similar" backend/app/indexing/vector_store.py` returns 2 matches (in-memory + Chroma)
- [ ] `uv run pytest -q` exits 0, all pass, including the new `query_similar` test
- [ ] `uv run ruff check app/indexing/vector_store.py app/retrieval/answering.py tests/test_answering.py` exits 0
- [ ] `uv run mypy app` reports only the pre-existing `app/jobs/queue.py:29` error
- [ ] `git status` shows only the three in-scope files changed
- [ ] `plans/README.md` status row for 004 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Plan 003 has not landed (`AnsweringOptions` has no `keyword_boost` field).
- The "Current state" excerpts don't match the live files (drift).
- `uv run mypy app` reports a new error about `query_similar` not satisfying the
  Protocol — that means a signature mismatch between the Protocol and an
  implementation; fix the signature, don't suppress it.
- You find an existing persisted Chroma collection (a non-empty
  `data/.../chroma` directory) — switching the collection to `hnsw:space:
  cosine` does **not** retroactively change an already-created collection, so
  scores from old data would be wrong. This is a local single-user dev tool with
  disposable data: report it and recommend clearing the chroma data directory
  and re-ingesting, rather than silently proceeding.

## Maintenance notes

- For the reviewer: confirm `n_results=limit` and the cosine-space conversion
  (`1.0 - distance`) line up with the `min_evidence_score` threshold semantics
  used by `answer_question` — both are cosine-similarity in `[−1, 1]`.
- The in-memory store remains an honest test double (brute-force is fine for the
  small fixtures); only the Chroma path needed the ANN change.
- Follow-up deferred: if the keyword half is ever expected to surface chunks
  outside the vector top-`limit`, consider querying a larger vector candidate
  pool (e.g. `limit * k`) before the merge. Not needed today.
