import sqlite3
from pathlib import Path

import pytest

from app.auth.store import SQLiteUserStore


def test_create_and_get_user(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user("user@example.com", "hash-1")

    record = store.get_by_email("user@example.com")
    assert record is not None
    assert record.email == "user@example.com"
    assert record.password_hash == "hash-1"
    assert record.is_active is True
    assert record.created_at


def test_get_unknown_email_returns_none(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    assert store.get_by_email("nobody@example.com") is None


def test_duplicate_email_raises(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user("dup@example.com", "hash-1")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_user("dup@example.com", "hash-2")


def test_email_is_normalized_on_write_and_lookup(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user("  MixedCase@Example.com  ", "hash-1")

    record = store.get_by_email("mixedcase@example.com")
    assert record is not None
    assert record.email == "mixedcase@example.com"
    # Lookup also normalizes the queried email.
    assert store.get_by_email("  MIXEDCASE@EXAMPLE.COM ") is not None


def test_list_users_and_set_active(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user("a@example.com", "hash-a")
    store.create_user("b@example.com", "hash-b")

    emails = {user.email for user in store.list_users()}
    assert emails == {"a@example.com", "b@example.com"}

    store.set_active("a@example.com", False)
    record = store.get_by_email("a@example.com")
    assert record is not None
    assert record.is_active is False
