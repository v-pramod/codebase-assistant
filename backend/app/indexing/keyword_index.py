import re
import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.chunking.chunker import CodeChunk


@dataclass(frozen=True)
class KeywordHit:
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    text: str


class SQLiteKeywordIndex:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add_chunks(self, repo_id: str, snapshot_id: str, chunks: list[CodeChunk]) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO chunk_keyword_index(
                    chunk_id, repo_id, snapshot_id, active, path, start_line, end_line, text
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        repo_id,
                        snapshot_id,
                        chunk.path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content,
                    )
                    for chunk in chunks
                ],
            )

    def search_active(self, repo_id: str, snapshot_id: str, query: str) -> list[KeywordHit]:
        fts_query = _safe_fts_query(query)
        if not fts_query:
            return []
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, path, start_line, end_line, text
                FROM chunk_keyword_index
                WHERE chunk_keyword_index MATCH ? AND repo_id = ? AND snapshot_id = ? AND active = 1
                ORDER BY rank
                """,
                (fts_query, repo_id, snapshot_id),
            ).fetchall()
        return [
            KeywordHit(
                chunk_id=str(row[0]),
                path=str(row[1]),
                start_line=int(row[2]),
                end_line=int(row[3]),
                text=str(row[4]),
            )
            for row in rows
        ]

    def deactivate_snapshot(self, repo_id: str, snapshot_id: str) -> None:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, repo_id, snapshot_id, path, start_line, end_line, text
                FROM chunk_keyword_index
                WHERE repo_id = ? AND snapshot_id = ?
                """,
                (repo_id, snapshot_id),
            ).fetchall()
            connection.execute(
                "DELETE FROM chunk_keyword_index WHERE repo_id = ? AND snapshot_id = ?",
                (repo_id, snapshot_id),
            )
            connection.executemany(
                """
                INSERT INTO chunk_keyword_index(
                    chunk_id, repo_id, snapshot_id, active, path, start_line, end_line, text
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?)
                """,
                rows,
            )

    def copy_active_chunks(
        self,
        repo_id: str,
        source_snapshot_id: str,
        target_snapshot_id: str,
        exclude_paths: set[str],
    ) -> None:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, path, start_line, end_line, text
                FROM chunk_keyword_index
                WHERE repo_id = ? AND snapshot_id = ? AND active = 1
                """,
                (repo_id, source_snapshot_id),
            ).fetchall()
            connection.executemany(
                """
                INSERT INTO chunk_keyword_index(
                    chunk_id, repo_id, snapshot_id, active, path, start_line, end_line, text
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                """,
                [
                    (
                        _copied_chunk_id(str(row[0]), target_snapshot_id),
                        repo_id,
                        target_snapshot_id,
                        str(row[1]),
                        int(row[2]),
                        int(row[3]),
                        str(row[4]),
                    )
                    for row in rows
                    if str(row[1]) not in exclude_paths
                ],
            )

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_keyword_index USING fts5(
                    chunk_id UNINDEXED,
                    repo_id UNINDEXED,
                    snapshot_id UNINDEXED,
                    active UNINDEXED,
                    path UNINDEXED,
                    start_line UNINDEXED,
                    end_line UNINDEXED,
                    text
                )
                """
            )


def _copied_chunk_id(source_chunk_id: str, target_snapshot_id: str) -> str:
    return sha256(f"{source_chunk_id}|{target_snapshot_id}".encode()).hexdigest()


_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_FTS_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "used",
    "what",
    "where",
    "which",
    "who",
    "why",
    "with",
}


def _safe_fts_query(query: str) -> str:
    tokens = []
    seen = set()
    for match in _FTS_TOKEN_RE.finditer(query):
        token = match.group(0).lower()
        if token in _FTS_STOP_WORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return " OR ".join(f'"{token}"' for token in tokens)
