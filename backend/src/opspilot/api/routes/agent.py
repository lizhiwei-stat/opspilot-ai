"""Agent API (Stage 3). Identity always comes from the bearer token; the request body can
never set user id / role. Approval is admin-only and re-checked inside the graph."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
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
    # stream=True: return immediately (status=running) and follow /runs/{id}/events (SSE)
    stream: bool = False


class ApprovalIn(BaseModel):
    decision: Literal["approved", "rejected", "returned"]  # returned = 退回修改 (needs comment)
    comment: str = Field(default="", max_length=500)


@router.post("/runs")
def create_run(body: RunIn, user: CurrentUser, svc: AgentDep) -> dict[str, Any]:
    """Start a turn (new thread, or a follow-up turn when thread_id is given)."""
    return svc.start(user, body.question, body.thread_id, background=body.stream)


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
    stream: bool = False,
) -> dict[str, Any]:
    """Human-in-the-loop: resume an interrupted run with an admin decision."""
    return svc.approve(run_id, admin, body.decision, body.comment, background=stream)


@router.get("/approvals")
def approval_queue(admin: AdminDep, svc: AgentDep) -> list[dict[str, Any]]:
    """Admin approval queue: runs waiting at the approval interrupt (survives restarts)."""
    return svc.pending_approvals(admin)


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: uuid.UUID, user: CurrentUser, svc: AgentDep, stream: bool = False) -> dict:
    """Continue a run that stopped mid-graph (backend restart or user interrupt)."""
    return svc.resume(run_id, user, background=stream)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: uuid.UUID, user: CurrentUser, svc: AgentDep) -> dict:
    """Interrupt a running run at the next node boundary (status -> aborted, resumable)."""
    return svc.request_cancel(run_id, user)


@router.get("/threads")
def list_threads(user: CurrentUser, svc: AgentDep) -> list[dict[str, Any]]:
    """Conversation sessions (one checkpointer thread each) of the caller (admin: all)."""
    return svc.threads(user)


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, user: CurrentUser, svc: AgentDep) -> list[dict[str, Any]]:
    return svc.thread_runs(thread_id, user)


_END_STATES = ("completed", "refused", "failed", "expired", "aborted", "awaiting_approval")


def _sse(seq: int | None, event: str, data: Any) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: uuid.UUID,
    user: CurrentUser,
    svc: AgentDep,
    after: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
    poll_ms: Annotated[int, Query(ge=50, le=2000)] = 250,
    max_s: Annotated[int, Query(ge=1, le=900)] = 600,
) -> StreamingResponse:
    """SSE stream of durable agent_events (node/event streaming, NOT token streaming).

    Cursor = `Last-Event-ID` header (standard reconnect) or `?after=<seq>`; events with
    seq > cursor are replayed from Postgres first, then new ones are pushed as they are
    written. The stream ends with `event: end` once the run is idle (finished, aborted or
    awaiting approval) and every event up to its `final` has been sent. Comment lines
    (`: hb`) are heartbeats. Disconnecting never affects the run (it runs in the backend)."""
    cursor = (
        after
        if after is not None
        else (int(last_event_id) if (last_event_id or "").isdigit() else 0)
    )
    status, first = svc.events(run_id, user, cursor)  # 404 before the stream starts

    def gen() -> Iterator[str]:
        nonlocal cursor
        yield "retry: 2000\n\n"
        batch, st = first, status
        t_end = time.monotonic() + max_s
        last_hb = time.monotonic()
        while True:
            for e in batch:
                cursor = e["seq"]
                yield _sse(e["seq"], e["type"], e)
            last_type = batch[-1]["type"] if batch else None
            if st in _END_STATES and (
                last_type == "final" or (not batch and _idle(svc, run_id, user))
            ):
                yield _sse(None, "end", {"status": st, "cursor": cursor})
                return
            if time.monotonic() > t_end:
                yield _sse(None, "timeout", {"cursor": cursor})
                return
            if time.monotonic() - last_hb > 10:
                last_hb = time.monotonic()
                yield ": hb\n\n"
            time.sleep(poll_ms / 1000)
            st, batch = svc.events(run_id, user, cursor)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _idle(svc: Any, run_id: uuid.UUID, user: User) -> bool:
    """True when nothing more will be written (no in-process execution for this run)."""
    return run_id not in getattr(svc, "_active", set())
