"""Stage 5 platform API: dashboard, ticket workflow, drafts, admin (users/settings/audit),
KB create + permissions, eval-result browser. Every number comes from Postgres rows or
committed eval result files - nothing is invented; missing data is returned as null."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from opspilot import STAGE, __version__
from opspilot.api.deps import CurrentUser, DbDep, require_role
from opspilot.core.errors import ConflictError, ForbiddenError, NotFoundError
from opspilot.core.security import hash_password
from opspilot.db.models import (
    AdminAudit,
    AgentRun,
    Approval,
    Chunk,
    Document,
    KbPermission,
    KnowledgeBase,
    McpToolAudit,
    Ticket,
    TicketDraft,
    TicketEvent,
    ToolCall,
    User,
)

router = APIRouter(prefix="/api/v1", tags=["platform"])
AdminDep = Annotated[User, Depends(require_role("admin"))]
StaffDep = Annotated[User, Depends(require_role("admin", "support"))]
ROLES = ("admin", "support", "user")
ROLE_LABELS = {"admin": "系统管理员", "support": "技术支持工程师", "user": "普通业务用户"}
_TERMINAL = ("completed", "refused", "failed", "expired", "aborted")


def _audit(db: Any, actor: User, action: str, ttype: str, tid: Any, **detail: Any) -> None:
    db.add(
        AdminAudit(
            actor_id=actor.id,
            action=action,
            target_type=ttype,
            target_id=str(tid)[:64],
            detail=detail,
        )
    )


def _ratio(num: int, den: int) -> dict[str, Any]:
    return {"value": round(num / den, 4) if den else None, "numerator": num, "denominator": den}


# ============================================================== dashboard
@router.get("/dashboard/summary")
def dashboard(user: CurrentUser, db: DbDep) -> dict[str, Any]:
    staff = user.role in ("admin", "support")
    scope_all_runs = user.role == "admin"
    tq = select(Ticket.status, func.count()).group_by(Ticket.status)
    if not staff:
        tq = tq.where(or_(Ticket.reporter_id == user.id, Ticket.assignee_id == user.id))
    by_status = {s: int(n) for s, n in db.execute(tq)}
    dq = (
        select(func.count())
        .select_from(TicketDraft)
        .where(TicketDraft.status == "pending_approval")
    )
    if not staff:
        dq = dq.where(TicketDraft.created_by == user.id)
    pending_drafts = int(db.scalar(dq) or 0)

    rq = select(
        AgentRun.status,
        AgentRun.is_mock,
        func.count(),
        func.coalesce(func.sum(AgentRun.prompt_tokens), 0),
        func.coalesce(func.sum(AgentRun.completion_tokens), 0),
        func.coalesce(func.sum(AgentRun.cost_cny), 0.0),
    ).group_by(AgentRun.status, AgentRun.is_mock)
    if not scope_all_runs:
        rq = rq.where(AgentRun.user_id == user.id)
    runs_by_status: dict[str, int] = {}
    usage = {
        m: {"runs": 0, "tokens_in": 0, "tokens_out": 0, "cost_cny": 0.0} for m in ("mock", "real")
    }
    for st, mock, n, ti, to, c in db.execute(rq):
        runs_by_status[st] = runs_by_status.get(st, 0) + int(n)
        u = usage["mock" if mock else "real"]
        u["runs"] += int(n)
        u["tokens_in"] += int(ti)
        u["tokens_out"] += int(to)
        u["cost_cny"] = round(u["cost_cny"] + float(c), 6)
    terminal = sum(runs_by_status.get(s, 0) for s in _TERMINAL)

    tc = select(ToolCall.status, func.count()).group_by(ToolCall.status)
    if not scope_all_runs:
        tc = tc.join(AgentRun, AgentRun.id == ToolCall.run_id).where(AgentRun.user_id == user.id)
    tools = {s: int(n) for s, n in db.execute(tc)}
    tools_total = sum(tools.values())

    since = datetime.now(UTC) - timedelta(days=13)
    day = func.date_trunc("day", AgentRun.created_at)
    dq2 = (
        select(day, func.count(), func.sum(case((AgentRun.status == "completed", 1), else_=0)))
        .where(AgentRun.created_at >= since)
        .group_by(day)
        .order_by(day)
    )
    if not scope_all_runs:
        dq2 = dq2.where(AgentRun.user_id == user.id)
    daily = [
        {"day": d.date().isoformat(), "runs": int(n), "completed": int(c or 0)}
        for d, n, c in db.execute(dq2)
    ]

    lq = select(
        func.percentile_cont(0.5).within_group(AgentRun.latency_ms),
        func.percentile_cont(0.95).within_group(AgentRun.latency_ms),
        func.count(),
    ).where(
        AgentRun.status.in_((*_TERMINAL, "awaiting_approval")), AgentRun.latency_ms.is_not(None)
    )
    if not scope_all_runs:
        lq = lq.where(AgentRun.user_id == user.id)
    p50, p95, n_lat = db.execute(lq).one()
    latency = {
        "p50_ms": round(float(p50), 1) if p50 is not None else None,
        "p95_ms": round(float(p95), 1) if p95 is not None else None,
        "n": int(n_lat),
        "source": "agent_runs.latency_ms (backend wall time per run, all turns)",
    }

    rr = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(8)
    if not scope_all_runs:
        rr = rr.where(AgentRun.user_id == user.id)
    recent = [
        {
            "run_id": str(r.id),
            "thread_id": r.thread_id,
            "status": r.status,
            "question": r.input_text[:80],
            "intent": r.intent,
            "latency_ms": r.latency_ms,
            "is_mock": r.is_mock,
            "llm": f"{r.llm_provider}:{r.llm_model}",
            "created_at": r.created_at.isoformat(),
        }
        for r in db.scalars(rr)
    ]
    return {
        "scope": "all" if scope_all_runs else ("all_tickets_own_runs" if staff else "own"),
        "generated_at": datetime.now(UTC).isoformat(),
        "tickets": {
            "total": sum(by_status.values()),
            "pending": by_status.get("open", 0) + by_status.get("in_progress", 0),
            "completed": by_status.get("resolved", 0) + by_status.get("closed", 0),
            "by_status": by_status,
            "pending_drafts": pending_drafts,
            "source": "tickets / ticket_drafts",
        },
        "agent_runs": {
            "total": sum(runs_by_status.values()),
            "by_status": runs_by_status,
            "task_success": _ratio(runs_by_status.get("completed", 0), terminal)
            | {
                "definition": "completed / finished runs ("
                "completed+refused+failed+expired+aborted)",
                "source": "agent_runs",
            },
            "daily": daily,
            "latency": latency,
        },
        "tool_success": _ratio(tools.get("ok", 0), tools_total)
        | {
            "by_status": tools,
            "definition": "tool_calls.status='ok' / all tool_calls rows",
            "source": "tool_calls",
        },
        "usage": usage
        | {
            "note": "mock tokens are character-based estimates; mock cost is always 0",
            "source": "agent_runs.prompt_tokens/completion_tokens/cost_cny",
        },
        "recent_runs": recent,
    }


# ============================================================== tickets
_TRANSITIONS = {
    "open": ("in_progress", "resolved", "closed"),
    "in_progress": ("open", "resolved", "closed"),
    "resolved": ("in_progress", "closed"),
    "closed": ("in_progress",),  # reopen
}


def _ticket_row(t: Ticket) -> dict[str, Any]:
    return {
        "number": t.number,
        "title": t.title,
        "status": t.status,
        "priority": t.priority,
        "category": t.category,
        "service": t.service,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


class TicketPatch(BaseModel):
    status: Literal["open", "in_progress", "resolved", "closed"]
    note: str = Field(min_length=1, max_length=500)
    resolution: str | None = Field(default=None, max_length=2000)


@router.patch("/tickets/{number}")
def transition_ticket(number: str, body: TicketPatch, user: StaffDep, db: DbDep) -> dict:
    """Manual status transition by admin / support, recorded as a ticket_event (audit)."""
    t = db.scalar(select(Ticket).where(Ticket.number == number[:32]).with_for_update())
    if t is None:
        raise NotFoundError("ticket not found")
    if body.status not in _TRANSITIONS[t.status]:
        raise ConflictError(f"transition {t.status} -> {body.status} is not allowed")
    if body.status == "resolved" and not (body.resolution or "").strip():
        raise ConflictError("resolving a ticket needs a resolution")
    db.add(
        TicketEvent(
            ticket_id=t.id,
            from_status=t.status,
            to_status=body.status,
            actor_id=user.id,
            note=f"[manual:{user.role}] {body.note}",
        )
    )
    t.status = body.status
    if body.resolution:
        t.resolution = body.resolution
    db.commit()
    return {**_ticket_row(t), "allowed_next": list(_TRANSITIONS[t.status])}


@router.get("/ticket-transitions")
def ticket_transitions(_: CurrentUser) -> dict[str, list[str]]:
    return {k: list(v) for k, v in _TRANSITIONS.items()}


@router.get("/ticket-drafts")
def list_drafts(
    user: CurrentUser,
    db: DbDep,
    status: Annotated[str | None, Query(max_length=24)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    stmt = select(TicketDraft, Approval).outerjoin(Approval, Approval.draft_id == TicketDraft.id)
    if user.role not in ("admin", "support"):
        stmt = stmt.where(TicketDraft.created_by == user.id)
    if status:
        stmt = stmt.where(TicketDraft.status == status)
    names = {u.id: u.display_name for u in db.scalars(select(User))}
    out = []
    for d, a in db.execute(stmt.order_by(TicketDraft.created_at.desc()).limit(limit)):
        out.append(
            {
                "draft_id": str(d.id),
                "run_id": str(d.run_id) if d.run_id else None,
                "action": d.action,
                "title": d.title,
                "priority": d.priority,
                "service": d.service,
                "status": d.status,
                "revision": d.revision,
                "parent_draft_id": str(d.parent_draft_id) if d.parent_draft_id else None,
                "created_by": names.get(d.created_by),
                "created_at": d.created_at.isoformat(),
                "evidence_count": len(d.evidence or []),
                "approval": None
                if a is None
                else {
                    "decision": a.decision,
                    "reviewer": names.get(a.reviewer_id),
                    "comment": a.comment,
                    "decided_at": a.decided_at.isoformat(),
                },
            }
        )
    return out


# ============================================================== users / admin
def _user_out(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "role_label": ROLE_LABELS.get(u.role, u.role),
        "is_active": u.is_active,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat(),
    }


@router.get("/users/directory")
def user_directory(_: StaffDep, db: DbDep) -> list[dict[str, Any]]:
    """Minimal directory (no hashes) for picking KB grantees / assignees."""
    return [
        {"id": str(u.id), "display_name": u.display_name, "email": u.email, "role": u.role}
        for u in db.scalars(select(User).where(User.is_active).order_by(User.email))
    ]


@router.get("/admin/users")
def admin_users(_: AdminDep, db: DbDep) -> list[dict[str, Any]]:
    return [_user_out(u) for u in db.scalars(select(User).order_by(User.created_at))]


@router.get("/admin/roles")
def admin_roles(_: CurrentUser) -> list[dict[str, Any]]:
    caps = {
        "admin": [
            "全部知识库读写与权限",
            "审批工单草稿",
            "工单状态流转",
            "用户与角色管理",
            "系统设置/健康",
            "查看所有智能体运行",
        ],
        "support": [
            "查看全部工单并流转状态",
            "查看全部工单草稿",
            "创建知识库",
            "按授权读写知识库",
            "使用智能体（仅本人会话）",
        ],
        "user": ["按授权读取知识库", "使用智能体（仅本人会话）", "查看本人工单与草稿"],
    }
    return [{"role": r, "label": ROLE_LABELS[r], "capabilities": caps[r]} for r in ROLES]


_PW = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{10,128}$")


class UserCreate(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    display_name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "support", "user"] = "user"
    password: str = Field(min_length=10, max_length=128)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Literal["admin", "support", "user"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=128)


def _active_admins(db: Any) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.is_active)
        )
        or 0
    )


@router.post("/admin/users", status_code=201)
def create_user(body: UserCreate, admin: AdminDep, db: DbDep) -> dict[str, Any]:
    if not _PW.match(body.password):
        raise ConflictError("password needs >=10 chars with letters and digits")
    u = User(
        email=body.email.strip().lower(),
        display_name=body.display_name.strip(),
        role=body.role,
        password_hash=hash_password(body.password),
        is_active=True,
    )
    db.add(u)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("email already exists") from exc
    _audit(db, admin, "user.create", "user", u.id, email=u.email, role=u.role)
    db.commit()
    return _user_out(u)


@router.patch("/admin/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UserPatch, admin: AdminDep, db: DbDep) -> dict:
    # lock all admin rows so two concurrent demotions cannot both pass the guard
    db.execute(select(User.id).where(User.role == "admin").with_for_update())
    u = db.get(User, user_id)
    if u is None:
        raise NotFoundError("user not found")
    changes: dict[str, Any] = {}
    losing_admin = (
        u.role == "admin"
        and u.is_active
        and ((body.role is not None and body.role != "admin") or body.is_active is False)
    )
    if losing_admin and _active_admins(db) <= 1:
        raise ConflictError("cannot demote or deactivate the last active admin")
    if body.display_name is not None:
        changes["display_name"] = [u.display_name, body.display_name]
        u.display_name = body.display_name.strip()
    if body.role is not None and body.role != u.role:
        changes["role"] = [u.role, body.role]
        u.role = body.role
    if body.is_active is not None and body.is_active != u.is_active:
        changes["is_active"] = [u.is_active, body.is_active]
        u.is_active = body.is_active
    if body.password is not None:
        if not _PW.match(body.password):
            raise ConflictError("password needs >=10 chars with letters and digits")
        u.password_hash = hash_password(body.password)
        changes["password"] = "reset"  # noqa: S105 - marker only, never the value
    _audit(db, admin, "user.update", "user", u.id, **changes)
    db.commit()
    return _user_out(u)


@router.get("/admin/audit")
def admin_audit(_: AdminDep, db: DbDep, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list:
    names = {u.id: u.display_name for u in db.scalars(select(User))}
    rows = db.scalars(select(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(limit))
    return [
        {
            "id": r.id,
            "actor": names.get(r.actor_id),
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "detail": r.detail,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/admin/settings")
def admin_settings(request: Request, _: AdminDep) -> dict[str, Any]:
    """Runtime configuration (read-only). Secrets are reported only as configured: bool."""
    s = request.app.state.settings
    ledger = {"path_configured": bool(s.model_ledger_path), "entries": None, "cost_cny": None}
    p = Path(s.model_ledger_path)
    if p.is_file():
        n, cost = 0, 0.0
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            n += 1
            cost += float(rec.get("cost_cny") or 0)
        ledger.update(entries=n, cost_cny=round(cost, 6))
    health = request.app.state.health_service.readiness()
    return {
        "version": __version__,
        "stage": STAGE,
        "env": s.env,
        "models": {
            "llm_provider": s.llm_provider,
            "llm_model": s.llm_model,
            "agent_llm_provider": s.agent_llm_provider,
            "agent_llm_model": s.agent_llm_model,
            "embedding_provider": s.embedding_provider,
            "embedding_model": s.local_embedding_model
            if s.embedding_provider == "local"
            else s.embedding_model,
            "embedding_dim": s.embedding_dim,
            "reranker": s.reranker,
            "api_key_configured": bool(s.llm_api_key.get_secret_value()),
        },
        "runtime": {
            "agent_tool_transport": s.agent_tool_transport,
            "agent_max_steps": s.agent_max_steps,
            "agent_max_tool_calls": s.agent_max_tool_calls,
            "agent_run_timeout_s": s.agent_run_timeout_s,
            "agent_approval_ttl_hours": s.agent_approval_ttl_hours,
            "mcp_call_timeout_s": s.mcp_call_timeout_s,
            "chunk_size": s.chunk_size,
            "rerank_fusion": s.rerank_fusion,
            "max_upload_mb": s.max_upload_mb,
        },
        "budget": {
            "allow_paid_api": s.allow_paid_api,
            "paid_budget_cny": s.paid_budget_cny,
            "paid_max_calls": s.paid_max_calls,
            "paid_approval_configured": bool(s.paid_approval_id),
            "ledger": ledger,
        },
        "health": health,
    }


# ============================================================== knowledge bases
class KbCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["public", "restricted"] = "restricted"


def _kb_admin(db: Any, user: User, kb_id: uuid.UUID) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError("knowledge base not found")
    if user.role == "admin" or kb.owner_id == user.id:
        return kb
    grant = db.scalar(
        select(KbPermission).where(
            KbPermission.kb_id == kb.id,
            KbPermission.user_id == user.id,
            KbPermission.permission == "admin",
        )
    )
    if grant is None:
        raise ForbiddenError("managing permissions needs KB admin")
    return kb


@router.post("/knowledge-bases", status_code=201)
def create_kb(body: KbCreate, user: StaffDep, db: DbDep) -> dict[str, Any]:
    kb = KnowledgeBase(
        slug=body.slug,
        name=body.name.strip(),
        description=body.description,
        visibility=body.visibility,
        owner_id=user.id,
    )
    db.add(kb)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("slug already exists") from exc
    _audit(db, user, "kb.create", "knowledge_base", kb.id, slug=kb.slug, visibility=kb.visibility)
    db.commit()
    return {
        "id": str(kb.id),
        "slug": kb.slug,
        "name": kb.name,
        "visibility": kb.visibility,
        "description": kb.description,
    }


@router.get("/knowledge-bases/{kb_id}/permissions")
def kb_permissions(kb_id: uuid.UUID, user: CurrentUser, db: DbDep) -> dict[str, Any]:
    kb = _kb_admin(db, user, kb_id)
    rows = db.execute(
        select(KbPermission, User)
        .join(User, User.id == KbPermission.user_id)
        .where(KbPermission.kb_id == kb.id)
        .order_by(User.email)
    )
    owner = db.get(User, kb.owner_id)
    return {
        "kb_id": str(kb.id),
        "slug": kb.slug,
        "visibility": kb.visibility,
        "owner": {"id": str(kb.owner_id), "display_name": owner.display_name if owner else None},
        "grants": [
            {
                "user_id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "permission": p.permission,
            }
            for p, u in rows
        ],
    }


class Grant(BaseModel):
    user_id: uuid.UUID
    permission: Literal["read", "write", "admin"]


class PermissionsPut(BaseModel):
    visibility: Literal["public", "restricted"]
    grants: list[Grant] = Field(max_length=200)


@router.put("/knowledge-bases/{kb_id}/permissions")
def put_kb_permissions(
    kb_id: uuid.UUID, body: PermissionsPut, user: CurrentUser, db: DbDep
) -> dict[str, Any]:
    kb = _kb_admin(db, user, kb_id)
    if len({g.user_id for g in body.grants}) != len(body.grants):
        raise ConflictError("duplicate user in grants")
    known = set(db.scalars(select(User.id).where(User.id.in_([g.user_id for g in body.grants]))))
    if len(known) != len(body.grants):
        raise NotFoundError("unknown user in grants")
    before = {
        str(p.user_id): p.permission
        for p in db.scalars(select(KbPermission).where(KbPermission.kb_id == kb.id))
    }
    for p in db.scalars(select(KbPermission).where(KbPermission.kb_id == kb.id)):
        db.delete(p)
    db.flush()
    for g in body.grants:
        db.add(
            KbPermission(
                kb_id=kb.id, user_id=g.user_id, permission=g.permission, granted_by=user.id
            )
        )
    after = {str(g.user_id): g.permission for g in body.grants}
    _audit(
        db,
        user,
        "kb.permissions",
        "knowledge_base",
        kb.id,
        visibility=[kb.visibility, body.visibility],
        before=before,
        after=after,
    )
    kb.visibility = body.visibility
    db.commit()
    return kb_permissions(kb_id, user, db)


class KbDeleteIn(BaseModel):
    confirm_slug: str = Field(min_length=2, max_length=64)


@router.delete("/knowledge-bases/{kb_id}")
def delete_kb(kb_id: uuid.UUID, body: KbDeleteIn, user: CurrentUser, db: DbDep) -> dict[str, Any]:
    """Delete a knowledge base after the caller types its slug. Owner or system admin only.

    Soft-deletes documents, hard-deletes chunks/embeddings (CASCADE), then removes the KB row
    (permissions CASCADE). Seed corpora can be deleted the same way — restore from backup if needed.
    """
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise NotFoundError("knowledge base not found")
    if body.confirm_slug != kb.slug:
        raise ConflictError("confirm_slug must exactly match the knowledge base slug")
    if user.role != "admin" and kb.owner_id != user.id:
        raise ForbiddenError("only the owner or a system admin can delete a knowledge base")
    # remove vector/index rows first (explicit, so we can report counts)
    n_chunks = db.execute(delete(Chunk).where(Chunk.kb_id == kb.id)).rowcount or 0
    n_docs = 0
    for doc in db.scalars(select(Document).where(Document.kb_id == kb.id)):
        doc.status = "deleted"
        n_docs += 1
    db.flush()
    slug, name = kb.slug, kb.name
    db.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb.id))
    _audit(
        db,
        user,
        "kb.delete",
        "knowledge_base",
        kb_id,
        slug=slug,
        name=name,
        documents=n_docs,
        chunks_removed=n_chunks,
    )
    db.commit()
    return {"status": "deleted", "slug": slug, "documents": n_docs, "chunks_removed": n_chunks}


@router.get("/admin/observability")
def admin_observability(user: AdminDep, db: DbDep) -> dict[str, Any]:
    """Live in-process spans/counters + durable DB summaries for admin UI."""
    from opspilot.core.tracing import snapshot

    snap = snapshot(limit=80)
    # durable last-hour MCP + agent stats
    since = datetime.now(UTC) - timedelta(hours=1)
    runs_1h = (
        db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.created_at >= since))
        or 0
    )
    mcp_1h = (
        db.scalar(
            select(func.count()).select_from(McpToolAudit).where(McpToolAudit.created_at >= since)
        )
        or 0
    )
    mcp_err = (
        db.scalar(
            select(func.count())
            .select_from(McpToolAudit)
            .where(McpToolAudit.created_at >= since, McpToolAudit.status != "ok")
        )
        or 0
    )
    recent_runs = [
        {
            "run_id": str(r.id),
            "thread_id": str(r.thread_id) if r.thread_id else None,
            "status": r.status,
            "latency_ms": r.latency_ms,
            "is_mock": r.is_mock,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in db.scalars(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(20))
    ]
    return {
        **snap,
        "durable": {
            "agent_runs_last_hour": runs_1h,
            "mcp_calls_last_hour": mcp_1h,
            "mcp_errors_last_hour": mcp_err,
            "recent_runs": recent_runs,
        },
        "viewer": {"email": user.email, "role": user.role},
    }


# ============================================================== eval results (files + DB)
def _results_dir(request: Request) -> Path:
    return Path(getattr(request.app.state.settings, "eval_results_dir", "evals/results"))


def _env_of(name: str) -> str:
    for tag in ("wsl", "box"):
        if name.endswith(f"_{tag}"):
            return tag
    return "unknown"


def _normalize(path: Path) -> list[dict[str, Any]]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    stem = path.stem
    if isinstance(d, dict) and "meta" in d and "retrieval" in d:  # RAG eval
        m = d["meta"]
        emb = m.get("embedding_provider") or "unknown"
        is_mock = emb == "mock"
        base = {
            "kind": "rag",
            "file": path.name,
            "generated_at": m.get("started_at"),
            "dataset": m.get("dataset"),
            "split": m.get("dataset_split"),
            "dataset_sha256": (m.get("dataset_sha256") or "")[:12],
            "label_source": m.get("label_source"),
            "version": (m.get("git_commit") or "")[:8],
            "model": m.get("embedding_model"),
            "provider": emb,
            "is_mock": is_mock,
            "environment": _env_of(stem),
            "generator": m.get("llm_generator"),
            "n": m.get("n_cases"),
        }
        qa = d.get("qa") or {}
        out = []
        for mode, r in (d.get("retrieval") or {}).items():
            out.append(
                base
                | {
                    "mode": mode,
                    "metrics": {
                        "recall_at_5": r.get("recall_at_5"),
                        "mrr_at_10": r.get("mrr_at_10"),
                        "ndcg_at_10": r.get("ndcg_at_10"),
                        "latency_p50_ms": r.get("latency_p50_ms"),
                        "latency_p95_ms": r.get("latency_p95_ms"),
                        "failure_rate": r.get("failure_rate"),
                        "acl_leaked_chunks": r.get("acl_leaked_chunks"),
                        "refusal_rate": None,
                        "tokens": None,
                        "cost_cny": None,
                    },
                }
            )
        if qa:
            out.append(
                base
                | {
                    "mode": f"qa:{qa.get('mode')}",
                    "metrics": {
                        "recall_at_5": None,
                        "mrr_at_10": None,
                        "ndcg_at_10": None,
                        "latency_p50_ms": qa.get("latency_p50_ms"),
                        "latency_p95_ms": qa.get("latency_p95_ms"),
                        "failure_rate": None
                        if qa.get("accuracy") is None
                        else round(1 - qa["accuracy"], 4),
                        "refusal_rate": qa.get("no_answer_correct_refusal_rate"),
                        "acl_leaked_chunks": qa.get("citation_acl_leaks"),
                        "tokens": None,
                        "cost_cny": None,
                    },
                }
            )
        return out
    if isinstance(d, dict) and "IS_MOCK" in d and "metrics" in d:  # agent eval
        m = d["metrics"]
        return [
            {
                "kind": "agent",
                "file": path.name,
                "generated_at": d.get("generated_at"),
                "dataset": d.get("dataset"),
                "split": None,
                "dataset_sha256": None,
                "label_source": None,
                "version": None,
                "model": d.get("model"),
                "provider": d.get("provider"),
                "is_mock": bool(d["IS_MOCK"]),
                "environment": _env_of(stem),
                "generator": None,
                "n": m.get("scenarios"),
                "mode": d.get("tool_transport") or "inprocess",
                "disclaimer": d.get("disclaimer"),
                "metrics": {
                    "task_success_rate": m.get("task_success_rate"),
                    "tool_call_success_rate": m.get("tool_call_success_rate"),
                    "correct_refusal_rate": m.get("correct_refusal_rate"),
                    "false_refusal_rate": m.get("false_refusal_rate"),
                    "latency_p50_ms": m.get("latency_ms_p50"),
                    "latency_p95_ms": m.get("latency_ms_p95"),
                    "tokens": (m.get("tokens_in") or 0) + (m.get("tokens_out") or 0)
                    if m.get("tokens_in") is not None
                    else None,
                    "cost_cny": m.get("cost_cny"),
                    "recall_at_5": None,
                    "mrr_at_10": None,
                    "failure_rate": None
                    if m.get("task_success_rate") is None
                    else round(1 - m["task_success_rate"], 4),
                },
            }
        ]
    return []


@router.get("/evals/results")
def eval_results(request: Request, _: CurrentUser) -> dict[str, Any]:
    """Normalized rows from committed eval result files. Each row keeps its own dataset,
    split, provider and mock flag; the API never averages across rows."""
    root = _results_dir(request)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    if root.is_dir():
        for p in sorted(root.glob("*.json")):
            got = _normalize(p)
            (rows.extend(got) if got else skipped.append(p.name))
    return {
        "source": str(root),
        "available": root.is_dir(),
        "rows": rows,
        "skipped_files": skipped,
        "note": "rows with different dataset/split/provider are not comparable; "
        "null = metric not measured by that eval",
    }
