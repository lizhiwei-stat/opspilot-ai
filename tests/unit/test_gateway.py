"""Budget/approval guards of the unified model gateway, with FAKE paid providers (no network,
no key). The fakes pose as name="openai_compatible", i.e. exactly what the real paid
DashScope providers report, so they take the guarded path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opspilot.core.redact import redact, register_secret
from opspilot.llm.base import ChatMessage, ChatResult, Usage
from opspilot.llm.gateway import (
    BudgetExceeded,
    GuardedEmbedding,
    GuardedLLM,
    ModelGateway,
    PaidCallRefused,
    PriceTable,
)
from opspilot.llm.mock import MockEmbedding, MockLLM
from tests.conftest import make_settings

PRICES = PriceTable(
    {
        "fake-embed": {"input_per_m": 1_000_000.0},  # 1 CNY per token: easy arithmetic
        "fake-chat": {"input_per_m": 1_000_000.0, "output_per_m": 2_000_000.0},
        "m-chat": {"input_per_m": 0.8, "output_per_m": 2.0},  # qwen-plus-like
    }
)
FAKE_KEY = "sk-fakefakefake0123456789abcdef"


class FakePaidEmbedding:
    name, model, dim = "openai_compatible", "fake-embed", 4

    def __init__(self, usage_tokens: int | None = None, fail: bool = False) -> None:
        self.calls = 0
        self.usage_tokens, self.fail = usage_tokens, fail
        self.last_usage: dict | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"upstream 500 (key={FAKE_KEY})")
        self.last_usage = (
            {"prompt_tokens": self.usage_tokens} if self.usage_tokens is not None else None
        )
        return [[0.0] * 4 for _ in texts]


class FakePaidLLM:
    name, model = "openai_compatible", "fake-chat"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, *, temperature=0.0, max_tokens=1024):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ChatResult("ok", self.model, Usage(2, 1))

    def health(self):  # type: ignore[no-untyped-def]
        return None


def gw(tmp_path: Path, **kw: object) -> ModelGateway:
    base = {
        "allow_paid_api": True,
        "paid_approval_id": "APPROVAL-TEST-1",
        "paid_budget_cny": 10.0,
        "paid_max_calls": 100,
        "model_ledger_path": str(tmp_path / "ledger.jsonl"),
    }
    base.update(kw)
    return ModelGateway(make_settings(**base), run_id="t", prices=PRICES)


def ledger(tmp_path: Path) -> list[dict]:
    p = tmp_path / "ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_paid_off_by_default(tmp_path: Path) -> None:
    g = ModelGateway(
        make_settings(model_ledger_path=str(tmp_path / "l.jsonl")), run_id="t", prices=PRICES
    )
    p = FakePaidEmbedding()
    with pytest.raises(PaidCallRefused, match="disabled"):
        g.embed(p, ["abc"])
    assert p.calls == 0 and ledger(tmp_path) == []


@pytest.mark.parametrize(
    ("kw", "msg"),
    [({"paid_approval_id": ""}, "approval"), ({"paid_budget_cny": 0.0}, "budget cap")],
)
def test_approval_and_cap_required(tmp_path: Path, kw: dict, msg: str) -> None:
    p = FakePaidEmbedding()
    with pytest.raises(PaidCallRefused, match=msg):
        gw(tmp_path, **kw).embed(p, ["abc"])
    assert p.calls == 0


def test_unpriced_model_refused(tmp_path: Path) -> None:
    p = FakePaidEmbedding()
    p.model = "unknown-model"
    with pytest.raises(PaidCallRefused, match="no price"):
        gw(tmp_path).embed(p, ["abc"])
    assert p.calls == 0


def test_api_usage_is_booked_and_hard_stop_at_cap(tmp_path: Path) -> None:
    g = gw(tmp_path)  # cap 10 CNY, 1 CNY/token
    p = FakePaidEmbedding(usage_tokens=3)
    g.embed(p, ["abcd"])  # estimate 4 <= 10, real usage 3 -> spent 3
    g.embed(p, ["abcd"])  # spent 6
    assert g.spent_cny == pytest.approx(6.0)
    with pytest.raises(BudgetExceeded):
        g.embed(p, ["abcde"])  # estimate 5 > remaining 4: refused BEFORE the call
    assert p.calls == 2
    rows = ledger(tmp_path)
    assert [r["usage_source"] for r in rows] == ["api", "api"]
    assert all(r["approval_id"] == "APPROVAL-TEST-1" and r["budget_cny"] == 10.0 for r in rows)
    assert rows[-1]["spent_cny_after"] == pytest.approx(6.0)
    g.embed(p, ["abc"])  # estimate 3 fits, usage 3 -> exactly 9
    p.usage_tokens = 1
    g.embed(p, ["a"])  # -> 10 == cap
    with pytest.raises(BudgetExceeded, match="exhausted"):
        g.embed(p, ["a"])
    assert p.calls == 4 and g.spent_cny == pytest.approx(10.0)


def test_missing_usage_books_estimate(tmp_path: Path) -> None:
    g = gw(tmp_path)
    g.embed(FakePaidEmbedding(usage_tokens=None), ["abcd"])
    assert ledger(tmp_path)[0]["usage_source"] == "estimate" and g.spent_cny == 4.0


def test_failed_call_booked_and_key_never_in_ledger(tmp_path: Path) -> None:
    register_secret(FAKE_KEY)
    g = gw(tmp_path)
    with pytest.raises(RuntimeError):
        g.embed(FakePaidEmbedding(fail=True), ["ab"])
    raw = (tmp_path / "ledger.jsonl").read_text()
    assert FAKE_KEY not in raw
    row = json.loads(raw)
    assert row["ok"] is False and row["cost_cny"] == 2.0  # booked conservatively


def test_chat_cost_in_and_out(tmp_path: Path) -> None:
    g = gw(tmp_path)
    llm = GuardedLLM(FakePaidLLM(), g)
    llm.chat([ChatMessage("user", "hi")], max_tokens=2)  # est 2 + 2*2 = 6 <= 10
    assert g.spent_cny == pytest.approx(2 * 1 + 1 * 2)
    with pytest.raises(BudgetExceeded):
        llm.chat([ChatMessage("user", "hi")], max_tokens=4)  # est 2 + 8 > 6 remaining
    s = g.summary()
    assert s["calls"] == {"openai_compatible:chat": 2} and s["usage"]["tokens_out"] == 1


def test_free_providers_pass_through_and_are_counted(tmp_path: Path) -> None:
    g = ModelGateway(
        make_settings(model_ledger_path=str(tmp_path / "l.jsonl")), run_id="t", prices=PRICES
    )
    e = GuardedEmbedding(MockEmbedding(), g)
    assert len(e.embed(["a", "b"])) == 2
    GuardedLLM(MockLLM(), g).chat([ChatMessage("user", "x")])
    assert g.calls == {"mock:embed": 1, "mock:chat": 1}
    assert g.spent_cny == 0 and not (tmp_path / "l.jsonl").exists()


def test_repo_price_table_has_verified_dashscope_prices() -> None:
    t = PriceTable.load(Path(__file__).resolve().parents[2] / "configs" / "model_prices.json")
    assert t.cost("text-embedding-v4", 1_000_000, 0) == pytest.approx(0.5)
    assert t.cost("qwen-plus", 1_000_000, 1_000_000) == pytest.approx(2.8)


def test_redact_shapes_and_registered_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "plainvalue-without-shape-42")
    s = "auth Bearer abcdefghijklmnop1234 k=sk-abcdefghijklmnop key plainvalue-without-shape-42"
    out = redact(s)
    assert "abcdefghijklmnop1234" not in out and "sk-abcdefghijklmnop" not in out
    assert "plainvalue-without-shape-42" not in out and out.count("***REDACTED***") == 3


# ----------------------------------------------------------- Stage 3 pre-request guards
def _fn_ok(max_out: int):  # type: ignore[no-untyped-def]
    return "ok", {"prompt_tokens": 10, "completion_tokens": max_out}


def test_call_count_limit_is_checked_before_request(tmp_path: Path) -> None:
    g = gw(tmp_path, paid_max_calls=2)
    sent: list[int] = []

    def fn(m: int):  # type: ignore[no-untyped-def]
        sent.append(m)
        return "ok", {"prompt_tokens": 1, "completion_tokens": 1}

    for _ in range(2):
        g.guarded_call(
            provider="openai_compatible", model="m-chat", kind="chat", est_in=5, max_out=5, fn=fn
        )
    with pytest.raises(PaidCallRefused, match="call limit"):
        g.guarded_call(
            provider="openai_compatible", model="m-chat", kind="chat", est_in=5, max_out=5, fn=fn
        )
    assert len(sent) == 2  # third request never sent


def test_default_max_calls_zero_refuses_everything(tmp_path: Path) -> None:
    g = gw(tmp_path, paid_max_calls=0)
    with pytest.raises(PaidCallRefused):
        g.guarded_call(
            provider="openai_compatible",
            model="m-chat",
            kind="chat",
            est_in=1,
            max_out=1,
            fn=_fn_ok,
        )


def test_max_output_tokens_clamped_and_used_in_estimate(tmp_path: Path) -> None:
    g = gw(tmp_path, paid_max_output_tokens=50)
    seen: list[int] = []

    def fn(m: int):  # type: ignore[no-untyped-def]
        seen.append(m)
        return "ok", {"prompt_tokens": 1, "completion_tokens": m}

    g.guarded_call(
        provider="openai_compatible", model="m-chat", kind="chat", est_in=1, max_out=4000, fn=fn
    )
    assert seen == [50]


def test_estimate_exceeding_budget_blocks_before_request(tmp_path: Path) -> None:
    g = gw(tmp_path, paid_budget_cny=0.000001, paid_max_output_tokens=8000)
    called = []
    with pytest.raises(BudgetExceeded):
        g.guarded_call(
            provider="openai_compatible",
            model="m-chat",
            kind="chat",
            est_in=100000,
            max_out=8000,
            fn=lambda m: called.append(m) or ("x", None),
        )
    assert called == [] and g.reserved_cny == 0.0


def test_concurrency_limit_refuses_when_no_slot(tmp_path: Path) -> None:
    import threading

    g = gw(tmp_path, paid_max_concurrency=1, paid_concurrency_wait_s=0.05)
    gate, started = threading.Event(), threading.Event()

    def slow(m: int):  # type: ignore[no-untyped-def]
        started.set()
        gate.wait(2)
        return "ok", {"prompt_tokens": 1, "completion_tokens": 1}

    t = threading.Thread(
        target=lambda: g.guarded_call(
            provider="openai_compatible", model="m-chat", kind="chat", est_in=1, max_out=1, fn=slow
        )
    )
    t.start()
    started.wait(2)
    with pytest.raises(PaidCallRefused, match="concurrency"):
        g.guarded_call(
            provider="openai_compatible",
            model="m-chat",
            kind="chat",
            est_in=1,
            max_out=1,
            fn=_fn_ok,
        )
    gate.set()
    t.join()
    assert g.reserved_cny == 0.0


def test_inflight_reservations_count_against_budget(tmp_path: Path) -> None:
    g = gw(tmp_path)
    est = g._authorize("m-chat", 10, 10)  # one call in flight
    g2_cap = g.cap
    with pytest.raises(BudgetExceeded):
        g._check_budget(g2_cap - est + 1e-9)
    g._release(est)
