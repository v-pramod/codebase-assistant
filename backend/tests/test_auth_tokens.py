import pytest

from app.auth.tokens import AuthError, create_access_token, decode_access_token
from app.core.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
        jwt_secret="test-secret",
        jwt_algorithm="HS256",
        access_token_ttl_minutes=720,
    )
    base.update(overrides)
    return Settings(**base)


def test_round_trip_returns_subject() -> None:
    settings = _settings()
    token = create_access_token("user@example.com", settings)
    assert decode_access_token(token, settings) == "user@example.com"


def test_expired_token_raises() -> None:
    settings = _settings(access_token_ttl_minutes=-1)
    token = create_access_token("user@example.com", settings)
    with pytest.raises(AuthError):
        decode_access_token(token, _settings())


def test_token_signed_with_different_secret_raises() -> None:
    token = create_access_token("user@example.com", _settings(jwt_secret="one"))
    with pytest.raises(AuthError):
        decode_access_token(token, _settings(jwt_secret="two"))


def test_garbage_string_raises() -> None:
    with pytest.raises(AuthError):
        decode_access_token("not-a-token", _settings())
