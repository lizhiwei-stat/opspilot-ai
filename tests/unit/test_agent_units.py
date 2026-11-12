"""Unit tests: evidence-sufficiency rules and the Qwen adapter with a FAKE client (no network,
no paid call: the fake client is injected and the gateway guards run for real)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from opspilot.agent import rules
from opspilot.agent.evidence import assess
from opspilot.agent.llm import AgentLLMError, MockAgentLLM, QwenAgentLLM
from opspilot.agent.tools import SPECS, idempotency_key, openai_tool_schemas
from opspilot.llm.gateway import ModelGateway
from tests.conftest import make_settings


def ev(title: str, text: str) -> dict[str, Any]:
    return {
        "id": title,
        "source": "kb",
        "title": title,
        "locator": "",
        "text": text,
        "score": 1.0,
        "flagged": False,
    }


def run(q: str, evidence: list[dict], presence: bool = True, **kw: Any):
    return assess(
        question=q,
        intent=kw.pop("intent", "knowledge_qa"),
        entities=rules.entities(q),
        evidence=evidence,
        tool_records=kw.pop("tool_records", []),
        qa_gate=kw.pop("qa_gate", {"ok": True}),
        presence=lambda parts: presence,
        tools_available=kw.pop("tools", ["search_knowledge"]),
        **kw,
    )


def test_contradictory_values_ask_for_clarification() -> None:
    a = run(
        "令牌有效期是多少",
        [ev("a.md", "访问令牌有效期为 60 分钟。"), ev("b.md", "访问令牌有效期为 30 分钟。")],
    )
    assert a.decision == "ask_clarification"
    assert a.contradictions and "60分钟" in a.contradictions[0] and "30分钟" in a.contradictions[0]


def test_same_value_in_two_docs_is_not_a_contradiction() -> None:
    a = run(
        "令牌有效期是多少",
        [ev("a.md", "访问令牌有效期为 60 分钟。"), ev("b.md", "令牌有效期 60 分钟。")],
    )
    assert a.decision == "sufficient"


def test_head_absent_from_accessible_kb_refuses() -> None:
    a = run("年假有几天", [ev("x.md", "值班补贴按天计算。")], presence=False)
    assert a.decision == "refuse" and "head_term_absent_from_accessible_kb" in a.reasons


def test_head_in_kb_but_not_retrieved_asks_for_search() -> None:
    a = run("令牌有效期是多少", [ev("x.md", "无关内容。")], presence=True)
    assert a.decision == "need_tools" and a.need_tools == ["search_knowledge"]


def test_judge_confidence_is_ignored_and_can_only_tighten() -> None:
    good = [ev("a.md", "访问令牌有效期为 60 分钟。")]
    a = run("令牌有效期是多少", good, judge={"answers_question": True, "confidence": 0.01})
    assert a.decision == "sufficient" and "confidence" not in a.checks["judge"]
    b = run(
        "令牌有效期是多少",
        good,
        judge={"answers_question": False, "missing": ["版本"], "confidence": 0.99},
    )
    assert b.decision == "refuse" and "judge_veto" in b.reasons


def test_error_code_requires_tool() -> None:
    a = run(
        "ORD-5003 怎么处理",
        [ev("a.md", "ORD-5003 表示连接池耗尽")],
        tools=["search_knowledge", "analyze_error_code"],
        intent="troubleshoot",
    )
    assert a.decision == "need_tools" and "analyze_error_code" in a.need_tools


def test_rules_ticket_number_is_not_an_error_code() -> None:
    assert rules.entities("TCK-0001 和 ORD-5003")["error_codes"] == ["ORD-5003"]
    e = rules.entities("日志出现ORD-5003，工单TCK-0002")  # no spaces around codes
    assert e["error_codes"] == ["ORD-5003"] and e["tickets"] == ["TCK-0002"]
    assert rules.explicit_ticket_request("帮我创建一个工单")
    assert not rules.explicit_ticket_request("工单系统怎么用")

    assert rules.explicit_ticket_request("分析并创建待审工单")
    assert rules.explicit_ticket_request("请生成高优先级工单草稿")
    assert not rules.explicit_ticket_request("总结支付熔断，下一轮创建工单")
    assert rules.wants_service_status("查 payment-gateway 服务状态")
    assert not rules.wants_service_status("查一下工单 TCK-0001 的状态")
    assert not rules.wants_service_status("如何查看服务状态")
    assert rules.rule_intent("公司下季度未发布的股价") == "out_of_scope"
    assert rules.rule_intent("火星基地的部署手册在哪") == "out_of_scope"
    assert rules.fact_question("kubectl 回滚命令是什么")["type"] == "entity"
    assert rules.fact_question("支付网关默认超时是多少")["type"] == "value"
    assert rules.fact_question("访问令牌有效期是多久")["type"] == "value"


def test_tool_schemas_hide_identity_and_validate() -> None:
    sch = openai_tool_schemas(list(SPECS))
    for s in sch:
        props = s["function"]["parameters"].get("properties", {})
        assert not {"user_id", "state", "tool_call_id", "role"} & set(props)
    assert idempotency_key("t", "x", {"a": 1, "b": 2}) == idempotency_key(
        "t", "x", {"b": 2, "a": 1}
    )
    assert idempotency_key("t", "x", {"a": 1}) != idempotency_key("t2", "x", {"a": 1})


def test_mock_llm_is_labelled() -> None:
    m = MockAgentLLM()
    assert m.is_mock and m.name == "mock"


# ------------------------------------------------ Qwen adapter with a fake client
class FakeCompletions:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.requests: list[dict] = []

    def create(self, **kw: Any) -> Any:
        self.requests.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(content: str = "", tool_calls: list | None = None, pt: int = 100, ct: int = 20) -> Any:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct),
    )


def _qwen(
    tmp_path, replies: list[Any], **kw: Any
) -> tuple[QwenAgentLLM, FakeCompletions, ModelGateway]:
    s = make_settings(
        allow_paid_api=True,
        paid_approval_id="test-approval",
        paid_budget_cny=1.0,
        paid_max_calls=kw.pop("paid_max_calls", 10),
        agent_llm_provider="qwen",
        model_ledger_path=str(tmp_path / "ledger.jsonl"),
        **kw,
    )
    gw = ModelGateway(s, run_id="unit")
    comp = FakeCompletions(replies)
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return QwenAgentLLM(s, gw, client=client), comp, gw


def test_qwen_function_calling_and_accounting(tmp_path) -> None:
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="get_ticket", arguments=json.dumps({"ticket_number": "TCK-0001"})
        ),
    )
    q, comp, gw = _qwen(tmp_path, [_resp(tool_calls=[tc])])
    r = q.decide("查工单 TCK-0001", [], openai_tool_schemas(["get_ticket"]), {})
    assert r.tool_calls == [
        {"id": "call_1", "name": "get_ticket", "args": {"ticket_number": "TCK-0001"}}
    ]
    assert r.usage.tokens_in == 100 and r.usage.tokens_out == 20 and r.usage.source == "api"
    assert r.usage.cost_cny == pytest.approx(gw.prices.cost("qwen-plus", 100, 20))
    req = comp.requests[0]
    assert req["tools"][0]["function"]["name"] == "get_ticket" and req["tool_choice"] == "auto"
    assert req["max_tokens"] <= 800  # clamped by paid_max_output_tokens
    rec = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])
    assert rec["kind"] == "agent_decide" and rec["approval_id"] == "test-approval"


def test_qwen_structured_output(tmp_path) -> None:
    q, comp, _ = _qwen(tmp_path, [_resp(json.dumps({"answer": "60 分钟 [1]", "citations": [1]}))])
    g, _u = q.generate("令牌有效期", [ev("a.md", "访问令牌有效期为 60 分钟。")])
    assert g.citations == [1] and comp.requests[0]["response_format"] == {"type": "json_object"}


def test_qwen_bad_json_is_a_parse_error(tmp_path) -> None:
    q, _, _ = _qwen(tmp_path, [_resp("not json")])
    with pytest.raises(AgentLLMError) as e:
        q.generate("x", [ev("a", "b")])
    assert e.value.kind == "parse"


def test_qwen_timeout_maps_to_timeout(tmp_path) -> None:
    import httpx
    import openai

    err = openai.APITimeoutError(request=httpx.Request("POST", "http://x"))
    q, _, gw = _qwen(tmp_path, [err])
    with pytest.raises(AgentLLMError) as e:
        q.classify("你好", [])
    assert e.value.kind == "timeout"
    assert gw.reserved_cny == 0  # reservation released / booked


def test_qwen_refused_without_approval_never_calls_client(tmp_path) -> None:
    q, comp, _ = _qwen(tmp_path, [_resp("{}")], paid_max_calls=0)
    with pytest.raises(AgentLLMError) as e:
        q.classify("你好", [])
    assert e.value.kind == "refused" and comp.requests == []


def test_qwen_judge_drops_confidence(tmp_path) -> None:
    q, _, _ = _qwen(tmp_path, [_resp(json.dumps({"answers_question": True, "confidence": 0.9}))])
    data, _ = q.judge("x", [ev("a", "b")])
    assert data == {"answers_question": True}
