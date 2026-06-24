from pathlib import Path

import pytest

from app.auth import cli
from app.auth.passwords import verify_password
from app.auth.store import SQLiteUserStore
from app.core.config import get_settings


def test_adduser_creates_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)

    exit_code = cli.main(["new@example.com", "--password", "hunter2-pass"])

    assert exit_code == 0
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    user = store.get_by_email("new@example.com")
    assert user is not None
    assert verify_password("hunter2-pass", user.password_hash)


def test_adduser_reads_password_via_getpass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    monkeypatch.setattr(cli, "getpass", lambda _prompt="": "prompted-pass")

    exit_code = cli.main(["typed@example.com"])

    assert exit_code == 0
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    user = store.get_by_email("typed@example.com")
    assert user is not None
    assert verify_password("prompted-pass", user.password_hash)


def test_adduser_duplicate_email_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)

    assert cli.main(["dup@example.com", "--password", "first-pass"]) == 0
    exit_code = cli.main(["dup@example.com", "--password", "second-pass"])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "dup@example.com" in (captured.err + captured.out)
