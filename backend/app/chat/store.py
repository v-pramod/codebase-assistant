import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.retrieval.answering import Citation


@dataclass(frozen=True)
class ChatSessionRecord:
    session_id: str
    repo_id: str
    title: str
    created_at: str


@dataclass(frozen=True)
class ChatMessageRecord:
    message_id: str
    session_id: str
    role: str
    content: str
    model: str | None
    snapshot_id: str | None
    citations: list[Citation]
    created_at: str


class SQLiteChatStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_session(self, repo_id: str, title: str) -> ChatSessionRecord:
        session = ChatSessionRecord(str(uuid4()), repo_id, title, _now())
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO chat_sessions(session_id, repo_id, title, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session.session_id, session.repo_id, session.title, session.created_at),
            )
        return session

    def list_sessions(self, repo_id: str) -> list[ChatSessionRecord]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT session_id, repo_id, title, created_at
                FROM chat_sessions
                WHERE repo_id = ?
                ORDER BY created_at DESC
                """,
                (repo_id,),
            ).fetchall()
        return [
            ChatSessionRecord(str(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in rows
        ]

    def get_session(self, session_id: str) -> ChatSessionRecord | None:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT session_id, repo_id, title, created_at
                FROM chat_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return ChatSessionRecord(str(row[0]), str(row[1]), str(row[2]), str(row[3]))

    def update_session_title(self, session_id: str, title: str) -> ChatSessionRecord | None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE chat_sessions
                SET title = ?
                WHERE session_id = ?
                """,
                (title, session_id),
            )
        return self.get_session(session_id)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model: str | None = None,
        snapshot_id: str | None = None,
        citations: list[Citation] | None = None,
    ) -> ChatMessageRecord:
        citations = citations or []
        message = ChatMessageRecord(
            str(uuid4()),
            session_id,
            role,
            content,
            model,
            snapshot_id,
            citations,
            _now(),
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO chat_messages(
                    message_id,
                    session_id,
                    role,
                    content,
                    model,
                    snapshot_id,
                    citations_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.model,
                    message.snapshot_id,
                    _serialize_citations(message.citations),
                    message.created_at,
                ),
            )
        return message

    def list_messages(self, session_id: str) -> list[ChatMessageRecord]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    message_id,
                    session_id,
                    role,
                    content,
                    model,
                    snapshot_id,
                    citations_json,
                    created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()
        return [
            ChatMessageRecord(
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]) if row[4] is not None else None,
                str(row[5]) if row[5] is not None else None,
                _deserialize_citations(str(row[6])),
                str(row[7]),
            )
            for row in rows
        ]

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions(
                    session_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages(
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model TEXT,
                    snapshot_id TEXT,
                    citations_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id)
                )
                """
            )


def _serialize_citations(citations: list[Citation]) -> str:
    return json.dumps(
        [
            {
                "path": citation.path,
                "start_line": citation.start_line,
                "end_line": citation.end_line,
                "snippet": citation.snippet,
                "commit_sha": citation.commit_sha,
                "local_ref": citation.local_ref,
                "github_permalink": citation.github_permalink,
            }
            for citation in citations
        ]
    )


def _deserialize_citations(raw: str) -> list[Citation]:
    payload = json.loads(raw)
    if not isinstance(payload, list):
        return []
    return [
        Citation(
            str(item["path"]),
            int(item["start_line"]),
            int(item["end_line"]),
            str(item["snippet"]),
            str(item["commit_sha"]) if item.get("commit_sha") is not None else None,
            str(item["local_ref"]) if item.get("local_ref") is not None else None,
            str(item["github_permalink"]) if item.get("github_permalink") is not None else None,
        )
        for item in payload
        if isinstance(item, dict)
    ]


def _now() -> str:
    return datetime.now(UTC).isoformat()
