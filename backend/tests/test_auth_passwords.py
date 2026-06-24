from app.auth.passwords import hash_password, verify_password


def test_hash_is_not_plaintext_and_verifies() -> None:
    h = hash_password("correct horse")
    assert h != "correct horse"
    assert verify_password("correct horse", h) is True


def test_wrong_password_fails() -> None:
    assert verify_password("nope", hash_password("secret")) is False


def test_hashes_are_salted_and_unique() -> None:
    assert hash_password("same") != hash_password("same")
