"""Stage 4 read APIs: MCP server discovery, the redacted MCP audit trail and the tickets
created through the approval workflow (with their status-transition events).

Identity comes only from the bearer token. Normal users see their own audit rows / tickets;
admins see everything. Audit rows are already redacted when written (no tokens, keys or
passwords; arguments are summarised + hashed)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select

from opspilot.api.deps import CurrentUser, DbDep, require_role
from opspilot.core.errors import NotFoundError
from opspilot.db.models import McpToolAudit, Ticket, TicketEvent, User

router = APIRouter(prefix="/api/v1", tags=["mcp"])
AdminDep = Annotated[User, Depends(require_role("admin"))]


@router.get("/mcp/servers")
def mcp_servers(request: Request, admin: AdminDep) -> dict[str, Any]:
    """Live tools/list from the allowlisted MCP servers (launches them if needed)."""
    s = request.app.state.settings
    if s.agent_tool_transport != "mcp":
        return {"transport": s.agent_tool_transport, "servers": {}}
    from opspilot.mcp.client import get_hub

    hub = get_hub(s, request.app.state.sessionmaker)
    return {"transport": "mcp-stdio", "servers": hub.discover()}


@router.get("/mcp/audit")
def mcp_audit(
    db: DbDep,
    user: CurrentUser,
    thread_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    q = select(McpToolAudit).order_by(McpToolAudit.created_at.desc()).limit(limit)
    if thread_id:
        q = q.where(McpToolAudit.thread_id == thread_id)
    if user.role != "admin":
        q = q.where(McpToolAudit.user_id == user.id)
    return [
        {
            "side": a.side,
            "server": a.server,
            "tool": a.tool,
            "transport": a.transport,
            "user_id": str(a.user_id) if a.user_id else None,
            "user_role": a.user_role,
            "thread_id": a.thread_id,
            "run_id": a.run_id,
            "call_id": a.call_id,
            "args_summary": a.args_summary,
            "args_sha256": a.args_sha256,
            "status": a.status,
            "error_class": a.error_class,
            "error": a.error,
            "duration_ms": a.duration_ms,
            "output_bytes": a.output_bytes,
            "idempotency_key": a.idempotency_key,
            "created_at": a.created_at.isoformat(),
        }
        for a in db.scalars(q)
    ]


def _ticket(t: Ticket, events: list[TicketEvent]) -> dict[str, Any]:
    return {
        "number": t.number,
        "title": t.title,
        "priority": t.priority,
        "status": t.status,
        "service": t.service,
        "category": t.category,
        "source_draft_id": str(t.source_draft_id) if t.source_draft_id else None,
        "created_at": t.created_at.isoformat(),
        "events": [
            {
                "from_status": e.from_status,
                "to_status": e.to_status,
                "actor_id": str(e.actor_id) if e.actor_id else None,
                "approver_id": str(e.approver_id) if e.approver_id else None,
                "approval_id": str(e.approval_id) if e.approval_id else None,
                "approved_at": e.approved_at.isoformat() if e.approved_at else None,
                "note": e.note,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }


@router.get("/tickets")
def list_tickets(
    db: DbDep, user: CurrentUser, limit: int = Query(default=20, ge=1, le=100)
) -> list[dict[str, Any]]:
    q = select(Ticket).order_by(Ticket.created_at.desc()).limit(limit)
    if user.role != "admin":
        q = q.where(or_(Ticket.reporter_id == user.id, Ticket.assignee_id == user.id))
    out = []
    for t in db.scalars(q):
        ev = db.scalars(
            select(TicketEvent)
            .where(TicketEvent.ticket_id == t.id)
            .order_by(TicketEvent.created_at)
        ).all()
        out.append(_ticket(t, list(ev)))
    return out


@router.get("/tickets/{number}")
def get_ticket(number: str, db: DbDep, user: CurrentUser) -> dict[str, Any]:
    t = db.scalar(select(Ticket).where(Ticket.number == number[:32]))
    if t is None or (user.role != "admin" and user.id not in (t.reporter_id, t.assignee_id)):
        raise NotFoundError("ticket not found")  # same answer for "not yours"
    ev = db.scalars(
        select(TicketEvent).where(TicketEvent.ticket_id == t.id).order_by(TicketEvent.created_at)
    ).all()
    return _ticket(t, list(ev))
