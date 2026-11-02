from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from opspilot.api.deps import CurrentUser, DbDep, SettingsDep
from opspilot.services.qa import QAService
from opspilot.services.retrieval import Mode, RetrievalService

router = APIRouter(prefix="/api/v1", tags=["retrieval"])


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    mode: Mode = "hybrid"
    top_k: int = Field(5, ge=1, le=50)
    kb_ids: list[uuid.UUID] | None = None


class QAIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(5, ge=1, le=20)
    kb_ids: list[uuid.UUID] | None = None


@router.post("/search")
def search(
    body: SearchIn, request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> dict[str, object]:
    svc = RetrievalService(db, settings, request.app.state.embedding)
    return svc.search(
        user, body.query, mode=body.mode, top_k=body.top_k, kb_ids=body.kb_ids
    ).to_dict()


@router.post("/qa")
def qa(
    body: QAIn, request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> dict[str, object]:
    svc = QAService(db, settings, request.app.state.embedding, request.app.state.llm)
    return svc.answer(user, body.question, top_k=body.top_k, kb_ids=body.kb_ids).to_dict()
