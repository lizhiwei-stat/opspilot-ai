"""Agent tools: Pydantic I/O, server-side guards, timeout/retry, error classification,
untrusted-output handling and idempotency.

Execution path for EVERY tool call (``ToolGuard.run``), whatever the model asked for:

  1. allowlist   - the tool must be in this turn's plan.allowed_tools (computed by the server;
                   side-effecting tools only when the USER's message explicitly asks)
  2. identity    - the acting user is re-loaded from the DB by the user_id the SERVER put in
                   the state; any user/role argument the model invents is not part of the
                   schema and is dropped
  3. validation  - Pydantic input model (bounds, patterns)
  4. permission  - per-tool ACL (KB ACL for search/error codes, service->KB ACL for logs,
                   reporter/assignee/admin for tickets)
  5. idempotency - side-effecting tools derive a key from (thread, tool, canonical args);
                   a repeat returns the stored result and status "duplicate"
  6. timeout     - each call runs in a worker with a deadline; read-only tools retry
                   transient failures at most ``agent_tool_retries`` times; writes never retry
  7. output      - Pydantic output model, sanitized (injection lines stripped + flagged), and
                   handed to the model inside <untrusted_tool_output>; never interpreted
  8. trace       - one ``tool_calls`` row per call (args/result summaries, status, class,
                   attempts, duration)
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import structlog
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from opspilot.core.config import Settings
from opspilot.db.models import KnowledgeBase, Ticket, TicketDraft, ToolCall, User
from opspilot.rag.sanitize import sanitize
from opspilot.repositories.knowledge import KnowledgeRepository

log = structlog.get_logger(__name__)

DATA = Path(__file__).parent / "data"
_POOL = cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent-tool")


# ------------------------------------------------------------------ errors
class ToolError(Exception):
    error_class = "internal"


class ToolPermissionError(ToolError):
    error_class = "permission"


class ToolTransientError(ToolError):
    error_class = "transient"


class ToolUpstreamError(ToolError):
    error_class = "upstream"


class ToolNotFound(ToolError):
    error_class = "not_found"


def classify(exc: BaseException) -> str:
    if isinstance(exc, ToolError):
        return exc.error_class
    if isinstance(exc, ValidationError):
        return "validation"
    if isinstance(exc, cf.TimeoutError | TimeoutError):
        return "timeout"
    if isinstance(exc, OperationalError):
        return "transient"
    return "internal"


# ------------------------------------------------------------------ I/O models
class SearchKnowledgeIn(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="检索问题或关键词")
    top_k: int = Field(default=5, ge=1, le=10)


class KnowledgeHit(BaseModel):
    chunk_id: str
    document: str
    locator: str
    text: str
    score: float
    flagged: bool


class SearchKnowledgeOut(BaseModel):
    hits: list[KnowledgeHit]
    degraded: str | None = None


class QueryLogsIn(BaseModel):
    service: str = Field(pattern=r"^[a-z][a-z0-9-]{2,40}$", description="服务名，如 order-service")
    level: str | None = Field(default=None, pattern=r"^(ERROR|WARN|INFO)$")
    error_code: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=20, ge=1, le=50)


class LogLine(BaseModel):
    ts: str
    service: str
    level: str
    code: str | None
    trace_id: str
    message: str
    flagged: bool = False


class QueryLogsOut(BaseModel):
    service: str
    total: int
    by_code: dict[str, int]
    lines: list[LogLine]


class GetTicketIn(BaseModel):
    ticket_number: str = Field(pattern=r"^TCK-\d{4}$")


class TicketOut(BaseModel):
    number: str
    title: str
    status: str
    priority: str
    service: str | None
    category: str
    description: str


class AnalyzeErrorCodeIn(BaseModel):
    code: str = Field(pattern=r"^(?:[A-Z]{2,6}-\d{3,5}|ERR_[A-Z_]{3,40})$")


class ErrorCodeOut(BaseModel):
    code: str
    service: str | None
    http_class: str | None  # 4xx / 5xx from the numeric part, when the format encodes it
    retryable: bool | None  # documented rule: only 5xx and 429 may be retried
    kb_rows: list[KnowledgeHit]  # rows found in KBs the user may read (ACL applies)
    known: bool


class CreateTicketDraftIn(BaseModel):
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(min_length=4, max_length=2000)
    priority: str = Field(default="P3", pattern=r"^P[1-4]$")
    service: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{2,40}$")
    category: str = Field(default="incident", pattern=r"^(incident|request|question)$")


class TicketDraftOut(BaseModel):
    draft_id: str
    status: str
    idempotency_key: str
    payload_sha256: str


# ------------------------------------------------------------------ context
@dataclass
class ToolContext:
    settings: Settings
    session_factory: Callable[[], Session]
    embedder: Any
    run_id: str | None
    thread_id: str
    user_id: str
    allowed: list[str]
    faults: dict[str, str] = field(default_factory=dict)
    attempts_seen: dict[str, int] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Session, User, BaseModel, ToolContext], BaseModel]
    side_effect: bool = False


def _load_user(s: Session, user_id: str) -> User:
    u = s.get(User, uuid.UUID(user_id))
    if u is None or not u.is_active:
        raise ToolPermissionError("acting user not found or disabled")
    return u


def _services() -> dict[str, Any]:
    return json.loads((DATA / "services.json").read_text(encoding="utf-8"))


def _can_read_service(s: Session, user: User, service: str) -> bool:
    kb_slug = _services()["service_kb"].get(service)
    if kb_slug is None:
        raise ToolNotFound(f"unknown service {service}")
    if user.role == "admin":
        return True
    kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.slug == kb_slug))
    return bool(kb and KnowledgeRepository(s).can_read(user, kb.id))


# ------------------------------------------------------------------ implementations
def _search(s: Session, user: User, a: BaseModel, ctx: ToolContext) -> BaseModel:
    from opspilot.services.retrieval import RetrievalService

    if not isinstance(a, SearchKnowledgeIn):
        raise TypeError("bad tool input")
    res = RetrievalService(s, ctx.settings, ctx.embedder).search(
        user, a.query, mode="hybrid_rerank", top_k=a.top_k
    )
    hits = []
    for c in res.candidates:
        clean = sanitize(c.content)
        hits.append(
            KnowledgeHit(
                chunk_id=str(c.chunk_id),
                document=c.document_name,
                locator=c.locator,
                text=clean.text[:800],
                score=round(float(c.score), 4),
                flagged=clean.flagged,
            )
        )
    return SearchKnowledgeOut(hits=hits, degraded=res.degraded)


def _logs(s: Session, user: User, a: BaseModel, ctx: ToolContext) -> BaseModel:
    if not isinstance(a, QueryLogsIn):
        raise TypeError("bad tool input")
    if not _can_read_service(s, user, a.service):
        raise ToolPermissionError(f"no permission to read logs of {a.service}")
    rows = [
        json.loads(x)
        for x in (DATA / "mock_logs.jsonl").read_text(encoding="utf-8").splitlines()
        if x
    ]
    rows = [
        r
        for r in rows
        if r["service"] == a.service
        and (a.level is None or r["level"] == a.level)
        and (a.error_code is None or r["code"] == a.error_code)
    ]
    by_code: dict[str, int] = {}
    lines = []
    for r in rows[-a.limit :]:
        clean = sanitize(r["message"])
        if r["code"]:
            by_code[r["code"]] = by_code.get(r["code"], 0) + 1
        lines.append(
            LogLine(
                **{**r, "message": clean.text or "[已移除可疑指令文本]", "flagged": clean.flagged}
            )
        )
    return QueryLogsOut(service=a.service, total=len(rows), by_code=by_code, lines=lines)


def _ticket(s: Session, user: User, a: BaseModel, ctx: ToolContext) -> BaseModel:
    if not isinstance(a, GetTicketIn):
        raise TypeError("bad tool input")
    t = s.scalar(select(Ticket).where(Ticket.number == a.ticket_number))
    # same answer for "absent" and "not yours": no existence oracle
    if t is None or not (user.role == "admin" or user.id in (t.reporter_id, t.assignee_id)):
        raise ToolNotFound(f"ticket {a.ticket_number} not found")
    return TicketOut(
        number=t.number,
        title=t.title,
        status=t.status,
        priority=t.priority,
        service=t.service,
        category=t.category,
        description=sanitize(t.description).text[:1000],
    )


def _error_code(s: Session, user: User, a: BaseModel, ctx: ToolContext) -> BaseModel:
    from opspilot.rag.tokenize import query_tokens

    if not isinstance(a, AnalyzeErrorCodeIn):
        raise TypeError("bad tool input")
    code = a.code
    svc_map = _services()["prefix_service"]
    service = next((v for k, v in svc_map.items() if code.startswith(k)), None)
    m = re.search(r"-(\d)(\d{2,4})$", code)
    http_class = f"{m.group(1)}xx" if m and m.group(1) in "45" else None
    retryable = None if http_class is None else (http_class == "5xx" or code.endswith("4290"))
    repo = KnowledgeRepository(s)
    hits = repo.keyword_search(user, query_tokens(code), limit=5)  # ACL-scoped
    loaded = repo.load_chunks([h.chunk_id for h in hits])
    rows: list[KnowledgeHit] = []
    for h in hits:
        chunk, doc = loaded[h.chunk_id][0], loaded[h.chunk_id][1]
        for line in chunk.content.splitlines():
            if code in line:
                clean = sanitize(line)
                rows.append(
                    KnowledgeHit(
                        chunk_id=str(chunk.id),
                        document=doc.source_name,
                        locator=doc.source_name,
                        text=clean.text[:400],
                        score=round(h.score, 4),
                        flagged=clean.flagged,
                    )
                )
    return ErrorCodeOut(
        code=code,
        service=service,
        http_class=http_class,
        retryable=retryable,
        kb_rows=rows[:3],
        known=bool(rows),
    )


def _draft(s: Session, user: User, a: BaseModel, ctx: ToolContext) -> BaseModel:
    if not isinstance(a, CreateTicketDraftIn):
        raise TypeError("bad tool input")
    payload = a.model_dump()
    sha = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    key = idempotency_key(ctx.thread_id, "create_ticket_draft", payload)
    existing = s.scalar(select(TicketDraft).where(TicketDraft.idempotency_key == key))
    if existing is None:
        d = TicketDraft(
            run_id=uuid.UUID(ctx.run_id) if ctx.run_id else None,
            action="create",
            title=a.title,
            description=a.description,
            category=a.category,
            priority=a.priority,
            service=a.service,
            evidence=[],
            payload_sha256=sha,
            status="pending_approval",
            created_by=user.id,
            idempotency_key=key,
        )
        s.add(d)
        try:
            s.commit()
        except IntegrityError:  # concurrent duplicate: the unique key wins
            s.rollback()
            existing = s.scalar(select(TicketDraft).where(TicketDraft.idempotency_key == key))
        else:
            existing = d
    if existing is None:
        raise RuntimeError("idempotent draft vanished")
    return TicketDraftOut(
        draft_id=str(existing.id),
        status=existing.status,
        idempotency_key=key,
        payload_sha256=existing.payload_sha256,
    )


def idempotency_key(thread_id: str, tool: str, args: dict[str, Any]) -> str:
    canon = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{thread_id}|{tool}|{canon}".encode()).hexdigest()[:40]


SPECS: dict[str, ToolSpec] = {
    s.name: s
    for s in [
        ToolSpec(
            "search_knowledge",
            "在当前用户有权限的知识库中检索（第二阶段 RAG，混合检索+重排）。",
            SearchKnowledgeIn,
            _search,
        ),
        ToolSpec(
            "query_mock_logs", "查询模拟系统日志（按服务、级别、错误码过滤）。", QueryLogsIn, _logs
        ),
        ToolSpec(
            "get_ticket",
            "按工单号查询已有工单（仅本人提交/负责或管理员可见）。",
            GetTicketIn,
            _ticket,
        ),
        ToolSpec(
            "analyze_error_code",
            "分析错误码：所属服务、4xx/5xx、是否可重试，并在可访问知识库中查找说明。",
            AnalyzeErrorCodeIn,
            _error_code,
        ),
        ToolSpec(
            "create_ticket_draft",
            "生成待审批的工单草稿（不会直接创建工单，需人工审批）。",
            CreateTicketDraftIn,
            _draft,
            side_effect=True,
        ),
    ]
}


def openai_tool_schemas(names: list[str]) -> list[dict[str, Any]]:
    out = []
    for n in names:
        spec = SPECS[n]
        out.append(
            {
                "type": "function",
                "function": {
                    "name": n,
                    "description": spec.description,
                    "parameters": spec.input_model.model_json_schema(),
                },
            }
        )
    return out


# ------------------------------------------------------------------ guard
def _summary(d: dict[str, Any], limit: int = 160) -> dict[str, Any]:
    return {
        k: (v[:limit] + "…" if isinstance(v, str) and len(v) > limit else v) for k, v in d.items()
    }


def _result_summary(out: BaseModel) -> str:
    if isinstance(out, SearchKnowledgeOut):
        return f"{len(out.hits)} hits: " + ", ".join(h.document for h in out.hits[:3])
    if isinstance(out, QueryLogsOut):
        return f"{out.total} log lines for {out.service}; by_code={out.by_code}"
    if isinstance(out, TicketOut):
        return f"{out.number} [{out.status}/{out.priority}] {out.title}"
    if isinstance(out, ErrorCodeOut):
        return (
            f"{out.code}: service={out.service} class={out.http_class} "
            f"retryable={out.retryable} kb_rows={len(out.kb_rows)}"
        )
    if isinstance(out, TicketDraftOut):
        return f"draft {out.draft_id} status={out.status}"
    return out.__class__.__name__


@dataclass
class ToolOutcome:
    status: str
    content: str  # what the model sees (untrusted wrapper)
    output: dict[str, Any] | None
    record: dict[str, Any]


class ToolGuard:
    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def run(self, name: str, raw_args: dict[str, Any], call_id: str) -> ToolOutcome:
        t0 = time.perf_counter()
        ctx = self.ctx
        spec = SPECS.get(name)
        attempts = 0
        key = None
        args_summary = _summary({k: v for k, v in raw_args.items()})

        def done(
            status: str,
            *,
            err: BaseException | None = None,
            out: BaseModel | None = None,
            error_class: str | None = None,
        ) -> ToolOutcome:
            ec = error_class or (classify(err) if err else None)
            rec = {
                "call_id": call_id,
                "tool": name,
                "args_summary": args_summary,
                "status": status,
                "error_class": ec,
                "attempts": attempts,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                "result_summary": _result_summary(out) if out else (f"{ec}: {err}" if err else ""),
                "idempotency_key": key,
            }
            if out is not None:
                body = out.model_dump_json()
                safe = body.replace("<", "‹").replace(">", "›")
                content = f'<untrusted_tool_output tool="{name}">\n{safe}\n</untrusted_tool_output>'
            else:
                msg = str(err)[:200] if err else status
                content = f'<tool_error tool="{name}" class="{ec}">{msg}</tool_error>'
            self._trace(rec, raw_args, out)
            return ToolOutcome(status, content, out.model_dump() if out else None, rec)

        if spec is None:
            return done(
                "invalid", err=ToolNotFound(f"unknown tool {name}"), error_class="validation"
            )
        if name not in ctx.allowed:
            return done(
                "denied",
                err=ToolPermissionError(f"tool {name} not allowed in this turn"),
                error_class="permission",
            )
        try:
            args = spec.input_model.model_validate(raw_args)
        except ValidationError as exc:
            return done(
                "invalid",
                err=ToolError(f"invalid arguments: {exc.error_count()} error(s)"),
                error_class="validation",
            )
        if spec.side_effect:
            key = idempotency_key(ctx.thread_id, name, args.model_dump())
            prev = self._previous(key)
            if prev is not None:
                return done("duplicate", out=TicketDraftOut(**prev))
        retries = 0 if spec.side_effect else ctx.settings.agent_tool_retries
        last: BaseException | None = None
        for _ in range(retries + 1):
            attempts += 1
            fut = _POOL.submit(self._execute, spec, args, attempts)
            try:
                out = fut.result(timeout=ctx.settings.agent_tool_timeout_s)
                return done("ok", out=out)
            except cf.TimeoutError as exc:
                last = exc
                status = "timeout"
                if spec.side_effect:
                    break
            except (ToolTransientError, OperationalError) as exc:
                last = exc
                status = "error"
            except Exception as exc:  # permission / not found / upstream / internal: no retry
                return done("denied" if classify(exc) == "permission" else "error", err=exc)
        if last is None:
            raise RuntimeError("no attempt")
        return done(
            status,
            err=last
            if not isinstance(last, cf.TimeoutError)
            else TimeoutError(f"tool timed out after {ctx.settings.agent_tool_timeout_s}s"),
            error_class=classify(last),
        )

    def _execute(self, spec: ToolSpec, args: BaseModel, attempt: int) -> BaseModel:
        fault = self.ctx.faults.get(spec.name) if self.ctx.settings.agent_fault_injection else None
        if fault == "timeout":
            time.sleep(self.ctx.settings.agent_tool_timeout_s + 0.5)
        elif fault == "error":
            raise ToolUpstreamError("injected upstream failure")
        elif fault == "transient_once" and attempt == 1:
            raise ToolTransientError("injected transient failure")
        with self.ctx.session_factory() as s:
            user = _load_user(s, self.ctx.user_id)
            return spec.fn(s, user, args, self.ctx)

    def _previous(self, key: str) -> dict[str, Any] | None:
        with self.ctx.session_factory() as s:
            d = s.scalar(select(TicketDraft).where(TicketDraft.idempotency_key == key))
            if d is None:
                return None
            return {
                "draft_id": str(d.id),
                "status": d.status,
                "idempotency_key": key,
                "payload_sha256": d.payload_sha256,
            }

    def _trace(self, rec: dict[str, Any], raw_args: dict[str, Any], out: BaseModel | None) -> None:
        if not self.ctx.run_id:
            return
        try:
            with self.ctx.session_factory() as s:
                s.add(
                    ToolCall(
                        run_id=uuid.UUID(self.ctx.run_id),
                        tool_name=rec["tool"][:64],
                        transport="inprocess",
                        arguments=_summary(raw_args),
                        result={
                            "summary": rec["result_summary"],
                            "deduplicated": rec["status"] == "duplicate",
                        }
                        if out is not None
                        else None,
                        status=_db_status(rec["status"]),
                        attempt=max(1, rec["attempts"]),
                        error=rec["error_class"],
                        duration_ms=int(rec["duration_ms"]),
                        idempotency_key=rec["idempotency_key"],
                        call_id=rec["call_id"][:64],
                    )
                )
                s.commit()
        except Exception:  # trace is best-effort; never fail the tool on audit write
            log.warning("tool_trace_failed", tool=rec.get("tool"))


def _db_status(st: str) -> str:
    return {"invalid": "error", "duplicate": "ok"}.get(st, st)


# ------------------------------------------------------------------ LangGraph tools
def make_tools(ctx_factory: Callable[[dict[str, Any]], ToolContext]) -> list[StructuredTool]:
    """StructuredTools for ToolNode. The acting identity comes from InjectedState (server-set
    state), which is hidden from the model's tool schema - a model cannot pass a user id."""
    tools = []
    for spec in SPECS.values():
        fields = {n: (f.annotation, f) for n, f in spec.input_model.model_fields.items()}
        schema = type(
            f"{spec.name}_args",
            (spec.input_model,),
            {
                "__annotations__": {
                    "state": Annotated[dict, InjectedState],
                    "tool_call_id": Annotated[str, InjectedToolCallId],
                }
            },
        )
        del fields

        def fn(state: dict, tool_call_id: str, __name: str = spec.name, **kwargs: Any) -> Command:
            outcome = ToolGuard(ctx_factory(state)).run(__name, kwargs, tool_call_id)
            ev = []
            if outcome.output is not None:
                ev = [
                    {
                        "turn": state.get("turn", 0),
                        "call_id": tool_call_id,
                        "tool": __name,
                        "output": outcome.output,
                    }
                ]
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            outcome.content,
                            tool_call_id=tool_call_id,
                            name=__name,
                            status="success" if outcome.status in ("ok", "duplicate") else "error",
                        )
                    ],
                    "tool_records": [{**outcome.record, "turn": state.get("turn", 0)}],
                    "tool_outputs": ev,
                }
            )

        tools.append(
            StructuredTool.from_function(
                func=fn, name=spec.name, description=spec.description, args_schema=schema
            )
        )
    return tools
