"""Stage 3C: durable PostgresSaver - multi-turn, interrupt/approval, restart, crash-resume,
idempotent side effects, TTL expiry, checkpoint history. Real Postgres (box test DB)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import func, select, update

from opspilot.agent import checkpoint
from opspilot.agent.llm import MockAgentLLM
from opspilot.agent.service import AgentService
from opspilot.core.errors import ConflictError, ForbiddenError, NotFoundError
from opspilot.db.models import AgentRun, AgentStep, Approval, TicketDraft, ToolCall, User
from opspilot.llm.mock import MockEmbedding


@pytest.fixture(scope="module")
def ready(agent_db):
    checkpoint.setup(agent_db["settings"])
    return agent_db


@contextmanager
def process(ready, session_factory, llm=None):
    """One 'backend process': its own connection pool + saver + compiled graph."""
    pool = checkpoint.open_pool(ready["settings"], max_size=2)
    try:
        yield AgentService(
            ready["settings"],
            session_factory,
            MockEmbedding(),
            PostgresSaver(pool),
            llm=llm or MockAgentLLM(),
        )
    finally:
        pool.close()


def user(sf, name: str) -> User:
    with sf() as s:
        return s.scalar(select(User).where(User.email == f"{name}@opspilot.local"))


def test_checkpoint_tables_live_in_langgraph_schema(ready, session_factory) -> None:
    from sqlalchemy import text

    with session_factory() as s:
        tables = set(
            s.scalars(
                text(
                    "select table_name from information_schema.tables where table_schema='langgraph'"
                )
            )
        )
        public = set(
            s.scalars(
                text("select table_name from information_schema.tables where table_schema='public'")
            )
        )
    assert {"checkpoints", "checkpoint_writes", "checkpoint_blobs"} <= tables
    assert not ({"checkpoints", "checkpoint_writes"} & public)
    assert os.environ.get("LANGGRAPH_STRICT_MSGPACK") == "true"


def test_multi_turn_follow_up_uses_thread_memory(ready, session_factory) -> None:
    alice = user(session_factory, "alice")
    with process(ready, session_factory) as svc:
        a = svc.start(alice, "查一下工单 TCK-0001 的状态")
        b = svc.start(alice, "它现在是什么优先级", thread_id=a["thread_id"])
        st = svc.graph.get_state(svc._cfg(a["thread_id"])).values
    assert (a["turn"], b["turn"]) == (1, 2) and b["status"] == "completed"
    assert [t["tool"] for t in b["tools"]] == ["get_ticket"] and "P2" in b["answer"]
    humans = [m for m in st["messages"] if m.type == "human"]
    assert len(humans) == 2  # conversation persisted across turns


def test_thread_is_private_to_its_owner(ready, session_factory) -> None:
    alice, bob = user(session_factory, "alice"), user(session_factory, "bob")
    with process(ready, session_factory) as svc:
        a = svc.start(alice, "你好")
        with pytest.raises(NotFoundError):
            svc.start(bob, "继续", thread_id=a["thread_id"])
        with pytest.raises(NotFoundError):
            svc.view(__import__("uuid").UUID(a["run_id"]), bob)


def test_approval_survives_restart_and_never_reruns_side_effect(ready, session_factory) -> None:
    import uuid

    alice, admin = user(session_factory, "alice"), user(session_factory, "admin")
    with process(ready, session_factory) as svc:  # process 1: run until interrupt
        r = svc.start(alice, "支付网关夜间超时，帮我创建一个工单，P1")
        assert r["status"] == "awaiting_approval" and r["pending_approval"]["draft_id"]
        assert r["draft"]["status"] == "pending_approval"
    rid = uuid.UUID(r["run_id"])
    with process(ready, session_factory) as svc2:  # process 2: fresh pool/graph
        with pytest.raises(ForbiddenError):
            svc2.approve(rid, alice, "approved")
        done = svc2.approve(rid, admin, "approved", "ok")
        hist = svc2.history(rid, admin)
        with pytest.raises(ConflictError):
            svc2.approve(rid, admin, "approved")  # second approval refused
    assert done["status"] == "completed" and done["draft"]["status"] == "executed"
    nodes = [n["node"] for n in done["node_trace"]]
    assert nodes[-3:] == ["approval", "apply_approval", "respond"]
    with session_factory() as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(ToolCall)
                .where(ToolCall.run_id == rid, ToolCall.tool_name == "create_ticket_draft")
            )
            == 1
        )
        did = uuid.UUID(r["draft"]["draft_id"])
        assert (
            s.scalar(select(func.count()).select_from(Approval).where(Approval.draft_id == did))
            == 1
        )
        assert s.scalar(
            select(func.count()).select_from(AgentStep).where(AgentStep.run_id == rid)
        ) == len(nodes)
    assert len(hist) >= 8 and any(h["next"] == ["approval"] for h in hist)


def test_reject_and_expiry(ready, session_factory) -> None:
    import uuid

    alice, admin = user(session_factory, "alice"), user(session_factory, "admin")
    with process(ready, session_factory) as svc:
        r1 = svc.start(alice, "库存同步延迟，帮我提一个工单")
        rej = svc.approve(uuid.UUID(r1["run_id"]), admin, "rejected", "duplicate")
        assert rej["draft"]["status"] == "rejected" and rej["status"] == "completed"
        r2 = svc.start(alice, "订单回调失败，帮我新建一个工单")
        with session_factory() as s:
            s.execute(
                update(AgentRun)
                .where(AgentRun.id == uuid.UUID(r2["run_id"]))
                .values(created_at=datetime.now(UTC) - timedelta(hours=48))
            )
            s.commit()
        assert svc.expire_stale() == 1
        v = svc.view(uuid.UUID(r2["run_id"]), alice)
    assert v["status"] == "expired" and v["draft"]["status"] == "expired"
    assert v["failure_reason"] is None or "expired" in (v["failure_reason"] or "")


class Crash(BaseException):
    """Simulates the process dying (not an Exception: nothing in the graph catches it)."""


class CrashingLLM(MockAgentLLM):
    crashed = False

    def generate(self, question, evidence):  # type: ignore[no-untyped-def]
        if not CrashingLLM.crashed:
            CrashingLLM.crashed = True
            raise Crash
        return super().generate(question, evidence)


def test_crash_mid_run_then_resume_after_restart(ready, session_factory) -> None:
    import uuid

    alice = user(session_factory, "alice")
    with process(ready, session_factory, llm=CrashingLLM()) as svc, pytest.raises(Crash):
        svc.start(alice, "查一下工单 TCK-0001 的状态")
    with session_factory() as s:
        run = s.scalars(
            select(AgentRun)
            .where(AgentRun.user_id == alice.id)
            .order_by(AgentRun.created_at.desc())
        ).first()
        rid = run.id
        assert run.status == "running"
        calls_before = s.scalar(
            select(func.count()).select_from(ToolCall).where(ToolCall.run_id == rid)
        )
    assert calls_before == 1
    with process(ready, session_factory) as svc2:  # restart
        v = svc2.resume(rid, alice)
        with pytest.raises(ConflictError):
            svc2.resume(rid, alice)  # nothing left to resume
    assert v["status"] == "completed" and "TCK-0001" in v["answer"]
    with session_factory() as s:  # get_ticket was NOT executed again
        assert (
            s.scalar(select(func.count()).select_from(ToolCall).where(ToolCall.run_id == rid)) == 1
        )
    assert uuid.UUID(v["run_id"]) == rid


def test_new_turn_blocked_while_awaiting_approval(ready, session_factory) -> None:
    alice = user(session_factory, "alice")
    with process(ready, session_factory) as svc:
        r = svc.start(alice, "登录服务报错 AUTH-4011，帮我开一个工单")
        assert r["status"] == "awaiting_approval"
        with pytest.raises(ConflictError):
            svc.start(alice, "再问一个问题", thread_id=r["thread_id"])
    with session_factory() as s:
        d = s.scalar(select(TicketDraft).where(TicketDraft.status == "pending_approval"))
        assert d is not None
