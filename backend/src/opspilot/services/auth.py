from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from opspilot.core.config import Settings
from opspilot.core.errors import AuthError
from opspilot.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from opspilot.db.models import User
from opspilot.repositories.users import UserRepository

# Real hash of a random value, used to equalise timing when the email does not exist.
_DUMMY_HASH = hash_password(uuid.uuid4().hex)


class AuthService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.users = UserRepository(session)
        self.settings = settings

    def login(self, email: str, password: str) -> tuple[str, int, User]:
        user = self.users.get_by_email(email)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            raise AuthError("Invalid email or password")
        if not verify_password(password, user.password_hash) or not user.is_active:
            raise AuthError("Invalid email or password")
        user.last_login_at = datetime.now(UTC)
        token, expires = create_access_token(self.settings, subject=str(user.id), role=user.role)
        return token, expires, user

    def user_from_token(self, token: str) -> User:
        claims = decode_access_token(self.settings, token)
        try:
            user_id = uuid.UUID(str(claims["sub"]))
        except ValueError as exc:
            raise AuthError("Invalid token subject") from exc
        user = self.users.get(user_id)
        if user is None or not user.is_active:
            raise AuthError("User not found or disabled")
        return user
