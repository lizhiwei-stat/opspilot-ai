import time

import jwt
import pytest

from opspilot.core.errors import AuthError
from opspilot.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from tests.conftest import TEST_SECRET, make_settings


def test_password_hash_roundtrip() -> None:
    h = hash_password("S3cret!")
    assert h.startswith("$argon2id$")
    assert verify_password("S3cret!", h) and not verify_password("wrong", h)


def test_jwt_roundtrip_and_claims() -> None:
    s = make_settings()
    token, exp = create_access_token(s, subject="u1", role="admin")
    claims = decode_access_token(s, token)
    assert claims["sub"] == "u1" and claims["role"] == "admin" and exp == 3600


def test_jwt_tampered_or_wrong_secret_rejected() -> None:
    s = make_settings()
    token, _ = create_access_token(s, subject="u1", role="user")
    other = make_settings(jwt_secret=TEST_SECRET + "-other")
    with pytest.raises(AuthError, match="Invalid"):
        decode_access_token(other, token)


def test_jwt_alg_none_rejected() -> None:
    s = make_settings()
    forged = jwt.encode(
        {"sub": "u1", "role": "admin", "iss": "opspilot", "exp": int(time.time()) + 60},
        key=None,
        algorithm="none",
    )
    with pytest.raises(AuthError):
        decode_access_token(s, forged)


def test_jwt_expired_rejected() -> None:
    s = make_settings(jwt_expire_minutes=-1)
    token, _ = create_access_token(s, subject="u1", role="user")
    with pytest.raises(AuthError, match="expired"):
        decode_access_token(make_settings(), token)
