import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class UserRecord:
    email: str
    password_hash: str
    is_active: bool
    created_at: str


class SQLiteUserStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_user(self, email: str, password_hash: str) -> UserRecord:
        record = UserRecord(_normalize_email(email), password_hash, True, _now())
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO users(email, password_hash, is_active, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    record.email,
                    record.password_hash,
                    1 if record.is_active else 0,
                    record.created_at,
                ),
            )
        return record

    def get_by_email(self, email: str) -> UserRecord | None:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT email, password_hash, is_active, created_at
                FROM users
                WHERE email = ?
                """,
                (_normalize_email(email),),
            ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def list_users(self) -> list[UserRecord]:
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT email, password_hash, is_active, created_at
                FROM users
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def set_active(self, email: str, is_active: bool) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE users SET is_active = ? WHERE email = ?",
                (1 if is_active else 0, _normalize_email(email)),
            )

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users(
                    email TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )


def _row_to_record(row: tuple[object, ...]) -> UserRecord:
    return UserRecord(str(row[0]), str(row[1]), bool(row[2]), str(row[3]))


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _now() -> str:
    return datetime.now(UTC).isoformat()
