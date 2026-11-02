from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from opspilot.core.config import Settings
from opspilot.core.errors import AuthError, ForbiddenError
from opspilot.db.models import User
from opspilot.db.session import get_db
from opspilot.services.auth import AuthService

_bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbDep = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbDep,
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise AuthError("Missing bearer token")
    return AuthService(db, settings).user_from_token(creds.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: str) -> Callable[[User], User]:
    def _checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ForbiddenError(f"Requires role: {', '.join(roles)}")
        return user

    return _checker
