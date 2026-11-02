"""Agent API (Stage 3). Identity always comes from the bearer token; the request body can
never set user id / role. Approval is admin-only and re-checked inside the graph."""

from __future__ import annotations

import threading
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from opspilot.api.deps import CurrentUser, require_role
from opspilot.db.models import User

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
_LOCK = threading.Lock()


def get_agent(request: Request) -> Any:
    app = request.app
    svc = getattr(app.state, "agent_service", None)
    if svc is not None:
        return svc
    with _LOCK:
        if getattr(app.state, "agent_service", None) is None:
            from langgraph.checkpoint.postgres import PostgresSaver

            from opspilot.agent.checkpoint import open_pool
            from opspilot.agent.service import AgentService

            s = app.state.settings
            pool = open_pool(s)
            app.state.agent_pool = pool
            app.state.agent_service = AgentService(
                s,
                app.state.sessionmaker,
                app.state.embedding,
                PostgresSaver(pool),  # type: ignore[arg-type]
            )
    return app.state.agent_service


AgentDep = Annotated[Any, Depends(get_agent)]
AdminDep = Annotated[User, Depends(require_role("admin"))]


class RunIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    thread_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


class ApprovalIn(BaseModel):
    decision: Literal["approved", "rejected", "returned"]  # returned = 退回修改 (needs comment)
    comment: str = Field(default="", max_length=500)


@router.post("/runs")
def create_run(body: RunIn, user: CurrentUser, svc: AgentDep) -> dict[str, Any]:
    """Start a turn (new thread, or a follow-up turn when thread_id is given)."""
    return svc.start(user, body.question, body.thread_id)


@router.get("/runs")
def list_runs(user: CurrentUser, svc: AgentDep) -> list[dict[str, Any]]:
    return svc.list(user)


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, user: CurrentUser, svc: AgentDep) -> dict:
    return svc.view(run_id, user)


@router.get("/runs/{run_id}/history")
def run_history(run_id: uuid.UUID, user: CurrentUser, svc: AgentDep) -> list:
    """Durable checkpoint history of the run's thread (PostgresSaver)."""
    return svc.history(run_id, user)


@router.post("/runs/{run_id}/approval")
def approve_run(
    run_id: uuid.UUID,
    body: ApprovalIn,
    admin: AdminDep,
    svc: AgentDep,
) -> dict[str, Any]:
    """Human-in-the-loop: resume an interrupted run with an admin decision."""
    return svc.approve(run_id, admin, body.decision, body.comment)


@router.get("/approvals")
def approval_queue(admin: AdminDep, svc: AgentDep) -> list[dict[str, Any]]:
    """Admin approval queue: runs waiting at the approval interrupt (survives restarts)."""
    return svc.pending_approvals(admin)


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: uuid.UUID, user: CurrentUser, svc: AgentDep) -> dict:
    """Continue a run that stopped mid-graph (e.g. after a backend restart)."""
    return svc.resume(run_id, user)
