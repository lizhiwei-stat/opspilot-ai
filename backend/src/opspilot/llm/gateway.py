"""Unified model-call layer: ONE interface for mock / local / paid (DashScope) models.

    gw = ModelGateway(settings, run_id="eval-20261102")
    vecs = gw.embed(provider, texts)          # any EmbeddingProvider
    out  = gw.chat(llm, messages)             # any LLMProvider

Free providers (mock, local) pass straight through (calls are still counted). PAID providers
(name == "openai_compatible") are guarded:

  1. OFF by default: OPSPILOT_ALLOW_PAID_API must be true                   (env flag)
  2. per-run approval: OPSPILOT_PAID_APPROVAL_ID must be set; it is written to every ledger
     line so each paid call is attributable to one approval                (approval token)
  3. per-run budget cap in CNY (OPSPILOT_PAID_BUDGET_CNY, > 0); prices from
     configs/model_prices.json; a model without a price is refused        (price table)
  4. BEFORE each call a conservative cost estimate (1 token per character, input + max
     output) must fit in the remaining budget, else BudgetExceeded and NO call is made
  5. AFTER each call the real cost is computed from the API's usage field (prompt/total
     tokens); if usage is missing the estimate is booked (usage_source="estimate")
  6. every paid call (ok or failed) is appended to a JSONL cost ledger; once spent >= cap,
     every further call is refused. The cap is never raised automatically.
  7. (Stage 3) pre-request call-count limit (OPSPILOT_PAID_MAX_CALLS, default 0 = no paid
     calls), max output tokens clamp (OPSPILOT_PAID_MAX_OUTPUT_TOKENS, also used in the
     estimate) and a concurrency limit (OPSPILOT_PAID_MAX_CONCURRENCY): a request that
     cannot get a slot within OPSPILOT_PAID_CONCURRENCY_WAIT_S is refused, not queued forever.

The API key is only ever read from the environment by Settings; nothing here logs or
stores it, and ledger lines pass through core.redact.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opspilot.core.config import Settings
from opspilot.core.redact import redact
from opspilot.llm.base import ChatMessage, ChatResult, EmbeddingProvider, LLMProvider

PAID_PROVIDERS = frozenset({"openai_compatible"})


class PaidCallRefused(RuntimeError):
    """Paid call blocked by a guard (flag / approval / price / budget)."""


class BudgetExceeded(PaidCallRefused):
    pass


@dataclass
class PriceTable:
    models: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, path: str | Path) -> PriceTable:
        p = Path(path)
        if not p.exists():
            return cls({})
        return cls(json.loads(p.read_text(encoding="utf-8")).get("models", {}))

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        if model not in self.models:
            raise PaidCallRefused(f"no price configured for paid model '{model}'")
        m = self.models[model]
        return (
            tokens_in * float(m["input_per_m"]) + tokens_out * float(m.get("output_per_m", 0))
        ) / 1e6


@dataclass
class Ledger:
    path: Path

    def append(self, rec: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = redact(json.dumps(rec, ensure_ascii=False))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


@dataclass
class ModelGateway:
    settings: Settings
    run_id: str
    prices: PriceTable = None  # type: ignore[assignment]  # loaded in __post_init__
    ledger: Ledger = None  # type: ignore[assignment]
    spent_cny: float = 0.0
    calls: dict[str, int] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=lambda: {"tokens_in": 0, "tokens_out": 0})

    paid_calls: int = 0
    reserved_cny: float = 0.0  # estimates of in-flight calls (concurrency-safe budget)
    _slots: threading.BoundedSemaphore = None  # type: ignore[assignment]
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.prices = self.prices or PriceTable.load(self.settings.model_price_table)
        self.ledger = self.ledger or Ledger(Path(self.settings.model_ledger_path))
        self._slots = threading.BoundedSemaphore(self.settings.paid_max_concurrency)

    def clamp_output(self, max_tokens: int) -> int:
        return min(int(max_tokens), int(self.settings.paid_max_output_tokens))

    def guarded_call(
        self,
        *,
        provider: str,
        model: str,
        kind: str,
        est_in: int,
        max_out: int,
        fn: Callable[[int], tuple[Any, dict[str, int] | None]],
        meta: dict[str, Any] | None = None,
    ) -> Any:
        """Generic paid call: ALL guards run before `fn(max_out_clamped)` is invoked.
        `fn` returns (result, usage) with usage = {prompt_tokens, completion_tokens} or None."""
        self._count(provider, kind)
        max_out = self.clamp_output(max_out)
        est = self._authorize(model, est_in, max_out)
        if not self._slots.acquire(timeout=self.settings.paid_concurrency_wait_s):
            self._release(est)
            raise PaidCallRefused("paid concurrency limit: no free slot")
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"provider": provider, "model": model, "kind": kind, **(meta or {})}
        try:
            try:
                out, u = fn(max_out)
            except Exception as exc:
                self._book(
                    reserved=est,
                    rec={
                        **rec,
                        "ok": False,
                        "error": type(exc).__name__,
                        "cost_cny": est,
                        "tokens_in": est_in,
                        "tokens_out": max_out,
                        "usage_source": "estimate (failed call booked conservatively)",
                        "latency_ms": round((time.perf_counter() - t0) * 1e3, 1),
                    },
                )
                raise
        finally:
            self._slots.release()
        tin = int(u["prompt_tokens"]) if u else est_in
        tout = int(u.get("completion_tokens", 0)) if u else max_out
        self._book(
            reserved=est,
            rec={
                **rec,
                "ok": True,
                "tokens_in": tin,
                "tokens_out": tout,
                "usage_source": "api" if u else "estimate",
                "cost_cny": round(self.prices.cost(model, tin, tout), 8),
                "latency_ms": round((time.perf_counter() - t0) * 1e3, 1),
            },
        )
        return out

    # ------------------------------------------------------------------ guards
    @property
    def cap(self) -> float:
        return float(self.settings.paid_budget_cny)

    def _authorize(self, model: str, est_in: int, est_out: int) -> float:
        s = self.settings
        if not s.allow_paid_api:
            raise PaidCallRefused("paid API disabled (OPSPILOT_ALLOW_PAID_API is not true)")
        if not s.paid_approval_id.strip():
            raise PaidCallRefused(
                "paid call needs a per-run approval id (OPSPILOT_PAID_APPROVAL_ID)"
            )
        if self.cap <= 0:
            raise PaidCallRefused("paid call needs a budget cap > 0 (OPSPILOT_PAID_BUDGET_CNY)")
        est = self.prices.cost(model, est_in, est_out)
        with self._lock:  # call-count check + reservation is atomic
            if self.paid_calls >= s.paid_max_calls:
                raise PaidCallRefused(
                    f"paid call limit reached ({self.paid_calls}/{s.paid_max_calls}); "
                    "set OPSPILOT_PAID_MAX_CALLS for an approved run"
                )
            self._check_budget(est)
            self.paid_calls += 1
            self.reserved_cny += est
        return est

    def _release(self, est: float) -> None:
        with self._lock:
            self.reserved_cny = max(0.0, self.reserved_cny - est)

    def _check_budget(self, est: float) -> None:
        committed = self.spent_cny + self.reserved_cny
        if committed >= self.cap:
            raise BudgetExceeded(f"budget exhausted: committed {committed:.6f} >= cap {self.cap}")
        if committed + est > self.cap:
            raise BudgetExceeded(
                f"estimated cost {est:.6f} CNY would exceed remaining budget "
                f"{self.cap - committed:.6f} CNY (cap {self.cap})"
            )

    def _book(self, rec: dict[str, Any], reserved: float = 0.0) -> None:
        with self._lock:  # move the reservation to spent atomically
            self.reserved_cny = max(0.0, self.reserved_cny - reserved)
            self.spent_cny += float(rec.get("cost_cny", 0.0))
        self.usage["tokens_in"] += int(rec.get("tokens_in", 0))
        self.usage["tokens_out"] += int(rec.get("tokens_out", 0))
        self.ledger.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "run_id": self.run_id,
                "approval_id": self.settings.paid_approval_id,
                "budget_cny": self.cap,
                "spent_cny_after": round(self.spent_cny, 8),
                **rec,
            }
        )

    def _count(self, provider: str, kind: str) -> None:
        k = f"{provider}:{kind}"
        self.calls[k] = self.calls.get(k, 0) + 1

    # ------------------------------------------------------------------ calls
    def embed(self, provider: EmbeddingProvider, texts: list[str]) -> list[list[float]]:
        self._count(provider.name, "embed")
        if provider.name not in PAID_PROVIDERS:
            return provider.embed(texts)
        est_in = sum(len(t) for t in texts)  # conservative: >= real tokens for zh text
        est = self._authorize(provider.model, est_in, 0)
        t0 = time.perf_counter()
        rec: dict[str, Any] = {
            "provider": provider.name,
            "model": provider.model,
            "kind": "embed",
            "n_inputs": len(texts),
        }
        try:
            out = provider.embed(texts)
        except Exception as exc:
            self._book(
                reserved=est,
                rec={
                    **rec,
                    "ok": False,
                    "error": type(exc).__name__,
                    "cost_cny": est,
                    "tokens_in": est_in,
                    "usage_source": "estimate (failed call booked conservatively)",
                    "latency_ms": round((time.perf_counter() - t0) * 1e3, 1),
                },
            )
            raise
        u = getattr(provider, "last_usage", None)
        tin = int(u["prompt_tokens"]) if u and u.get("prompt_tokens") is not None else est_in
        self._book(
            reserved=est,
            rec={
                **rec,
                "ok": True,
                "tokens_in": tin,
                "tokens_out": 0,
                "usage_source": "api" if u else "estimate",
                "cost_cny": round(self.prices.cost(provider.model, tin, 0), 8),
                "latency_ms": round((time.perf_counter() - t0) * 1e3, 1),
            },
        )
        return out

    def chat(
        self,
        llm: LLMProvider,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> ChatResult:
        self._count(llm.name, "chat")
        if llm.name not in PAID_PROVIDERS:
            return llm.chat(messages, temperature=temperature, max_tokens=max_tokens)
        est_in = sum(len(m.content) for m in messages)
        max_tokens = self.clamp_output(max_tokens)
        est = self._authorize(llm.model, est_in, max_tokens)
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"provider": llm.name, "model": llm.model, "kind": "chat"}
        try:
            out = llm.chat(messages, temperature=temperature, max_tokens=max_tokens)
        except Exception as exc:
            self._book(
                reserved=est,
                rec={
                    **rec,
                    "ok": False,
                    "error": type(exc).__name__,
                    "cost_cny": est,
                    "tokens_in": est_in,
                    "tokens_out": max_tokens,
                    "usage_source": "estimate (failed call booked conservatively)",
                    "latency_ms": round((time.perf_counter() - t0) * 1e3, 1),
                },
            )
            raise
        has = out.usage.total_tokens > 0
        tin = out.usage.prompt_tokens if has else est_in
        tout = out.usage.completion_tokens if has else max_tokens
        self._book(
            reserved=est,
            rec={
                **rec,
                "ok": True,
                "tokens_in": tin,
                "tokens_out": tout,
                "usage_source": "api" if has else "estimate",
                "cost_cny": round(self.prices.cost(llm.model, tin, tout), 8),
                "latency_ms": round((time.perf_counter() - t0) * 1e3, 1),
            },
        )
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "calls": dict(self.calls),
            "usage": dict(self.usage),
            "spent_cny": round(self.spent_cny, 8),
            "budget_cny": self.cap,
        }


class GuardedEmbedding:
    """EmbeddingProvider facade: every call goes through the gateway's paid-call guards."""

    def __init__(self, inner: EmbeddingProvider, gateway: ModelGateway) -> None:
        self.inner, self.gateway = inner, gateway
        self.name, self.model, self.dim = inner.name, inner.model, inner.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.gateway.embed(self.inner, texts)


class GuardedLLM:
    def __init__(self, inner: LLMProvider, gateway: ModelGateway) -> None:
        self.inner, self.gateway = inner, gateway
        self.name, self.model = inner.name, inner.model

    def chat(
        self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> ChatResult:
        return self.gateway.chat(
            self.inner, messages, max_tokens=max_tokens, temperature=temperature
        )

    def health(self) -> Any:
        return self.inner.health()
