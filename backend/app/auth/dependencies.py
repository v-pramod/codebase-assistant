from fastapi import Header

from app.auth.store import UserRecord
from app.auth.tokens import AuthError, decode_access_token
from app.core.config import get_settings
from app.core.errors import AppError

_BEARER_PREFIX = "Bearer "


def get_current_user(authorization: str | None = Header(default=None)) -> UserRecord:
    # Imported lazily to avoid an import cycle (routes imports this module for
    # the /auth/me handler) and to honor tests that repoint routes._user_store.
    from app.api import routes

    unauthorized = AppError("unauthorized", "Not authenticated.", 401)
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        raise unauthorized
    token = authorization[len(_BEARER_PREFIX) :].strip()
    try:
        username = decode_access_token(token, get_settings())
    except AuthError as exc:
        raise unauthorized from exc
    user = routes._user_store.get_by_username(username)
    if user is None or not user.is_active:
        raise unauthorized
    return user
