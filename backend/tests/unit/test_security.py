from datetime import timedelta

from app.auth.security import create_access_token, decode_access_token, hash_password, verify_password


def test_hash_password_is_not_plaintext():
    hashed = hash_password("mypassword")
    assert hashed != "mypassword"


def test_verify_password_correct():
    hashed = hash_password("mypassword")
    assert verify_password("mypassword", hashed) is True


def test_verify_password_incorrect():
    hashed = hash_password("mypassword")
    assert verify_password("wrongpassword", hashed) is False


def test_access_token_roundtrip():
    token = create_access_token(subject="alice")
    assert decode_access_token(token) == "alice"


def test_expired_token_is_rejected():
    token = create_access_token(subject="alice", expires_delta=timedelta(seconds=-1))
    assert decode_access_token(token) is None


def test_garbage_token_is_rejected():
    assert decode_access_token("not-a-real-token") is None