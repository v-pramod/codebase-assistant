from pathlib import Path

import pytest

from app.auth import cli
from app.auth.passwords import verify_password
from app.auth.store import SQLiteUserStore
from app.core.config import get_settings


def test_adduser_creates_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)

    exit_code = cli.main(["newuser", "--password", "hunter2-pass"])

    assert exit_code == 0
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    user = store.get_by_username("newuser")
    assert user is not None
    assert verify_password("hunter2-pass", user.password_hash)


def test_adduser_reads_password_via_getpass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    monkeypatch.setattr(cli, "getpass", lambda _prompt="": "prompted-pass")

    exit_code = cli.main(["typeduser"])

    assert exit_code == 0
    store = SQLiteUserStore(tmp_path / "auth.sqlite3")
    user = store.get_by_username("typeduser")
    assert user is not None
    assert verify_password("prompted-pass", user.password_hash)


def test_adduser_duplicate_username_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)

    assert cli.main(["dupuser", "--password", "first-pass"]) == 0
    exit_code = cli.main(["dupuser", "--password", "second-pass"])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "dupuser" in (captured.err + captured.out)
