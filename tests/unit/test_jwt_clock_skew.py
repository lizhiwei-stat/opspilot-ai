"""Regression: intermittent 401 right after login (Stage 2.5 e2e, 2/5 runs).

Root cause (captured in backend logs): ImmatureSignatureError, iat/nbf 6.2 s ahead of the
server clock that had just issued the token - the WSL2 VM realtime clock stepped backwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from opspilot.core.config import Settings
from opspilot.core.errors import AuthError
from opspilot.core.security import create_access_token, decode_access_token

SUB = "00000000-0000-0000-0000-000000000001"


def _token(settings: Settings, ahead_s: float) -> str:
    """Token as issued before the clock stepped back by `ahead_s` seconds."""
    t, _ = create_access_token(
        settings, subject=SUB, role="user", now=datetime.now(UTC) + timedelta(seconds=ahead_s)
    )
    return t


def test_token_survives_clock_step_back_observed_in_wsl() -> None:
    s = Settings()
    assert decode_access_token(s, _token(s, 8.7))["sub"] == SUB  # observed steps: 6-8.6 s


def test_without_leeway_the_same_token_fails_like_the_bug() -> None:
    s = Settings(jwt_clock_skew_s=0)
    with pytest.raises(AuthError, match="Invalid token"):
        decode_access_token(s, _token(s, 8.7))


def test_skew_is_bounded() -> None:
    s = Settings()
    with pytest.raises(AuthError):
        decode_access_token(s, _token(s, s.jwt_clock_skew_s + 30))


def test_expired_token_still_rejected_beyond_leeway() -> None:
    s = Settings()
    t, _ = create_access_token(
        s, subject=SUB, role="user", now=datetime.now(UTC) - timedelta(hours=2)
    )
    with pytest.raises(AuthError, match="expired"):
        decode_access_token(s, t)
