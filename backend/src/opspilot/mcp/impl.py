"""Implementations of the Stage 4 tools that did not exist in Stage 3. Same signature as the
Stage 3 implementations in agent/tools.py: (session, acting_user, validated_args, ctx).
They run INSIDE the MCP server processes; ACL is always evaluated against the DB user."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opspilot.agent.tools import (
    DATA,
    TicketOut,
    ToolContext,
    ToolError,
    ToolNotFound,
    ToolPermissionError,
    _can_read_service,
    idempotency_key,
)
from opspilot.db.models import (
    Approval,
    Document,
    KnowledgeBase,
    Ticket,
    TicketDraft,
    TicketEvent,
    User,
)
from opspilot.mcp.contracts import (
    DocumentChunk,
    DocumentOut,
    GetDocumentIn,
    GetServiceStatusIn,
    KnowledgeBaseItem,
    KnowledgeBasesOut,
    ListTicketsIn,
    ReviseTicketDraftIn,
    ServiceStatusOut,
    TicketChangeOut,
    TicketDraftOut,
    TicketListOut,
    UpdateTicketStatusIn,
)
from opspilot.rag.sanitize import sanitize
from opspilot.repositories.knowledge import KnowledgeRepository


class ToolConflict(ToolError):
    """State conflict (already executed elsewhere, draft changed, bad transition)."""


# ticket status machine (Stage 4): only these transitions are executable, and only via an
# approved draft
TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "resolved", "closed"},
    "in_progress": {"resolved", "open"},
    "resolved": {"closed", "open"},
    "closed": set(),
}


def _ticket_out(t: Ticket) -> TicketOut:
    return TicketOut(
        number=t.number,
        title=t.title,
        status=t.status,
        priority=t.priority,
        service=t.service,
        category=t.category,
        description=sanitize(t.description).text[:1000],
    )


# ---------------------------------------------------------------- knowledge
def get_document(s: Session, user: User, a: Any, ctx: ToolContext) -> DocumentOut:
    if not isinstance(a, GetDocumentIn):
        raise TypeError("bad tool input")
    repo = KnowledgeRepository(s)
    docs = s.scalars(
        select(Document).where(Document.source_name == a.document, Document.status == "active")
    ).all()
    doc = next((d for d in docs if repo.can_read(user, d.kb_id)), None)
    if doc is None:  # same answer for "absent" and "not permitted"
        raise ToolNotFound(f"document {a.document} not found")
    kb = s.get(KnowledgeBase, doc.kb_id)
    chunks, total = repo.chunks_of(doc.id, limit=a.max_chunks, offset=0)
    out = []
    for c in chunks:
        clean = sanitize(c.content)
        loc = f"p.{c.page_number}" if c.page_number else (c.heading_path or f"#{c.chunk_index}")
        out.append(
            DocumentChunk(
                index=c.chunk_index, locator=loc[:120], text=clean.text[:900], flagged=clean.flagged
            )
        )
    return DocumentOut(
        document=doc.source_name,
        knowledge_base=kb.slug if kb else "?",
        version=int(doc.current_version or 1),
        total_chunks=total,
        chunks=out,
    )


def list_knowledge_bases(s: Session, user: User, a: Any, ctx: ToolContext) -> KnowledgeBasesOut:
    rows = KnowledgeRepository(s).list_kbs(user)  # ACL-scoped
    return KnowledgeBasesOut(
        knowledge_bases=[
            KnowledgeBaseItem(slug=kb.slug, name=kb.name, visibility=kb.visibility, documents=n)
            for kb, n, _ in rows
        ]
    )


# ---------------------------------------------------------------- ticket
def list_tickets(s: Session, user: User, a: Any, ctx: ToolContext) -> TicketListOut:
    if not isinstance(a, ListTicketsIn):
        raise TypeError("bad tool input")
    stmt = select(Ticket)
    if user.role != "admin":
        stmt = stmt.where((Ticket.reporter_id == user.id) | (Ticket.assignee_id == user.id))
    if a.status:
        stmt = stmt.where(Ticket.status == a.status)
    if a.service:
        stmt = stmt.where(Ticket.service == a.service)
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = s.scalars(stmt.order_by(Ticket.number.desc()).limit(a.limit)).all()
    return TicketListOut(tickets=[_ticket_out(t) for t in rows], total=int(total))


def _next_number(s: Session) -> str:
    nums = [
        int(m.group(1))
        for n in s.scalars(select(Ticket.number))
        if (m := re.fullmatch(r"TCK-(\d{4,})", n))
    ]
    return f"TCK-{(max(nums) if nums else 0) + 1:04d}"


def update_ticket_status(s: Session, user: User, a: Any, ctx: ToolContext) -> TicketChangeOut:
    """Execute an approved draft exactly once (row lock + unique(approval_id) +
    unique(tickets.source_draft_id)); a repeat returns the first result (deduplicated)."""
    if not isinstance(a, UpdateTicketStatusIn):
        raise TypeError("bad tool input")
    did, aid = uuid.UUID(a.draft_id), uuid.UUID(a.approval_id)
    draft = s.scalar(select(TicketDraft).where(TicketDraft.id == did).with_for_update())
    appr = s.get(Approval, aid)
    if draft is None or appr is None or appr.draft_id != draft.id:
        raise ToolNotFound("approval not found for this draft")
    if appr.decision != "approved":
        raise ToolPermissionError(f"approval decision is {appr.decision!r}, not 'approved'")
    reviewer = s.get(User, appr.reviewer_id)
    if reviewer is None or not reviewer.is_active or reviewer.role != "admin":
        raise ToolPermissionError("approval was not made by an active admin")
    if user.role != "admin" and user.id != draft.created_by:
        raise ToolPermissionError("caller may not execute this draft")
    if appr.payload_sha256 != draft.payload_sha256:
        raise ToolConflict("draft payload changed after approval")
    prev = s.scalar(select(TicketEvent).where(TicketEvent.approval_id == aid))
    if prev is not None:
        t = s.get(Ticket, prev.ticket_id)
        return TicketChangeOut(
            ticket_number=t.number if t else "?",
            status=prev.to_status,
            from_status=prev.from_status,
            draft_status=draft.status,
            deduplicated=True,
        )
    if draft.status != "approved":
        raise ToolConflict(f"draft is {draft.status}, not approved")
    if draft.action == "create":
        t = Ticket(
            number=_next_number(s),
            title=draft.title,
            description=draft.description,
            category=draft.category,
            priority=draft.priority,
            status="open",
            service=draft.service,
            reporter_id=draft.created_by,
            source_draft_id=draft.id,
            tags=["agent", f"draft-r{draft.revision}"],
        )
        s.add(t)
        s.flush()
        frm, to = None, "open"
    else:
        t = s.scalar(select(Ticket).where(Ticket.id == draft.target_ticket_id).with_for_update())
        if t is None:
            raise ToolNotFound("target ticket not found")
        to = draft.target_status or ""
        if to not in TRANSITIONS.get(t.status, set()):
            raise ToolConflict(f"transition {t.status} -> {to} not allowed")
        frm, t.status = t.status, to
    s.add(
        TicketEvent(
            ticket_id=t.id,
            draft_id=draft.id,
            approval_id=aid,
            from_status=frm,
            to_status=to,
            actor_id=user.id,
            approver_id=reviewer.id,
            approved_at=appr.decided_at,
            note=f"executed via MCP ticket.update_ticket_status (draft r{draft.revision})",
        )
    )
    draft.status = "executed"
    try:
        s.commit()
    except IntegrityError as exc:  # a concurrent executor won: report its result
        s.rollback()
        prev = s.scalar(select(TicketEvent).where(TicketEvent.approval_id == aid))
        if prev is None:
            raise ToolConflict("concurrent execution conflict") from exc
        t2 = s.get(Ticket, prev.ticket_id)
        return TicketChangeOut(
            ticket_number=t2.number if t2 else "?",
            status=prev.to_status,
            from_status=prev.from_status,
            draft_status="executed",
            deduplicated=True,
        )
    return TicketChangeOut(
        ticket_number=t.number,
        status=to,
        from_status=frm,
        draft_status="executed",
        deduplicated=False,
    )


def revise_ticket_draft(s: Session, user: User, a: Any, ctx: ToolContext) -> TicketDraftOut:
    if not isinstance(a, ReviseTicketDraftIn):
        raise TypeError("bad tool input")
    parent = s.get(TicketDraft, uuid.UUID(a.parent_draft_id))
    if parent is None or (user.role != "admin" and parent.created_by != user.id):
        raise ToolNotFound("draft not found")
    if parent.status != "returned":
        raise ToolConflict(f"draft is {parent.status}, not returned for revision")
    rev = parent.revision + 1
    if rev > 1 + ctx.settings.mcp_max_revisions:
        raise ToolConflict("revision limit reached")
    payload = {
        "title": a.title,
        "description": a.description,
        "priority": a.priority,
        "service": parent.service,
        "category": parent.category,
        "revision": rev,
    }
    sha = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    key = idempotency_key(str(parent.id), "revise_ticket_draft", {"revision": rev})
    existing = s.scalar(select(TicketDraft).where(TicketDraft.idempotency_key == key))
    if existing is None:
        d = TicketDraft(
            run_id=parent.run_id,
            action=parent.action,
            target_ticket_id=parent.target_ticket_id,
            target_status=parent.target_status,
            title=a.title,
            description=a.description,
            category=parent.category,
            priority=a.priority,
            service=parent.service,
            evidence=parent.evidence,
            payload_sha256=sha,
            status="pending_approval",
            created_by=parent.created_by,
            idempotency_key=key,
            revision=rev,
            parent_draft_id=parent.id,
            thread_id=parent.thread_id,
        )
        s.add(d)
        try:
            s.commit()
            existing = d
        except IntegrityError:
            s.rollback()
            existing = s.scalar(select(TicketDraft).where(TicketDraft.idempotency_key == key))
    if existing is None:
        raise ToolConflict("revision vanished")
    return TicketDraftOut(
        draft_id=str(existing.id),
        status=existing.status,
        idempotency_key=key,
        payload_sha256=existing.payload_sha256,
    )


# ---------------------------------------------------------------- diagnostics
def get_service_status(s: Session, user: User, a: Any, ctx: ToolContext) -> ServiceStatusOut:
    if not isinstance(a, GetServiceStatusIn):
        raise TypeError("bad tool input")
    if not _can_read_service(s, user, a.service):
        raise ToolPermissionError(f"no permission to read status of {a.service}")
    rows = [
        json.loads(x)
        for x in (DATA / "mock_logs.jsonl").read_text(encoding="utf-8").splitlines()
        if x
    ]
    mine = [r for r in rows if r["service"] == a.service]
    errs = [r for r in mine if r["level"] == "ERROR"]
    codes: dict[str, int] = {}
    for r in errs:
        if r["code"]:
            codes[r["code"]] = codes.get(r["code"], 0) + 1
    status = "down" if len(errs) >= 5 else "degraded" if errs else "healthy"
    owners = json.loads((DATA / "services.json").read_text("utf-8")).get("owners", {})
    return ServiceStatusOut(
        service=a.service,
        status=status,
        error_rate_5m=round(len(errs) / len(mine), 3) if mine else 0.0,
        recent_errors=len(errs),
        top_codes=dict(sorted(codes.items(), key=lambda kv: -kv[1])[:5]),
        owner=owners.get(a.service),
    )
