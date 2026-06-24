import sqlite3
from pathlib import Path

import pytest

from app.auth.store import SQLiteUserStore


def test_create_and_get_user(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user("user", "hash-1")

    record = store.get_by_username("user")
    assert record is not None
    assert record.username == "user"
    assert record.password_hash == "hash-1"
    assert record.is_active is True
    assert record.created_at


def test_get_unknown_username_returns_none(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    assert store.get_by_username("nobody") is None


def test_duplicate_username_raises(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user("dup", "hash-1")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_user("dup", "hash-2")


def test_username_is_normalized_on_write_and_lookup(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user("  MixedCase  ", "hash-1")

    record = store.get_by_username("mixedcase")
    assert record is not None
    assert record.username == "mixedcase"
    # Lookup also normalizes the queried username.
    assert store.get_by_username("  MIXEDCASE ") is not None


def test_list_users_and_set_active(tmp_path: Path) -> None:
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    store.create_user("a", "hash-a")
    store.create_user("b", "hash-b")

    usernames = {user.username for user in store.list_users()}
    assert usernames == {"a", "b"}

    store.set_active("a", False)
    record = store.get_by_username("a")
    assert record is not None
    assert record.is_active is False
