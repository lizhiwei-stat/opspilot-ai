"""Stage 3 agent: an explicit LangGraph StateGraph (not one big function).

    guard_in -> classify -> plan -> retrieve -> assess -sufficient-> generate -> verify -> respond
                                                 assess -need_tools-> agent -> tools -> collect
                                                 collect -> assess (ReAct loop, bounded)
                                                 assess -clarify/refuse-> respond / refuse
    ticket_action: plan -> agent -> tools -> collect -> approval (interrupt)
    incident_ticket (Stage 4): plan -> retrieve -> assess -> agent -> tools (MCP: logs, error
        code, service status) -> collect -> assess -> generate -> verify -> draft (server-built,
        Ticket MCP) -> approval (interrupt) -> apply_approval -> [update_ticket_status via
        Ticket MCP | revise -> approval ...] -> respond
    any node --(exception | max steps | deadline)--> fail_safe

Every node returns a `Command(update=..., goto=...)`, so routing is explicit and testable;
guards (allowlist, limits, identity) run in server code the model cannot bypass.
Only system-recorded steps are traced (node, timing, decision summary) - never model
chain-of-thought.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from opspilot.agent import rules
from opspilot.agent.evidence import assess as assess_evidence
from opspilot.agent.llm import AgentLLM, AgentLLMError, MockAgentLLM, Usage
from opspilot.agent.state import AgentState, Evidence, FinalAnswer
from opspilot.agent.tools import DATA, ToolContext, ToolGuard, make_tools, openai_tool_schemas
from opspilot.core.config import Settings
from opspilot.db.models import Approval, TicketDraft, ToolCall, User
from opspilot.llm.mock import MockLLM
from opspilot.rag.policy import check_answer, verify_quote
from opspilot.rag.sanitize import sanitize
from opspilot.repositories.knowledge import KnowledgeRepository
from opspilot.services.qa import QAService

MOCK_BANNER = "【Mock 智能体 · 规则模拟，未调用大模型；仅验证流程与工具，不代表真实模型推理能力】"
REFUSE_PREFIX = "无法给出可靠回答"
NODES = (
    "guard_in",
    "classify",
    "plan",
    "retrieve",
    "assess",
    "agent",
    "tools",
    "collect",
    "generate",
    "verify",
    "approval",
    "apply_approval",
    "respond",
    "refuse",
    "fail_safe",
)
_NUM = re.compile(r"\d+(?:\.\d+)?")
_INDEX = re.compile(r"\[(\d{1,2})\]")


@dataclass
class AgentDeps:
    settings: Settings
    session_factory: Callable[[], Session]
    embedder: Any
    llm: AgentLLM
    faults: dict[str, str] | None = None  # tool fault injection (tests/eval only)
    llm_faults: dict[str, str] | None = None  # e.g. {"decide": "timeout"} (tests/eval only)


# ------------------------------------------------------------------ helpers
def _turn_items(state: AgentState, key: str) -> list[dict[str, Any]]:
    t = state.get("turn", 0)
    return [x for x in state.get(key, []) or [] if x.get("turn") == t]


def _trace(
    state: AgentState, node: str, t0: float, status: str = "ok", detail: dict | None = None
) -> dict:
    return {
        "turn": state.get("turn", 0),
        "node": node,
        "status": status,
        "ms": round((time.perf_counter() - t0) * 1000, 2),
        "detail": detail or {},
    }


def _usage_update(state: AgentState, u: Usage) -> dict[str, Any]:
    cur = state.get("usage") or {}
    return {
        "usage": {
            "tokens_in": int(cur.get("tokens_in", 0)) + u.tokens_in,
            "tokens_out": int(cur.get("tokens_out", 0)) + u.tokens_out,
            "cost_cny": round(float(cur.get("cost_cny", 0.0)) + u.cost_cny, 8),
            "usage_source": u.source if u.calls else cur.get("usage_source", "estimate"),
        },
        "llm_calls": state.get("llm_calls", 0) + u.calls,
    }


def _tools_done(state: AgentState) -> list[str]:
    return [r["tool"] for r in _turn_items(state, "tool_records")]


def _final(status: str, answer: str, **kw: Any) -> dict[str, Any]:
    return FinalAnswer(status=status, answer=answer, **kw).model_dump()  # type: ignore[arg-type]


def _evidence_from_tools(state: AgentState) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in _turn_items(state, "tool_outputs"):
        tool, o, cid = item["tool"], item["output"], item["call_id"]
        if tool == "search_knowledge":
            for h in o.get("hits", []):
                out.append(
                    Evidence(
                        id=h["chunk_id"],
                        source="kb",
                        title=h["document"],
                        locator=h["locator"],
                        text=h["text"],
                        score=h["score"],
                        flagged=h["flagged"],
                    ).model_dump()
                )
        elif tool == "analyze_error_code":
            for h in o.get("kb_rows", []):
                out.append(
                    Evidence(
                        id=h["chunk_id"],
                        source="kb",
                        title=h["document"],
                        locator=h["locator"],
                        text=h["text"],
                        flagged=h["flagged"],
                    ).model_dump()
                )
            txt = (
                f"错误码 {o['code']}：所属服务 {o.get('service') or '未知'}；"
                f"类别 {o.get('http_class') or '未知'}；"
                f"可重试：{ {True: '是', False: '否', None: '未知'}[o.get('retryable')] }。"
            )
            out.append(
                Evidence(id=cid, source="tool", title="analyze_error_code", text=txt).model_dump()
            )
        elif tool == "query_system_logs":
            lines = o.get("lines", [])[-5:]
            txt = (
                f"{o['service']} 最近 {o['total']} 条日志，按错误码统计 {o['by_code']}。"
                + "".join(
                    f"\n{ln['ts']} {ln['level']} {ln.get('code') or ''} {ln['message']}"
                    for ln in lines
                )
            )
            out.append(
                Evidence(
                    id=cid,
                    source="tool",
                    title=f"query_system_logs:{o['service']}",
                    text=txt,
                    flagged=any(ln.get("flagged") for ln in o.get("lines", [])),
                ).model_dump()
            )
        elif tool == "get_ticket":
            txt = (
                f"工单 {o['number']}「{o['title']}」状态 {o['status']}，优先级 {o['priority']}，"
                f"服务 {o.get('service') or '-'}。{o['description']}"
            )
            out.append(
                Evidence(
                    id=cid, source="tool", title=f"get_ticket:{o['number']}", text=txt
                ).model_dump()
            )
    return out


def _dedupe(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for e in evs:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        out.append(e)
    return out


def _history_openai(state: AgentState) -> list[dict[str, Any]]:
    """Previous turns as plain user/assistant text (tool payloads stay out of history)."""
    msgs = []
    for m in state.get("messages", [])[:-1]:
        if isinstance(m, HumanMessage):
            msgs.append({"role": "user", "content": str(m.content)[:500]})
        elif isinstance(m, AIMessage) and not m.tool_calls and m.content:
            msgs.append({"role": "assistant", "content": str(m.content)[:500]})
    return msgs


# ------------------------------------------------------------------ builder
def build_graph(deps: AgentDeps, checkpointer: Any = None) -> Any:
    s = deps.settings
    max_steps = s.agent_max_steps
    llm = deps.llm
    rules_llm = MockAgentLLM()  # deterministic fallback for recovery paths

    def node(name: str, destinations: tuple[str, ...]):
        """Wrap a node: step budget + deadline + exception -> fail_safe (never swallow
        GraphBubbleUp, which carries interrupts)."""

        def deco(fn: Callable[[AgentState], Command]) -> Callable[[AgentState], Command]:
            def run(state: AgentState) -> Command:
                t0 = time.perf_counter()
                steps = state.get("steps", 0) + 1
                if name not in ("guard_in", "fail_safe"):
                    if steps > max_steps:
                        return Command(
                            update={"error": f"max_steps_exceeded:{max_steps}", "steps": steps},
                            goto="fail_safe",
                        )
                    if time.time() > state.get("deadline", float("inf")):
                        return Command(
                            update={"error": "run_deadline_exceeded", "steps": steps},
                            goto="fail_safe",
                        )
                try:
                    cmd = fn(state)
                except GraphBubbleUp:
                    raise
                except Exception as exc:  # recovery: route to the fail-safe terminal node
                    return Command(
                        update={
                            "error": f"{name}:{type(exc).__name__}:{str(exc)[:160]}",
                            "steps": steps,
                            "node_trace": [
                                _trace(state, name, t0, "error", {"error": type(exc).__name__})
                            ],
                        },
                        goto="fail_safe",
                    )
                upd = dict(cmd.update or {})
                upd["steps"] = steps if name != "guard_in" else 1
                detail = upd.pop("_detail", {})
                pre = upd.pop("_pre_trace", [])
                upd["node_trace"] = [
                    *pre,
                    _trace(state, name, t0, upd.pop("_status", "ok"), detail),
                ]
                return Command(update=upd, goto=cmd.goto)

            run.__name__ = name
            run._destinations = destinations  # type: ignore[attr-defined]
            return run

        return deco

    def llm_call(
        state: AgentState, op: str, primary: Callable[[], Any], fallback: Callable[[], Any]
    ):
        """Bounded LLM use with recovery: over the per-turn call limit, on timeout / refusal
        (budget guard) / API error -> deterministic fallback, recorded in `degraded`."""
        degraded = list(state.get("degraded") or [])
        if state.get("llm_calls", 0) >= s.agent_max_llm_calls:
            degraded.append(f"{op}:llm_call_limit->rules")
            return fallback(), degraded
        try:
            fault = (deps.llm_faults or {}).get(op)
            if fault:
                raise AgentLLMError(fault, f"injected {fault}")
            return primary(), degraded
        except AgentLLMError as exc:
            degraded.append(f"{op}:llm_{exc.kind}->rules")
            return fallback(), degraded

    # ------------------------------------------------------------- nodes
    @node("guard_in", ("classify", "refuse"))
    def guard_in(state: AgentState) -> Command:
        q = (state.get("question") or "").strip()
        base = {
            "messages": [HumanMessage(q, id=f"h-{state.get('run_id')}")],
            "llm_calls": 0,
            "tool_calls": 0,
            "react_rounds": 0,
            "error": None,
            "final": None,
            "draft": None,
            "approval": None,
            "draft_result": None,
            "evidence": [],
            "assessment": None,
            "status": "running",
            "agent_idle": False,
            "degraded": [],
            "plan": None,
            "classification": None,
            "usage": {"tokens_in": 0, "tokens_out": 0, "cost_cny": 0.0, "usage_source": "estimate"},
            "deadline": time.time() + s.agent_run_timeout_s,
        }
        if not q or len(q) > 1000:
            return Command(
                update={
                    **base,
                    "assessment": {
                        "decision": "refuse",
                        "reasons": ["invalid_input"],
                        "missing": ["问题为空或超过 1000 字"],
                    },
                },
                goto="refuse",
            )
        return Command(update=base, goto="classify")

    @node("classify", ("plan",))
    def classify(state: AgentState) -> Command:
        q = state["question"]
        (c, u), degraded = llm_call(
            state,
            "classify",
            lambda: llm.classify(q, _history_openai(state)),
            lambda: rules_llm.classify(q, []),
        )
        ent = {k: list(v) for k, v in c.entities.items()}
        # follow-up resolution: "它/这个工单的状态呢" reuses the previous turn's entities
        last = state.get("last_entities") or {}
        if (
            not any(ent.values())
            and re.search(r"它|这个|那个|该|上面|刚才", q)
            and any(last.values())
        ):
            ent = {k: list(v) for k, v in last.items()}
            if ent.get("tickets") and c.intent == "knowledge_qa":
                c.intent = "ticket_query"  # type: ignore[assignment]
        # server-side guard: a side-effecting intent needs the USER's explicit request
        if c.intent in ("ticket_action", "incident_ticket") and not rules.explicit_ticket_request(
            q
        ):
            c.intent = rules.rule_intent(q)  # type: ignore[assignment]
        c.entities = ent
        return Command(
            update={
                "classification": c.model_dump(),
                "last_entities": ent,
                "degraded": degraded,
                **_usage_update(state, u),
                "_detail": {"intent": c.intent, "source": c.source, "entities": ent},
            },
            goto="plan",
        )

    @node("plan", ("retrieve", "agent", "respond", "refuse"))
    def plan(state: AgentState) -> Command:
        c = state["classification"]
        intent, ent = c["intent"], c["entities"]
        steps: list[dict[str, Any]] = []
        allowed: list[str] = []
        if intent == "chitchat":
            return Command(
                update={
                    "plan": {"steps": [], "needs_rag": False, "allowed_tools": []},
                    "final": _final(
                        "answered",
                        "你好，我是 OpsPilot 运维助手，可以帮你查知识库、日志、错误码和工单。",
                    ),
                    "_detail": {"route": "direct"},
                },
                goto="respond",
            )
        if intent == "out_of_scope":
            return Command(
                update={
                    "plan": {"steps": [], "needs_rag": False, "allowed_tools": []},
                    "assessment": {
                        "decision": "refuse",
                        "reasons": ["out_of_scope"],
                        "missing": ["该问题不在运维知识与工单范围内"],
                    },
                    "_detail": {"route": "out_of_scope"},
                },
                goto="refuse",
            )
        allowed.append("search_knowledge")
        steps.append({"action": "retrieve", "reason": "先检索有权限的知识库"})
        if ent.get("error_codes"):
            allowed.append("analyze_error_code")
            steps.append(
                {"action": "tool", "tool": "analyze_error_code", "reason": "问题包含错误码"}
            )
        incident = intent == "incident_ticket"
        if incident or (
            intent == "troubleshoot"
            and (rules.wants_logs(state["question"]) or ent.get("error_codes"))
        ):
            allowed.append("query_system_logs")
            steps.append({"action": "tool", "tool": "query_system_logs", "reason": "需要日志证据"})
        if incident:
            allowed.append("get_service_status")
        if intent == "ticket_query" or ent.get("tickets"):
            allowed.append("get_ticket")
            steps.append({"action": "tool", "tool": "get_ticket", "reason": "查询工单"})
        if intent == "ticket_action" and rules.explicit_ticket_request(state["question"]):
            allowed.append("create_ticket_draft")
            steps.append(
                {"action": "tool", "tool": "create_ticket_draft", "reason": "用户明确要求建单"}
            )
            steps.append({"action": "approval", "reason": "副作用操作需人工审批"})
        steps.append({"action": "generate", "reason": "基于证据生成并核验引用"})
        if incident:
            # the draft is composed SERVER-SIDE from the verified solution (draft node); the
            # model is never offered create_ticket_draft in this pipeline
            steps.append({"action": "approval", "reason": "解决方案核验后生成草稿并提交人工审批"})
        # ticket lookups / actions do not need the KB; everything else retrieves first
        needs_rag = intent not in ("ticket_action", "ticket_query")
        if not needs_rag:
            steps = [st for st in steps if st["action"] != "retrieve"]
        p = {"steps": steps, "needs_rag": needs_rag, "allowed_tools": allowed}
        goto = "retrieve" if p["needs_rag"] else "agent"
        return Command(
            update={"plan": p, "_detail": {"allowed_tools": allowed, "next": goto}}, goto=goto
        )

    @node("retrieve", ("assess",))
    def retrieve(state: AgentState) -> Command:
        q = state["question"]
        with deps.session_factory() as sess:
            user = sess.get(User, uuid.UUID(state["user_id"]))
            if user is None or not user.is_active:
                raise PermissionError("acting user not found or disabled")
            qa = QAService(sess, s, deps.embedder, MockLLM())  # gate + ACL retrieval, no LLM
            res = qa.answer(user, q, top_k=5)
            ids = [uuid.UUID(str(c["chunk_id"])) for c in res.candidates][: s.qa_max_context_chunks]
            loaded = KnowledgeRepository(sess).load_chunks(ids)
            evs = []
            for c in res.candidates[: s.qa_max_context_chunks]:
                row = loaded.get(uuid.UUID(str(c["chunk_id"])))
                if row is None:
                    continue
                clean = sanitize(row[0].content)
                evs.append(
                    Evidence(
                        id=str(c["chunk_id"]),
                        source="kb",
                        title=str(c["document_name"]),
                        locator=str(c["locator"]),
                        text=clean.text,
                        score=float(c["evidence"]),
                        flagged=clean.flagged,
                    ).model_dump()
                )
        gate = {
            "ok": res.evidence_sufficient,
            "reason": res.refusal_reason,
            "score": res.evidence_score,
            "threshold": res.threshold,
            "signal": res.evidence_signal,
        }
        return Command(
            update={
                "evidence": evs,
                "assessment": {"qa_gate": gate},
                "_detail": {"chunks": len(evs), "gate": gate},
            },
            goto="assess",
        )

    def presence_for(state: AgentState) -> Callable[[list[str]], bool]:
        def presence(parts: list[str]) -> bool:
            with deps.session_factory() as sess:
                user = sess.get(User, uuid.UUID(state["user_id"]))
                return bool(user) and KnowledgeRepository(sess).terms_cooccur(user, parts)

        return presence

    @node("assess", ("generate", "agent", "respond", "refuse"))
    def assess(state: AgentState) -> Command:
        c = state["classification"]
        allowed = state["plan"]["allowed_tools"]
        tool_ev = _evidence_from_tools(state)
        evs = _dedupe(
            [e for e in tool_ev if e["source"] == "tool"]
            + [e for e in tool_ev if e["source"] == "kb"]
            + list(state.get("evidence") or [])
        )
        gate = (state.get("assessment") or {}).get("qa_gate") or (
            state.get("assessment") or {}
        ).get("checks", {}).get("qa_gate", {})

        def _assess(judge: dict[str, Any] | None = None) -> Any:
            return assess_evidence(
                question=state["question"],
                intent=c["intent"],
                entities=c["entities"],
                evidence=evs,
                tool_records=_turn_items(state, "tool_records"),
                qa_gate=gate or {},
                presence=presence_for(state),
                tools_available=allowed,
                required_tools=[
                    st["tool"]
                    for st in state["plan"]["steps"]
                    if st["action"] == "tool" and st.get("tool") != "create_ticket_draft"
                ],
                judge=judge,
            )

        a = _assess()
        judge_usage = Usage()
        degraded_j = list(state.get("degraded") or [])
        if a.decision == "sufficient" and s.agent_llm_judge and not llm.is_mock:
            # optional second opinion: may only tighten; its confidence is never used
            (jd, judge_usage), degraded_j = llm_call(
                state,
                "judge",
                lambda: llm.judge(state["question"], evs[:6]),
                lambda: (None, Usage()),
            )
            if jd is not None:
                a = _assess(jd)
        limits_hit = (
            state.get("react_rounds", 0) >= s.agent_max_react_rounds
            or state.get("tool_calls", 0) >= s.agent_max_tool_calls
        )
        decision = a.decision
        trecs = _turn_items(state, "tool_records")
        ok_tools = {r["tool"] for r in trecs if r["status"] in ("ok", "duplicate")}
        failed = [r for r in trecs if r["status"] not in ("ok", "duplicate")]
        required = [
            st["tool"]
            for st in state["plan"]["steps"]
            if st["action"] == "tool" and st.get("tool") != "create_ticket_draft"
        ]
        failed_required = [r for r in failed if r["tool"] in required and r["tool"] not in ok_tools]
        if failed:
            a.reasons.append(
                "tool_failures:" + ",".join(f"{r['tool']}={r['error_class']}" for r in failed)
            )
        if decision == "need_tools" and (limits_hit or state.get("agent_idle")):
            a.reasons.append("tool_limit_reached" if limits_hit else "no_further_tool_available")
            decision = "refuse"
        elif decision == "sufficient" and failed_required:
            # a required tool (e.g. logs) failed or was denied: do not answer from KB alone
            a.reasons.append("required_tool_failed")
            decision = "refuse"
        if decision == "refuse" and failed_required:
            a.missing.append(
                "所需工具未能取得证据："
                + "、".join(f"{r['tool']}（{r['error_class']}）" for r in failed_required)
            )
        a.decision = decision  # type: ignore[assignment]
        a.checks["qa_gate"] = gate
        goto = {
            "sufficient": "generate",
            "need_tools": "agent",
            "ask_clarification": "respond",
            "refuse": "refuse",
        }[decision]
        upd: dict[str, Any] = {
            **_usage_update(state, judge_usage),
            "degraded": degraded_j,
            "assessment": {**a.model_dump(), "qa_gate": gate},
            "evidence": evs,
            "_detail": {
                "decision": decision,
                "reasons": a.reasons[-3:],
                "need_tools": a.need_tools,
            },
        }
        if decision == "ask_clarification":
            cites = [
                {
                    "index": i,
                    "source": e["source"],
                    "title": e["title"],
                    "locator": e["locator"],
                    "id": e["id"],
                }
                for i, e in enumerate(evs[:4], 1)
            ]
            upd["final"] = _final(
                "clarify",
                "资料之间存在矛盾，请确认你指的是哪个场景/版本：\n"
                + "\n".join(f"- {x}" for x in a.contradictions),
                citations=cites,
                refusal_reason="contradictory_evidence",
            )
        return Command(update=upd, goto=goto)

    @node("agent", ("tools", "assess", "collect"))
    def agent(state: AgentState) -> Command:
        allowed = state["plan"]["allowed_tools"]
        remaining = s.agent_max_tool_calls - state.get("tool_calls", 0)
        a = state.get("assessment") or {}
        c = state["classification"]
        done_keys = [
            (r["tool"], json.dumps(r["args_summary"], sort_keys=True, ensure_ascii=False))
            for r in _turn_items(state, "tool_records")
        ]
        hint = {
            "intent": c["intent"],
            "entities": c["entities"],
            "need_tools": a.get("need_tools", []),
            "tools_done": [(t, k) for t, k in done_keys],
            "max_calls": max(0, remaining),
            "prefix_service": json.loads((DATA / "services.json").read_text("utf-8"))[
                "prefix_service"
            ],
            "search_query": " ".join(
                h["head"] for h in (a.get("checks", {}).get("heads") or []) if h.get("head")
            )
            or None,
        }
        schemas = openai_tool_schemas(allowed)
        reply, degraded = llm_call(
            state,
            "decide",
            lambda: llm.decide(state["question"], _history_openai(state), schemas, hint),
            lambda: rules_llm.decide(state["question"], [], schemas, hint),
        )
        # ---- server-side filter: the model's proposal is a request, not a command
        accepted, rejected = [], []
        seen = {(t, k) for t, k in done_keys}
        for tc in reply.tool_calls:
            key = (tc["name"], json.dumps(tc["args"], sort_keys=True, ensure_ascii=False))
            if tc["name"] not in allowed:
                rejected.append({**tc, "why": "not_allowed"})
            elif key in seen:
                rejected.append({**tc, "why": "duplicate_call"})
            elif len(accepted) >= remaining:
                rejected.append({**tc, "why": "tool_call_limit"})
            else:
                accepted.append(tc)
                seen.add(key)
        recs = [
            {
                "turn": state.get("turn", 0),
                "call_id": str(r.get("id") or uuid.uuid4().hex[:12]),
                "tool": r["name"],
                "args_summary": {k: str(v)[:80] for k, v in r["args"].items()},
                "status": "denied",
                "error_class": "permission" if r["why"] == "not_allowed" else "limit",
                "attempts": 0,
                "duration_ms": 0.0,
                "result_summary": r["why"],
                "idempotency_key": None,
            }
            for r in rejected
        ]
        upd: dict[str, Any] = {
            **_usage_update(state, reply.usage),
            "degraded": degraded,
            "react_rounds": state.get("react_rounds", 0) + 1,
            "tool_records": recs,
            "_detail": {
                "proposed": [t["name"] for t in reply.tool_calls],
                "accepted": [t["name"] for t in accepted],
                "rejected": [(t["name"], t["why"]) for t in rejected],
            },
        }
        if not accepted:
            upd["agent_idle"] = True
            return Command(update=upd, goto="assess")
        ai = AIMessage(
            content="",
            tool_calls=[
                {"id": t["id"], "name": t["name"], "args": t["args"], "type": "tool_call"}
                for t in accepted
            ],
        )
        upd["messages"] = [ai]
        upd["tool_calls"] = state.get("tool_calls", 0) + len(accepted)
        upd["agent_idle"] = False
        return Command(update=upd, goto="tools")

    def ctx_factory(state: dict[str, Any]) -> ToolContext:
        return ToolContext(
            settings=s,
            session_factory=deps.session_factory,
            embedder=deps.embedder,
            run_id=state.get("run_id"),
            thread_id=state["thread_id"],
            user_id=state["user_id"],
            allowed=list((state.get("plan") or {}).get("allowed_tools", [])),
            faults=dict(deps.faults or {}),
            user_role=str(state.get("user_role") or "user"),
        )

    tool_node = ToolNode(make_tools(ctx_factory), handle_tool_errors=True)

    @node("collect", ("assess", "approval", "refuse"))
    def collect(state: AgentState) -> Command:
        # ToolNode validation failures produce ToolMessages without records: record them too
        recs_ids = {r["call_id"] for r in _turn_items(state, "tool_records")}
        extra = []
        for m in state.get("messages", []):
            if (
                isinstance(m, ToolMessage)
                and m.status == "error"
                and m.tool_call_id not in recs_ids
            ):
                extra.append(
                    {
                        "turn": state.get("turn", 0),
                        "call_id": m.tool_call_id,
                        "tool": m.name or "?",
                        "args_summary": {},
                        "status": "invalid",
                        "error_class": "validation",
                        "attempts": 0,
                        "duration_ms": 0.0,
                        "result_summary": str(m.content)[:120],
                        "idempotency_key": None,
                    }
                )
        drafts = [
            o for o in _turn_items(state, "tool_outputs") if o["tool"] == "create_ticket_draft"
        ]
        last_ai = next(
            (m for m in reversed(state.get("messages", [])) if isinstance(m, AIMessage)), None
        )
        batch = {tc["id"] for tc in (last_ai.tool_calls if last_ai else [])}
        batch_recs = [r for r in _turn_items(state, "tool_records") if r["call_id"] in batch]
        tools_trace = {
            "turn": state.get("turn", 0),
            "node": "tools",
            "status": "ok"
            if all(r["status"] in ("ok", "duplicate") for r in batch_recs)
            else "error",
            "ms": round(sum(r["duration_ms"] for r in batch_recs), 2),
            "detail": {"calls": [(r["tool"], r["status"]) for r in batch_recs]},
        }
        upd: dict[str, Any] = {
            "_pre_trace": [tools_trace],
            "tool_records": extra,
            "_detail": {
                "tool_results": [
                    (r["tool"], r["status"]) for r in _turn_items(state, "tool_records")
                ][-6:]
            },
        }
        if drafts:
            d = drafts[-1]["output"]
            upd["draft"] = d
            upd["status"] = "awaiting_approval"
            upd["final"] = _final(
                "awaiting_approval", f"已生成待审批工单草稿 {d['draft_id'][:8]}…，等待管理员审批。"
            )
            return Command(update=upd, goto="approval")
        if state["classification"]["intent"] == "ticket_action":
            failed = [
                r for r in _turn_items(state, "tool_records") if r["tool"] == "create_ticket_draft"
            ]
            upd["assessment"] = {
                "decision": "refuse",
                "reasons": ["draft_not_created"]
                + [f"{r['status']}:{r['error_class']}" for r in failed],
                "missing": ["工单草稿未能生成"],
            }
            return Command(update=upd, goto="refuse")
        return Command(update=upd, goto="assess")

    # ------------------------------------------------------------- Stage 4 helpers
    def backend_tool(state: AgentState, tool: str, args: dict[str, Any], as_user: User) -> Any:
        """Backend-initiated MCP call (never offered to the model): business operation after
        approval, draft revision. Identity = the given DB user, not anything in the state
        the model could influence. Recorded in tool_records like any other call."""
        from opspilot.mcp.contracts import SERVER_TOOLS, TOOL_SERVER

        server = TOOL_SERVER[tool]
        in_model, _ = SERVER_TOOLS[server][tool]
        call_id = f"srv-{tool}-{uuid.uuid4().hex[:8]}"
        t0 = time.perf_counter()
        status, err_cls, out, err = "ok", None, None, None
        try:
            if s.agent_tool_transport == "mcp":
                from opspilot.mcp.client import CallerCtx, get_hub

                out = get_hub(s, deps.session_factory).call(
                    server,
                    tool,
                    args,
                    CallerCtx(
                        str(as_user.id),
                        as_user.role,
                        state["thread_id"],
                        state.get("run_id"),
                        call_id,
                    ),
                )
            else:
                from opspilot.mcp import impl

                with deps.session_factory() as sess:
                    u = sess.get(User, as_user.id)
                    out = getattr(impl, tool)(
                        sess,
                        u,
                        in_model.model_validate(args),
                        ToolContext(
                            s,
                            deps.session_factory,
                            deps.embedder,
                            state.get("run_id"),
                            state["thread_id"],
                            str(as_user.id),
                            [tool],
                        ),
                    )
        except Exception as exc:
            from opspilot.agent.tools import classify

            status, err_cls, err = "error", classify(exc), exc
            if "conflict" in type(exc).__name__.lower():
                err_cls = "conflict"
        rec = {
            "turn": state.get("turn", 0),
            "call_id": call_id,
            "tool": tool,
            "args_summary": {k: str(v)[:80] for k, v in args.items()},
            "status": status,
            "error_class": err_cls,
            "attempts": 1,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
            "result_summary": (
                json.dumps(out.model_dump(), ensure_ascii=False)[:160]
                if out
                else f"{err_cls}: {err}"
            ),
            "idempotency_key": getattr(out, "idempotency_key", None),
            "server": server,
            "initiator": "backend",
            "acting_user": str(as_user.id),
        }
        if state.get("run_id"):
            with deps.session_factory() as sess:
                sess.add(
                    ToolCall(
                        run_id=uuid.UUID(str(state["run_id"])),
                        tool_name=tool,
                        transport="mcp-stdio" if s.agent_tool_transport == "mcp" else "inprocess",
                        arguments=rec["args_summary"],
                        result={
                            "summary": rec["result_summary"],
                            "deduplicated": bool(getattr(out, "deduplicated", False)),
                            "initiator": "backend",
                        }
                        if out is not None
                        else None,
                        status="ok" if status == "ok" else "error",
                        attempt=1,
                        error=err_cls,
                        duration_ms=int(rec["duration_ms"]),
                        caller_id=as_user.id,
                        call_id=call_id[:64],
                    )
                )
                sess.commit()
        return out, rec, err

    def _draft_args(state: AgentState) -> dict[str, Any]:
        """Draft composed server-side from the VERIFIED answer + tool facts. The model does
        not author it, so tool output / injected text cannot steer what gets filed."""
        ent = state["classification"]["entities"]
        outs = _turn_items(state, "tool_outputs")
        code = (ent.get("error_codes") or [None])[0]
        svc = (ent.get("services") or [None])[0]
        status_by_svc = {}
        log_lines = []
        for o in outs:
            out = o["output"]
            if o["tool"] == "analyze_error_code" and out.get("service"):
                svc = svc or out["service"]
            if o["tool"] == "get_service_status":
                status_by_svc[out["service"]] = out["status"]
            if o["tool"] == "query_system_logs":
                log_lines.append(f"{out['service']}：{out['total']} 条，按错误码 {out['by_code']}")
        health = status_by_svc.get(svc or "", "unknown")
        priority = {"down": "P1", "degraded": "P2"}.get(health, "P3")
        answer = re.sub(
            r"^【Mock 智能体[^】]*】\s*", "", (state.get("final") or {}).get("answer", "")
        )
        cites = (state.get("final") or {}).get("citations") or []
        desc = (
            f"【现象】{state['question']}\n"
            f"【服务状态】{svc or '未知'}：{health}\n"
            f"【日志】{'；'.join(log_lines) or '无'}\n"
            f"【分析与建议（已核验引用）】\n{answer}\n"
            f"【证据】{'；'.join(sorted({c['title'] for c in cites}))}"
        )
        args: dict[str, Any] = {
            "title": f"{code or '故障'} {svc or ''} 排查与处理".replace("  ", " ")[:120],
            "description": sanitize(desc).text[:2000],
            "priority": priority,
            "category": "incident",
        }
        if svc:
            args["service"] = svc
        return args

    @node("draft", ("approval", "refuse"))
    def draft(state: AgentState) -> Command:
        ctx = ctx_factory(state)
        ctx.allowed = [*ctx.allowed, "create_ticket_draft"]  # server-side grant for this node
        call_id = f"srv-draft-{state.get('turn', 0)}"
        outcome = ToolGuard(ctx).run("create_ticket_draft", _draft_args(state), call_id)
        rec = {**outcome.record, "turn": state.get("turn", 0), "initiator": "backend"}
        upd: dict[str, Any] = {"tool_records": [rec], "_detail": {"status": outcome.status}}
        if outcome.output is None:
            upd["assessment"] = {
                "decision": "refuse",
                "reasons": [f"draft_not_created:{outcome.status}:{rec.get('error_class')}"],
                "missing": ["工单草稿未能生成"],
            }
            return Command(update=upd, goto="refuse")
        d = {**outcome.output, "revision": 1}
        solution = (state.get("final") or {}).get("answer", "")
        upd.update(
            {
                "tool_outputs": [
                    {
                        "turn": state.get("turn", 0),
                        "call_id": call_id,
                        "tool": "create_ticket_draft",
                        "output": outcome.output,
                    }
                ],
                "draft": d,
                "status": "awaiting_approval",
                "final": _final(
                    "awaiting_approval",
                    f"{solution}\n\n已生成待审核工单草稿 {d['draft_id'][:8]}…（第 1 版），"
                    "等待管理员审批。",
                    citations=(state.get("final") or {}).get("citations") or [],
                ),
            }
        )
        upd["_detail"]["draft_id"] = d["draft_id"]
        return Command(update=upd, goto="approval")

    def approval(state: AgentState) -> Command:
        # NOTE: no side effects before interrupt(): this node re-runs from the top on resume
        t0 = time.perf_counter()
        d = state["draft"] or {}
        decision = interrupt(
            {
                "type": "ticket_draft_approval",
                "draft_id": d.get("draft_id"),
                "revision": d.get("revision", 1),
                "payload_sha256": d.get("payload_sha256"),
                "question": state["question"],
            }
        )
        # only the server (AgentService.approve, admin-checked) produces resume payloads;
        # anything else (missing reviewer, unknown decision) is treated as a rejection
        if not isinstance(decision, dict) or decision.get("decision") not in (
            "approved",
            "rejected",
            "returned",
            "expired",
        ):
            decision = {"decision": "rejected", "comment": "invalid resume payload"}
        return Command(
            update={
                "approval": decision,
                "steps": state.get("steps", 0) + 1,
                "node_trace": [
                    _trace(state, "approval", t0, "ok", {"decision": decision.get("decision")})
                ],
            },
            goto="apply_approval",
        )

    @node("apply_approval", ("respond", "revise"))
    def apply_approval(state: AgentState) -> Command:
        d, ap = state["draft"] or {}, state.get("approval") or {}
        did = uuid.UUID(d["draft_id"])
        verdict = ap.get("decision")
        with deps.session_factory() as sess:
            draft_row = sess.get(TicketDraft, did)
            if draft_row is None:
                raise RuntimeError("draft missing")
            if verdict == "expired":
                sess.execute(
                    update(TicketDraft)
                    .where(TicketDraft.id == did, TicketDraft.status == "pending_approval")
                    .values(status="expired")
                )
                sess.commit()
                return Command(
                    update={
                        "status": "expired",
                        "draft_result": {"status": "expired"},
                        "final": _final(
                            "failed",
                            "审批超时，草稿已过期，未执行任何操作。",
                            refusal_reason="approval_expired",
                        ),
                        "_detail": {"draft": "expired"},
                    },
                    goto="respond",
                )
            reviewer = sess.get(User, uuid.UUID(str(ap.get("reviewer_id"))))
            if (
                reviewer is None
                or not reviewer.is_active
                or reviewer.role != "admin"
                or verdict not in ("approved", "rejected", "returned")
            ):
                raise PermissionError("approval requires an admin reviewer and a valid decision")
            existing = sess.scalar(select(Approval).where(Approval.draft_id == did))
            applied = False
            if existing is None and draft_row.status == "pending_approval":
                # conditional update + unique(draft_id): a decision is recorded at most once
                n = sess.execute(
                    update(TicketDraft)
                    .where(TicketDraft.id == did, TicketDraft.status == "pending_approval")
                    .values(status=verdict)
                ).rowcount
                if n == 1:
                    sess.add(
                        Approval(
                            draft_id=did,
                            decision=verdict,
                            reviewer_id=reviewer.id,
                            comment=(ap.get("comment") or "")[:500],
                            payload_sha256=draft_row.payload_sha256,
                        )
                    )
                sess.commit()
                applied = n == 1
            sess.refresh(draft_row)
            appr = sess.scalar(select(Approval).where(Approval.draft_id == did))
            status = draft_row.status
            revision = draft_row.revision
            result: dict[str, Any] = {
                "status": status,
                "applied_now": applied,
                "approval_id": str(appr.id) if appr else None,
                "decision": appr.decision if appr else None,
                "reviewer_id": str(appr.reviewer_id) if appr else None,
                "decided_at": appr.decided_at.isoformat() if appr else None,
                "revision": revision,
            }
            reviewer_email = reviewer.email
            sess.expunge(reviewer)
        recs: list[dict[str, Any]] = []
        if status in ("approved", "executed") and result["decision"] == "approved":
            # business operation: the Ticket MCP server re-checks the approval in the DB and
            # executes it exactly once (repeat -> deduplicated, same ticket)
            out, rec, err = backend_tool(
                state,
                "update_ticket_status",
                {"draft_id": str(did), "approval_id": str(result["approval_id"])},
                reviewer,
            )
            recs.append(rec)
            if out is None:
                result["business_op"] = {"ok": False, "error": f"{rec['error_class']}: {err}"[:200]}
                return Command(
                    update={
                        "draft_result": result,
                        "tool_records": recs,
                        "final": _final(
                            "failed",
                            "审批已通过，但执行工单操作失败（已记录，可重试恢复）。",
                            refusal_reason="business_op_failed",
                        ),
                        "status": "failed",
                        "_status": "error",
                        "_detail": {"draft": status, "business_op": "failed"},
                    },
                    goto="respond",
                )
            result["business_op"] = {"ok": True, **out.model_dump()}
            result["ticket_number"] = out.ticket_number
            msg = (
                f"审批通过（审批人 {reviewer_email}），"
                f"已通过 Ticket MCP 创建工单 {out.ticket_number}"
                f"（状态 {out.status}）。" + ("（重复执行已去重）" if out.deduplicated else "")
            )
        elif status == "returned":
            if revision > s.mcp_max_revisions:
                msg = "草稿被退回，但已达到最大修改次数，流程结束，未执行任何操作。"
            else:
                return Command(
                    update={
                        "draft_result": result,
                        "_detail": {"draft": "returned", "revision": revision},
                    },
                    goto="revise",
                )
        elif status == "rejected":
            msg = "草稿已被驳回，未执行任何操作。"
        else:
            msg = f"草稿状态：{status}，未执行任何操作。"
        return Command(
            update={
                "draft_result": result,
                "tool_records": recs,
                "final": _final("answered", msg),
                "status": "completed",
                "_detail": {
                    "draft": status,
                    "applied_now": applied,
                    "ticket": result.get("ticket_number"),
                },
            },
            goto="respond",
        )

    @node("revise", ("approval", "respond"))
    def revise(state: AgentState) -> Command:
        """Return-for-revision: new draft version that incorporates the reviewer's comment
        (created by the Ticket MCP server; idempotent per parent + revision)."""
        d, ap = state["draft"] or {}, state.get("approval") or {}
        with deps.session_factory() as sess:
            parent = sess.get(TicketDraft, uuid.UUID(d["draft_id"]))
            owner = sess.get(User, uuid.UUID(state["user_id"]))
            if parent is None or owner is None:
                raise RuntimeError("draft or owner missing")
            comment = sanitize((ap.get("comment") or "").strip()).text[:400] or "（无意见）"
            args = {
                "parent_draft_id": str(parent.id),
                "title": parent.title,
                "description": (parent.description + f"\n【审批意见 r{parent.revision}】{comment}")[
                    -2000:
                ],
                "priority": parent.priority,
            }
            sess.expunge(owner)
        out, rec, err = backend_tool(state, "revise_ticket_draft", args, owner)
        if out is None:
            return Command(
                update={
                    "tool_records": [rec],
                    "final": _final(
                        "failed", "草稿修订失败，流程结束。", refusal_reason="revise_failed"
                    ),
                    "status": "failed",
                    "_status": "error",
                    "_detail": {"error": str(err)[:120]},
                },
                goto="respond",
            )
        nd = {**out.model_dump(), "revision": int(d.get("revision", 1)) + 1}
        return Command(
            update={
                "tool_records": [rec],
                "draft": nd,
                "approval": None,
                "status": "awaiting_approval",
                "final": _final(
                    "awaiting_approval",
                    f"已按审批意见生成草稿第 {nd['revision']} 版 {nd['draft_id'][:8]}…，"
                    "等待再次审批。",
                ),
                "_detail": {"draft_id": nd["draft_id"], "revision": nd["revision"]},
            },
            goto="approval",
        )

    @node("generate", ("verify",))
    def generate(state: AgentState) -> Command:
        evs = (state.get("evidence") or [])[:6]
        (g, u), degraded = llm_call(
            state,
            "generate",
            lambda: llm.generate(state["question"], evs),
            lambda: rules_llm.generate(state["question"], evs),
        )
        cites = []
        for i in sorted(set(g.citations)):
            if 1 <= i <= len(evs):
                e = evs[i - 1]
                cites.append(
                    {
                        "index": i,
                        "source": e["source"],
                        "title": e["title"],
                        "locator": e["locator"],
                        "id": e["id"],
                        "flagged": e["flagged"],
                    }
                )
        mock = llm.is_mock or any(x.startswith("generate:") for x in degraded)
        return Command(
            update={
                **_usage_update(state, u),
                "degraded": degraded,
                "final": _final("answered", g.answer, citations=cites),
                "_detail": {
                    "citations": len(cites),
                    "generator": "mock-extractive" if mock else llm.model,
                },
            },
            goto="verify",
        )

    @node("verify", ("respond", "refuse", "draft"))
    def verify(state: AgentState) -> Command:
        f, evs = state["final"], state.get("evidence") or []
        ans = f["answer"]
        problems: list[str] = []
        idx = {int(x) for x in _INDEX.findall(ans)}
        # real models sometimes return the JSON citations list without inline [n] markers;
        # fall back to the declared list (numbers are still checked against cited text)
        if not idx and not llm.is_mock:
            idx = {int(c["index"]) for c in f["citations"]}
        if not f["citations"] or not idx:
            problems.append("no_citation")
        if any(i < 1 or i > len(evs) for i in idx):
            problems.append("citation_out_of_range")
        cited_text = "\n".join(evs[i - 1]["text"] for i in idx if 1 <= i <= len(evs))
        # extractive lines must be verbatim quotes of the evidence they cite
        if llm.is_mock or any(x.startswith("generate:") for x in state.get("degraded") or []):
            for line in ans.splitlines():
                m = re.match(r"\[(\d+)\]\s*(.+)", line)
                if (
                    m
                    and 1 <= int(m.group(1)) <= len(evs)
                    and not verify_quote(m.group(2), evs[int(m.group(1)) - 1]["text"])
                ):
                    problems.append(f"quote_not_verbatim:[{m.group(1)}]")
        # numbers in the answer must appear in cited evidence (no invented values)
        for n in set(_NUM.findall(_INDEX.sub("", ans))):
            if n not in cited_text:
                problems.append(f"unsupported_number:{n}")
        trusted = [e["text"] for e in evs if not e["flagged"]]
        pol = check_answer(ans, trusted, tuple(s.qa_link_allowlist))
        problems += pol.violations
        if problems:
            return Command(
                update={
                    "final": {**f, "policy_violations": problems},
                    "assessment": {
                        **(state.get("assessment") or {}),
                        "decision": "refuse",
                        "reasons": ["answer_failed_verification"],
                        "missing": [],
                    },
                    "_status": "error",
                    "_detail": {"problems": problems[:5]},
                },
                goto="refuse",
            )
        out = (
            ans
            if not (llm.is_mock or "generate:" in " ".join(state.get("degraded") or []))
            else f"{MOCK_BANNER}\n{ans}"
        )
        nxt = "draft" if state["classification"]["intent"] == "incident_ticket" else "respond"
        return Command(
            update={"final": {**f, "answer": out}, "_detail": {"ok": True, "next": nxt}},
            goto=nxt,
        )

    @node("respond", (END,))
    def respond(state: AgentState) -> Command:
        f = state.get("final") or _final("failed", "无结果")
        status = state.get("status")
        if status not in ("awaiting_approval", "expired"):
            status = {
                "answered": "completed",
                "clarify": "completed",
                "refused": "refused",
                "failed": "failed",
                "awaiting_approval": "awaiting_approval",
            }[f["status"]]
        return Command(
            update={
                "status": status,
                "messages": [AIMessage(f["answer"])],
                "_detail": {"status": status},
            },
            goto=END,
        )

    @node("refuse", (END,))
    def refuse(state: AgentState) -> Command:
        a = state.get("assessment") or {}
        missing = a.get("missing") or []
        reasons = a.get("reasons") or ["insufficient_evidence"]
        body = f"{REFUSE_PREFIX}：在你有权限访问的资料和工具结果中没有足够证据。"
        if missing:
            body += "\n缺少的信息：" + "；".join(dict.fromkeys(missing))
        prev = state.get("final") or {}
        f = _final(
            "refused",
            body,
            refusal_reason=reasons[-1] if reasons else None,
            policy_violations=prev.get("policy_violations", []) if prev else [],
        )
        return Command(
            update={
                "final": f,
                "status": "refused",
                "messages": [AIMessage(body)],
                "_detail": {"reason": f["refusal_reason"]},
            },
            goto=END,
        )

    @node("fail_safe", (END,))
    def fail_safe(state: AgentState) -> Command:
        err = state.get("error") or "unknown_error"
        body = "处理失败，已安全终止，未执行任何未审批操作。"
        return Command(
            update={
                "final": _final("failed", body, refusal_reason=err),
                "status": "failed",
                "messages": [AIMessage(body)],
                "_detail": {"error": err[:120]},
            },
            goto=END,
        )

    g = StateGraph(AgentState)
    for fn in (
        guard_in,
        classify,
        plan,
        retrieve,
        assess,
        agent,
        collect,
        apply_approval,
        generate,
        verify,
        respond,
        refuse,
        fail_safe,
    ):
        g.add_node(fn.__name__, fn, destinations=fn._destinations)  # type: ignore[attr-defined]
    for fn in (draft, revise):
        g.add_node(fn.__name__, fn, destinations=fn._destinations)  # type: ignore[attr-defined]
    g.add_node("approval", approval, destinations=("apply_approval",))
    g.add_node("tools", tool_node)
    g.add_edge(START, "guard_in")
    g.add_edge("tools", "collect")
    return g.compile(checkpointer=checkpointer)
