"""Password hashing (Argon2 via pwdlib) and JWT (PyJWT) helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import structlog
from pwdlib import PasswordHash

from opspilot.core.config import Settings
from opspilot.core.errors import AuthError

_hasher = PasswordHash.recommended()
_log = structlog.get_logger("opspilot.auth")


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _hasher.verify(raw, hashed)


def create_access_token(settings: Settings, *, subject: str, role: str) -> tuple[str, int]:
    now = datetime.now(UTC)
    expires = settings.jwt_expire_minutes * 60
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=expires),
        "jti": uuid.uuid4().hex,
        "iss": "opspilot",
    }
    token = jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer="opspilot",
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        _log.info("auth_token_rejected", reason=type(exc).__name__)
        raise AuthError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        # diagnostic only: the class name + unverified time claims vs server clock (no token)
        try:
            u = jwt.decode(token, options={"verify_signature": False})
            times = {k: u.get(k) for k in ("iat", "nbf", "exp")}
        except Exception:
            times = {}
        _log.info(
            "auth_token_rejected",
            reason=type(exc).__name__,
            server_now=round(datetime.now(UTC).timestamp(), 3),
            **times,
        )
        raise AuthError("Invalid token") from exc
