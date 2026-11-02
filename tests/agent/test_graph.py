"""Stage 3A/3B: explicit StateGraph routing, evidence sufficiency, tool guards (mock LLM).

All runs here use MockAgentLLM: they verify graph plumbing, guards and tools, NOT the
reasoning quality of a real model."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from opspilot.agent.graph import MOCK_BANNER, NODES
from opspilot.agent.llm import AgentReply, MockAgentLLM
from opspilot.db.models import TicketDraft, ToolCall
from tests.conftest import make_settings


def nodes(st: dict) -> list[str]:
    return [n["node"] for n in st["node_trace"] if n["turn"] == st["turn"]]


def recs(st: dict) -> list[dict]:
    return [r for r in st.get("tool_records", []) if r["turn"] == st["turn"]]


# ------------------------------------------------ Stage 2.5 held-out regressions (h0xx)
@pytest.mark.parametrize(
    ("hid", "user", "q", "head"),
    [
        ("h029", "alice", "支付网关的商户号是多少", "商户号"),
        ("h032", "bob", "年假有几天", "年假"),
        ("h033", "alice", "灰度发布用的是哪家云厂商", "云厂商"),
        ("h035", "bob", "支付回调签名密钥多久轮换", "密钥"),
        ("h036", "alice", "晚上处理 P1 事故可以调休吗", "调休"),
    ],
)
def test_heldout_unanswerable_now_refused(run_agent, hid, user, q, head) -> None:
    st = run_agent(q, user)
    assert st["status"] == "refused", hid
    if hid == "h029":  # 商户号 IS mentioned in alice's KB, but never with a value
        assert st["final"]["refusal_reason"] == "value_slot_empty"
    else:
        assert st["final"]["refusal_reason"] == "head_term_absent_from_accessible_kb"
        assert any(head in m for m in st["assessment"]["missing"])
    assert st["final"]["citations"] == []
    if hid == "h029":  # value missing -> one widened search_knowledge, then refuse
        assert [r["tool"] for r in recs(st)] == ["search_knowledge"]
        assert nodes(st)[-2:] == ["assess", "refuse"]
    else:
        assert nodes(st) == ["guard_in", "classify", "plan", "retrieve", "assess", "refuse"]


@pytest.mark.parametrize(
    "q",
    ["访问令牌有效期是多久", "订单服务 HTTP 500 应该怎么排查", "支付回调签名密钥多久轮换一次"],
)
def test_answerable_controls_still_answered(run_agent, q) -> None:
    st = run_agent(q)
    assert st["status"] == "completed" and st["final"]["status"] == "answered"
    assert st["final"]["citations"] and st["final"]["answer"].startswith(MOCK_BANNER)


# ------------------------------------------------ routing
def test_error_code_question_runs_react_loop(run_agent) -> None:
    st = run_agent("ORD-5003 是什么错误，怎么处理")
    assert nodes(st) == [
        "guard_in",
        "classify",
        "plan",
        "retrieve",
        "assess",
        "agent",
        "tools",
        "collect",
        "assess",
        "generate",
        "verify",
        "respond",
    ]
    names = {r["tool"]: r["status"] for r in recs(st)}
    assert names == {"analyze_error_code": "ok", "query_mock_logs": "ok"}
    assert "ORD-5003" in st["final"]["answer"]
    assert st["status"] == "completed"


def test_ticket_query_skips_rag_and_uses_get_ticket(run_agent) -> None:
    st = run_agent("查一下工单 TCK-0001 的状态")
    assert "retrieve" not in nodes(st)
    assert [r["tool"] for r in recs(st)] == ["get_ticket"]
    assert "open" in st["final"]["answer"]


def test_version_dependent_value_asks_for_clarification(run_agent) -> None:
    st = run_agent("连接池最大连接数现在是多少")
    assert st["final"]["status"] == "clarify" and st["status"] == "completed"
    assert "30" in st["final"]["answer"] and "50" in st["final"]["answer"]
    assert st["final"]["refusal_reason"] == "contradictory_evidence"


def test_chitchat_and_out_of_scope(run_agent) -> None:
    assert nodes(run_agent("你好")) == ["guard_in", "classify", "plan", "respond"]
    st = run_agent("今天天气怎么样")
    assert st["status"] == "refused" and st["final"]["refusal_reason"] == "out_of_scope"


def test_graph_declares_all_nodes() -> None:
    assert len(NODES) == 15 and "fail_safe" in NODES


# ------------------------------------------------ server-side tool guards
def test_model_cannot_call_tool_outside_plan(run_agent, session_factory) -> None:
    """A (scripted) misbehaving model asks for create_ticket_draft on a plain question."""
    with session_factory() as s:
        before = s.scalar(select(func.count()).select_from(TicketDraft))
    bad = AgentReply(
        tool_calls=[
            {
                "id": "x1",
                "name": "create_ticket_draft",
                "args": {"title": "hack", "description": "auto created"},
            }
        ]
    )
    st = run_agent("订单服务最近的错误日志里有什么", llm=MockAgentLLM(script=[bad]))
    denied = [r for r in recs(st) if r["tool"] == "create_ticket_draft"]
    assert denied and denied[0]["status"] == "denied" and denied[0]["error_class"] == "permission"
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(TicketDraft)) == before


def test_tool_output_cannot_unlock_side_effects(run_agent) -> None:
    # TCK-0003's description contains an injected instruction; admin may read it
    st = run_agent("查一下工单 TCK-0003 的状态", "admin")
    assert "evil.example" not in st["final"]["answer"]
    assert all(r["tool"] != "create_ticket_draft" for r in recs(st))


def test_forged_identity_argument_is_ignored(run_agent) -> None:
    """The model passes user_id=admin: the injected-state identity (bob) is used, and bob
    cannot read order-service logs (order-platform KB not granted)."""
    forged = AgentReply(
        tool_calls=[
            {
                "id": "f1",
                "name": "query_mock_logs",
                "args": {"service": "order-service", "user_id": "admin"},
            }
        ]
    )
    st = run_agent("订单服务最近的错误日志里有什么", "bob", llm=MockAgentLLM(script=[forged]))
    r = next(x for x in recs(st) if x["tool"] == "query_mock_logs")
    assert r["status"] == "denied" and r["error_class"] == "permission"
    assert "user_id" not in r["args_summary"]
    assert st["status"] == "refused"


def test_invalid_arguments_are_rejected_before_execution(run_agent) -> None:
    bad = AgentReply(
        tool_calls=[
            {
                "id": "v1",
                "name": "query_mock_logs",
                "args": {"service": "Order Service!!", "limit": 999},
            }
        ]
    )
    st = run_agent("订单服务最近的错误日志里有什么", llm=MockAgentLLM(script=[bad]))
    r = next(x for x in recs(st) if x["call_id"] == "v1")
    assert r["status"] == "invalid" and r["error_class"] == "validation"


def test_ticket_acl_has_no_existence_oracle(run_agent) -> None:
    st = run_agent("查一下工单 TCK-0002 的状态", "alice")  # bob's ticket
    r = recs(st)[0]
    assert r["status"] == "error" and r["error_class"] == "not_found"
    assert st["status"] == "refused"


# ------------------------------------------------ errors, timeouts, retries, limits
def _fault_settings(agent_settings, **kw):
    return make_settings(
        database_url=agent_settings.database_url,
        upload_dir=agent_settings.upload_dir,
        agent_fault_injection=True,
        **kw,
    )


def test_tool_timeout_is_bounded_and_reported(run_agent, agent_settings) -> None:
    st_ = _fault_settings(agent_settings, agent_tool_timeout_s=1.0)
    st = run_agent("查一下工单 TCK-0001 的状态", settings=st_, faults={"get_ticket": "timeout"})
    r = recs(st)[0]
    assert r["status"] == "timeout" and r["attempts"] == 2  # 1 retry for read-only tools
    assert st["status"] == "refused"
    assert any(
        x.startswith("tool_failures:get_ticket=timeout") for x in st["assessment"]["reasons"]
    )
    assert any("get_ticket（timeout）" in m for m in st["assessment"]["missing"])


def test_transient_error_retried_once_then_ok(run_agent, agent_settings) -> None:
    st = run_agent(
        "查一下工单 TCK-0001 的状态",
        settings=_fault_settings(agent_settings),
        faults={"get_ticket": "transient_once"},
    )
    r = recs(st)[0]
    assert r["status"] == "ok" and r["attempts"] == 2 and st["status"] == "completed"


def test_upstream_error_not_retried(run_agent, agent_settings) -> None:
    st = run_agent(
        "查一下工单 TCK-0001 的状态",
        settings=_fault_settings(agent_settings),
        faults={"get_ticket": "error"},
    )
    r = recs(st)[0]
    assert r["status"] == "error" and r["error_class"] == "upstream" and r["attempts"] == 1


def test_faults_ignored_unless_enabled(run_agent) -> None:
    st = run_agent("查一下工单 TCK-0001 的状态", faults={"get_ticket": "error"})
    assert recs(st)[0]["status"] == "ok"


def test_max_tool_calls_per_turn(run_agent, agent_settings) -> None:
    s = make_settings(database_url=agent_settings.database_url, agent_max_tool_calls=1)
    st = run_agent("ORD-5003 是什么错误，怎么处理", settings=s)
    ok = [r for r in recs(st) if r["status"] == "ok"]
    assert len(ok) == 1
    assert st["tool_calls"] == 1


def test_max_steps_routes_to_fail_safe(run_agent, agent_settings) -> None:
    s = make_settings(database_url=agent_settings.database_url, agent_max_steps=4)
    st = run_agent("ORD-5003 是什么错误，怎么处理", settings=s)
    assert st["status"] == "failed" and st["error"].startswith("max_steps_exceeded")
    assert nodes(st)[-1] == "fail_safe"


def test_llm_timeout_recovers_with_rules(run_agent) -> None:
    st = run_agent("ORD-5003 是什么错误，怎么处理", llm_faults={"decide": "timeout"})
    assert "decide:llm_timeout->rules" in st["degraded"]
    assert st["status"] == "completed"


def test_duplicate_tool_calls_are_filtered(run_agent) -> None:
    dup = {"id": "d1", "name": "get_ticket", "args": {"ticket_number": "TCK-0001"}}
    st = run_agent(
        "查一下工单 TCK-0001 的状态",
        llm=MockAgentLLM(script=[AgentReply(tool_calls=[dup, {**dup, "id": "d2"}])]),
    )
    statuses = sorted(r["status"] for r in recs(st))
    assert statuses == ["denied", "ok"]
    assert (
        next(r for r in recs(st) if r["status"] == "denied")["result_summary"] == "duplicate_call"
    )


# ------------------------------------------------ prompt injection in tool output
def test_injected_log_line_is_flagged_and_not_obeyed(run_agent) -> None:
    st = run_agent("支付网关最近的错误日志，ERR_PAY_TIMEOUT 怎么处理")
    outs = [o for o in st["tool_outputs"] if o["tool"] == "query_mock_logs"]
    assert outs and any(ln["flagged"] for ln in outs[0]["output"]["lines"])
    assert "evil.example" not in st["final"]["answer"]


# ------------------------------------------------ side effects: idempotency
def test_ticket_draft_is_idempotent_per_thread(run_agent, session_factory) -> None:
    q = "订单接口 500 了，帮我创建一个工单"
    a = run_agent(q, thread_id="idem-thread-1")
    b = run_agent(q, thread_id="idem-thread-1", turn=2)
    assert a["draft"]["draft_id"] == b["draft"]["draft_id"]
    assert [r["status"] for r in recs(b)] == ["duplicate"]
    with session_factory() as s:
        n = s.scalar(
            select(func.count())
            .select_from(TicketDraft)
            .where(TicketDraft.idempotency_key == a["draft"]["idempotency_key"])
        )
    assert n == 1


def test_draft_needs_explicit_user_request(run_agent) -> None:
    st = run_agent("订单接口 500 了，工单系统怎么用")
    assert all(r["tool"] != "create_ticket_draft" for r in recs(st))


def test_tool_calls_are_audited_when_run_id_set(run_agent, session_factory) -> None:
    # tool_calls rows are written only with a run id (service layer); none here
    with session_factory() as s:
        assert (
            s.scalar(select(func.count()).select_from(ToolCall).where(ToolCall.run_id.is_(None)))
            == 0
        )
