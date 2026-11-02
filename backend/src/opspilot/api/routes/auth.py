from __future__ import annotations

from fastapi import APIRouter

from opspilot.api.deps import CurrentUser, DbDep, SettingsDep
from opspilot.api.schemas import LoginRequest, LoginResponse, UserOut
from opspilot.core.logging import get_logger
from opspilot.services.auth import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
log = get_logger("opspilot.auth")


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: DbDep, settings: SettingsDep) -> LoginResponse:
    token, expires, user = AuthService(db, settings).login(body.email, body.password)
    log.info("login_success", user_id=str(user.id), role=user.role)
    return LoginResponse(access_token=token, expires_in=expires, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
