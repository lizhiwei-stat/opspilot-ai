"""AgentService: runs/resumes the graph on a durable PostgresSaver and mirrors each turn into
the business tables (agent_runs / agent_steps / tool_calls) for the API and audit.

thread_id = one conversation (checkpointer thread); each user message is a new turn (row).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opspilot.agent.graph import AgentDeps, build_graph
from opspilot.agent.llm import AgentLLM, build_agent_llm
from opspilot.core.config import Settings
from opspilot.core.errors import ConflictError, ForbiddenError, NotFoundError
from opspilot.core.logging import get_logger
from opspilot.db.models import AgentRun, AgentStep, TicketDraft, ToolCall, User

log = get_logger()
_TERMINAL = ("completed", "refused", "failed", "expired")


class AgentService:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
        embedder: Any,
        saver: Any,
        llm: AgentLLM | None = None,
        faults: dict[str, str] | None = None,
        llm_faults: dict[str, str] | None = None,
    ) -> None:
        self.s = settings
        self.sf = session_factory
        self.llm = llm or build_agent_llm(settings)
        self.graph = build_graph(
            AgentDeps(settings, session_factory, embedder, self.llm, faults, llm_faults),
            checkpointer=saver,
        )

    # ---------------------------------------------------------------- helpers
    def _cfg(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.s.agent_recursion_limit,
        }

    def _load(self, sess: Session, run_id: uuid.UUID, user: User) -> AgentRun:
        run = sess.get(AgentRun, run_id)
        if run is None or (user.role != "admin" and run.user_id != user.id):
            raise NotFoundError("agent run not found")  # no existence oracle
        return run

    def _invoke(self, run_id: uuid.UUID, thread_id: str, payload: Any) -> None:
        t0 = time.perf_counter()
        err = None
        try:
            self.graph.invoke(payload, self._cfg(thread_id))
        except GraphRecursionError:
            err = "recursion_limit"
        except Exception as exc:  # unexpected crash: state stays at the last checkpoint
            err = f"crash:{type(exc).__name__}"
            log.warning("agent_run_crashed", run_id=str(run_id), error=type(exc).__name__)
        self._persist(run_id, thread_id, (time.perf_counter() - t0) * 1000, err)

    def _persist(self, run_id: uuid.UUID, thread_id: str, ms: float, err: str | None) -> None:
        snap = self.graph.get_state(self._cfg(thread_id))
        v = snap.values or {}
        interrupts = [i.value for t in snap.tasks for i in (t.interrupts or [])]
        turn = v.get("turn")
        status = v.get("status") or "failed"
        if interrupts:
            status = "awaiting_approval"
        elif err:
            status = "failed"
        elif snap.next:  # stopped mid-graph without an interrupt
            status = "failed"
            err = err or "incomplete"
        final = v.get("final") or {}
        trace = [n for n in v.get("node_trace", []) if n.get("turn") == turn]
        recs = [r for r in v.get("tool_records", []) if r.get("turn") == turn]
        usage = v.get("usage") or {}
        with self.sf() as sess:
            run = sess.get(AgentRun, run_id)
            if run is None:
                return
            run.status = status
            run.intent = (v.get("classification") or {}).get("intent")
            run.final_answer = final.get("answer")
            run.citations = final.get("citations") or []
            run.node_trace = trace
            run.evidence = [
                {k: e.get(k) for k in ("id", "source", "title", "locator", "flagged", "score")}
                | {"snippet": (e.get("text") or "")[:200]}
                for e in (v.get("evidence") or [])[:8]
            ]
            a = v.get("assessment") or {}
            run.assessment = {
                k: a.get(k)
                for k in ("decision", "reasons", "missing", "contradictions", "need_tools")
            } | {
                "degraded": v.get("degraded") or [],
                "draft": v.get("draft"),
                "draft_result": v.get("draft_result"),
                "interrupt": interrupts[0] if interrupts else None,
                "refusal_reason": final.get("refusal_reason"),
                "final_status": final.get("status"),
                "policy_violations": final.get("policy_violations") or [],
            }
            run.step_count = len(trace)
            run.tool_call_count = len([r for r in recs if r.get("attempts", 0) > 0])
            run.prompt_tokens = int(usage.get("tokens_in", 0))
            run.completion_tokens = int(usage.get("tokens_out", 0))
            run.cost_cny = float(usage.get("cost_cny", 0.0))
            run.latency_ms = int((run.latency_ms or 0) + ms)
            run.error = (
                err
                or v.get("error")
                or (final.get("refusal_reason") if status == "failed" else None)
            )
            run.finished_at = datetime.now(UTC) if status in _TERMINAL else None
            sess.execute(delete(AgentStep).where(AgentStep.run_id == run_id))
            now = datetime.now(UTC)
            for i, n in enumerate(trace, 1):
                sess.add(
                    AgentStep(
                        run_id=run_id,
                        seq=i,
                        node=n["node"][:64],
                        status="ok" if n["status"] == "ok" else "error",
                        input_summary={},
                        output_summary=n.get("detail") or {},
                        started_at=now,
                        duration_ms=int(n.get("ms", 0)),
                    )
                )
            sess.commit()

    # ---------------------------------------------------------------- API
    def start(self, user: User, question: str, thread_id: str | None = None) -> dict[str, Any]:
        thread_id = thread_id or uuid.uuid4().hex
        with self.sf() as sess:
            rows = sess.scalars(select(AgentRun).where(AgentRun.thread_id == thread_id)).all()
            if rows and any(r.user_id != user.id for r in rows):
                raise NotFoundError("thread not found")
            if any(r.status in ("awaiting_approval", "running") for r in rows):
                raise ConflictError("thread has a run awaiting approval or still running")
            turn = max((r.turn for r in rows), default=0) + 1
            run = AgentRun(
                thread_id=thread_id,
                turn=turn,
                user_id=user.id,
                input_text=question[:4000],
                status="running",
                llm_provider=self.llm.name,
                llm_model=self.llm.model,
                is_mock=self.llm.is_mock,
            )
            sess.add(run)
            try:
                sess.commit()
            except IntegrityError as exc:
                raise ConflictError("concurrent turn on this thread") from exc
            run_id = run.id
        self._invoke(
            run_id,
            thread_id,
            {
                "question": question,
                "thread_id": thread_id,
                "user_id": str(user.id),
                "user_role": user.role,
                "run_id": str(run_id),
                "turn": turn,
            },
        )
        return self.view(run_id, user)

    def approve(self, run_id: uuid.UUID, reviewer: User, decision: str, comment: str = "") -> dict:
        if reviewer.role != "admin":
            raise ForbiddenError("approval requires admin")
        if decision not in ("approved", "rejected", "returned"):
            raise ConflictError("decision must be approved, rejected or returned")
        if decision == "returned" and not comment.strip():
            raise ConflictError("return-for-revision needs a comment for the author")
        with self.sf() as sess:
            run = self._load(sess, run_id, reviewer)
            if run.status != "awaiting_approval":
                raise ConflictError(f"run is {run.status}, not awaiting approval")
            thread_id = run.thread_id
            expired = run.created_at < datetime.now(UTC) - timedelta(
                hours=self.s.agent_approval_ttl_hours
            )
        payload = (
            {"decision": "expired"}
            if expired
            else {"decision": decision, "reviewer_id": str(reviewer.id), "comment": comment[:500]}
        )
        self._invoke(run_id, thread_id, Command(resume=payload))
        return self.view(run_id, reviewer)

    def resume(self, run_id: uuid.UUID, user: User) -> dict[str, Any]:
        """Continue a run that stopped mid-graph (process crash/restart) from its last
        checkpoint. Completed nodes - including side-effecting tool calls - are not re-run."""
        with self.sf() as sess:
            run = self._load(sess, run_id, user)
            thread_id, status = run.thread_id, run.status
        snap = self.graph.get_state(self._cfg(thread_id))
        if any(t.interrupts for t in snap.tasks):
            raise ConflictError("run is waiting for approval; use the approval endpoint")
        if not snap.next or (snap.values or {}).get("run_id") != str(run_id):
            raise ConflictError(f"nothing to resume (status={status})")
        self._invoke(run_id, thread_id, None)
        return self.view(run_id, user)

    def expire_stale(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=self.s.agent_approval_ttl_hours)
        with self.sf() as sess:
            stale = sess.scalars(
                select(AgentRun).where(
                    AgentRun.status == "awaiting_approval", AgentRun.created_at < cutoff
                )
            ).all()
            ids = [(r.id, r.thread_id) for r in stale]
        for rid, tid in ids:
            self._invoke(rid, tid, Command(resume={"decision": "expired"}))
        return len(ids)

    def view(self, run_id: uuid.UUID, user: User) -> dict[str, Any]:
        with self.sf() as sess:
            run = self._load(sess, run_id, user)
            tools = sess.scalars(
                select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at)
            ).all()
            a = run.assessment or {}
            draft = None
            if a.get("draft"):
                d = sess.get(TicketDraft, uuid.UUID(a["draft"]["draft_id"]))
                if d is not None:
                    draft = {
                        "draft_id": str(d.id),
                        "status": d.status,
                        "title": d.title,
                        "priority": d.priority,
                        "service": d.service,
                        "description": d.description,
                        "revision": d.revision,
                        "parent_draft_id": str(d.parent_draft_id) if d.parent_draft_id else None,
                    }
            return {
                "run_id": str(run.id),
                "thread_id": run.thread_id,
                "turn": run.turn,
                "status": run.status,
                "question": run.input_text,
                "intent": run.intent,
                "answer": run.final_answer,
                "citations": run.citations,
                "node_trace": run.node_trace,
                "evidence": run.evidence,
                "assessment": {
                    k: a.get(k) for k in ("decision", "reasons", "missing", "contradictions")
                },
                "refusal_reason": a.get("refusal_reason"),
                "final_status": a.get("final_status"),
                "policy_violations": a.get("policy_violations") or [],
                "degraded": a.get("degraded") or [],
                "tools": [
                    {
                        "tool": t.tool_name,
                        "status": t.status,
                        "args": t.arguments,
                        "result": (t.result or {}).get("summary"),
                        "deduplicated": bool((t.result or {}).get("deduplicated")),
                        "error_class": t.error,
                        "attempt": t.attempt,
                        "duration_ms": t.duration_ms,
                        "idempotency_key": t.idempotency_key,
                    }
                    for t in tools
                ],
                "pending_approval": a.get("interrupt"),
                "draft": draft,
                "draft_result": a.get("draft_result"),
                "usage": {
                    "tokens_in": run.prompt_tokens,
                    "tokens_out": run.completion_tokens,
                    "cost_cny": run.cost_cny,
                    "source": "estimate (mock: chars, no API)" if run.is_mock else "api",
                },
                "latency_ms": run.latency_ms,
                "failure_reason": run.error,
                "is_mock": run.is_mock,
                "llm": f"{run.llm_provider}:{run.llm_model}",
                "created_at": run.created_at.isoformat(),
            }

    def list(self, user: User, limit: int = 20) -> list[dict[str, Any]]:
        with self.sf() as sess:
            q = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
            if user.role != "admin":
                q = q.where(AgentRun.user_id == user.id)
            return [
                {
                    "run_id": str(r.id),
                    "thread_id": r.thread_id,
                    "turn": r.turn,
                    "status": r.status,
                    "question": r.input_text[:120],
                    "is_mock": r.is_mock,
                    "created_at": r.created_at.isoformat(),
                }
                for r in sess.scalars(q)
            ]

    def pending_approvals(self, admin: User) -> list[dict[str, Any]]:
        """Approval queue (admin): every run still interrupted at the approval node. Durable:
        after a backend restart the same list comes back from agent_runs + the checkpointer."""
        if admin.role != "admin":
            raise ForbiddenError("approval queue requires admin")
        with self.sf() as sess:
            runs = sess.scalars(
                select(AgentRun)
                .where(AgentRun.status == "awaiting_approval")
                .order_by(AgentRun.created_at)
            ).all()
            ids = [r.id for r in runs]
        return [self.view(i, admin) for i in ids]

    def history(self, run_id: uuid.UUID, user: User) -> list[dict[str, Any]]:
        """Checkpoint history of the run's thread (durable, from Postgres)."""
        with self.sf() as sess:
            run = self._load(sess, run_id, user)
            thread_id = run.thread_id
        out = []
        for snap in self.graph.get_state_history(self._cfg(thread_id)):
            md = snap.metadata or {}
            out.append(
                {
                    "checkpoint_id": snap.config["configurable"].get("checkpoint_id"),
                    "step": md.get("step"),
                    "source": md.get("source"),
                    "turn": (snap.values or {}).get("turn"),
                    "wrote": sorted((md.get("writes") or {}).keys()) if md.get("writes") else [],
                    "next": list(snap.next),
                    "created_at": snap.created_at,
                }
            )
        return out

    def count_runs(self) -> int:
        with self.sf() as sess:
            return int(sess.scalar(select(func.count()).select_from(AgentRun)) or 0)
