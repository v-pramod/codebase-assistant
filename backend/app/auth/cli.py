import argparse
import sqlite3
import sys
from getpass import getpass

from app.auth.passwords import hash_password
from app.auth.store import SQLiteUserStore
from app.core.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codebase-assistant-adduser",
        description="Create an invite-only user for the codebase assistant.",
    )
    parser.add_argument("email", help="Email address of the user to create.")
    parser.add_argument(
        "--password",
        help="Password (omit to be prompted securely; intended for scripting/tests).",
    )
    args = parser.parse_args(argv)

    password = args.password if args.password is not None else getpass("Password: ")
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        return 1

    settings = get_settings()
    store = SQLiteUserStore(settings.data_dir / "auth.sqlite3")
    try:
        user = store.create_user(args.email, hash_password(password))
    except sqlite3.IntegrityError:
        print(f"A user with email {args.email} already exists.", file=sys.stderr)
        return 1

    print(f"Created user {user.email}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
