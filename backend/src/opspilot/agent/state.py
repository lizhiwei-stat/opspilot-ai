"""Agent state: the single TypedDict that flows through the StateGraph + Pydantic models for
every structured value stored in it (stored as plain dicts so the Postgres checkpointer can
serialize them; validated with the models on the way in/out).

Only system-recorded steps and business decisions live here - never private model reasoning.
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

Intent = Literal[
    "knowledge_qa",  # answer from the knowledge base
    "troubleshoot",  # diagnose an incident: KB + logs + error codes
    "ticket_query",  # look up an existing ticket
    "ticket_action",  # create a ticket draft (side effect -> approval branch)
    "chitchat",
    "out_of_scope",
]
Decision = Literal["sufficient", "need_tools", "ask_clarification", "refuse"]
RunStatus = Literal["running", "awaiting_approval", "completed", "refused", "failed", "expired"]


class Classification(BaseModel):
    intent: Intent
    entities: dict[str, list[str]] = Field(default_factory=dict)  # error_codes, tickets, services
    source: Literal["rules", "llm"] = "rules"


class PlanStep(BaseModel):
    action: Literal["retrieve", "tool", "generate", "approval"]
    tool: str | None = None
    reason: str


class Plan(BaseModel):
    steps: list[PlanStep]
    needs_rag: bool = True
    allowed_tools: list[str] = Field(default_factory=list)  # server-side allowlist for this turn


class Evidence(BaseModel):
    """One piece of evidence. `source` = kb (retrieved chunk) or tool (tool output)."""

    id: str  # chunk_id or tool_call_id
    source: Literal["kb", "tool"]
    title: str  # document name or tool name
    locator: str = ""
    text: str  # sanitized; always treated as untrusted data
    score: float | None = None
    flagged: bool = False  # prompt-injection heuristics fired on this text


class Assessment(BaseModel):
    """Output of the evidence-sufficiency node. Deterministic checks; an optional LLM judge
    only adds missing-info/contradiction findings (its self-reported confidence is ignored)."""

    decision: Decision
    answers_question: bool
    missing: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    need_tools: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    checks: dict[str, Any] = Field(default_factory=dict)


class ToolRecord(BaseModel):
    call_id: str
    tool: str
    args_summary: dict[str, Any]
    status: Literal["ok", "error", "timeout", "denied", "invalid", "duplicate"]
    error_class: str | None = None  # validation|permission|timeout|transient|upstream|internal
    attempts: int = 1
    duration_ms: float = 0.0
    result_summary: str = ""
    idempotency_key: str | None = None


class FinalAnswer(BaseModel):
    status: Literal["answered", "refused", "clarify", "awaiting_approval", "failed"]
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    refusal_reason: str | None = None
    policy_violations: list[str] = Field(default_factory=list)


class Limits(BaseModel):
    max_steps: int = 24  # node executions per turn
    max_tool_calls: int = 6  # per turn
    max_react_rounds: int = 3  # agent<->tools loops per turn
    max_llm_calls: int = 8  # per turn


def _merge_dict(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


class AgentState(TypedDict, total=False):
    # identity: injected by the server, never taken from the model or tool output
    run_id: str
    thread_id: str
    user_id: str
    user_role: str
    # conversation (multi-turn via the checkpointer: messages accumulate across turns)
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    turn: int
    # decisions
    classification: dict[str, Any]
    plan: dict[str, Any]
    evidence: list[dict[str, Any]]
    assessment: dict[str, Any]
    tool_records: Annotated[list[dict[str, Any]], add]
    tool_outputs: Annotated[list[dict[str, Any]], add]  # untrusted-wrapped tool payloads
    draft: dict[str, Any] | None  # pending ticket draft (approval branch)
    approval: dict[str, Any] | None
    draft_result: dict[str, Any] | None  # output of the side-effecting node (idempotent)
    final: dict[str, Any] | None
    # accounting + guards (per turn, reset by guard_in)
    steps: int
    node_trace: Annotated[list[str], add]
    llm_calls: int
    tool_calls: int
    react_rounds: int
    usage: Annotated[dict[str, Any], _merge_dict]
    error: str | None
    status: RunStatus
