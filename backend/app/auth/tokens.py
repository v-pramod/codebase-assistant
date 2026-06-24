from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings


class AuthError(Exception):
    """Raised when a token is missing, malformed, expired, or tampered with."""


def create_access_token(subject: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {"sub": subject, "iat": now, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> str:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid or expired token.") from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthError("Token is missing a subject.")
    return subject
