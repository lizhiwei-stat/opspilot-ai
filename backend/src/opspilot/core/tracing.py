"""Lightweight request/agent tracing without OpenTelemetry or Prometheus.

Why not OTel + Prometheus + Grafana here:
- Single-node local Compose stack; three more containers add ops cost and another failure mode.
- We already have structlog JSON + Postgres (agent_events, tool_calls, mcp_tool_audit, llm ledger).
- This module adds: a shared trace_id, an in-process ring of recent spans for the admin UI,
  and optional Prometheus text exposition from the same counters (no Grafana required).

Maintenance cost: ~150 lines, no new services. Swap for OTel later if multi-host is needed.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog

_lock = threading.Lock()
_spans: deque[dict[str, Any]] = deque(maxlen=500)
_counters: dict[str, float] = {
    "http_requests_total": 0,
    "http_errors_total": 0,
    "agent_runs_total": 0,
    "mcp_calls_total": 0,
    "mcp_errors_total": 0,
    "model_calls_total": 0,
    "retrieval_calls_total": 0,
    "tokens_prompt_total": 0,
    "tokens_completion_total": 0,
    "estimated_cost_cny_total": 0,
}
_latency_ms: dict[str, deque[float]] = {
    "http": deque(maxlen=1000),
    "agent": deque(maxlen=500),
    "retrieval": deque(maxlen=500),
    "mcp": deque(maxlen=500),
    "model": deque(maxlen=500),
}


def new_trace_id() -> str:
    return uuid.uuid4().hex


def bind_trace(*, request_id: str | None = None, trace_id: str | None = None, **extra: Any) -> str:
    tid = trace_id or new_trace_id()
    payload: dict[str, Any] = {"trace_id": tid}
    if request_id:
        payload["request_id"] = request_id
    payload.update({k: v for k, v in extra.items() if v is not None})
    structlog.contextvars.bind_contextvars(**payload)
    return tid


@dataclass
class Span:
    name: str
    kind: str  # http|node|mcp|model|retrieval|db|agent
    trace_id: str
    request_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    status: str = "ok"
    duration_ms: float = 0
    error_type: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def emit(self) -> None:
        row = asdict(self)
        # drop bulky / sensitive raw payloads if any slipped in
        for k in ("prompt", "completion", "authorization", "api_key", "password", "token"):
            row.get("attrs", {}).pop(k, None)
        with _lock:
            _spans.appendleft(row)
            if self.kind in _latency_ms:
                _latency_ms[self.kind].append(self.duration_ms)
            if self.kind == "http":
                _counters["http_requests_total"] += 1
                if self.status not in ("ok", "2xx") and not str(self.status).startswith("2"):
                    _counters["http_errors_total"] += 1
            elif self.kind == "agent":
                _counters["agent_runs_total"] += 1
            elif self.kind == "mcp":
                _counters["mcp_calls_total"] += 1
                if self.status != "ok":
                    _counters["mcp_errors_total"] += 1
            elif self.kind == "model":
                _counters["model_calls_total"] += 1
                toks = self.attrs or {}
                _counters["tokens_prompt_total"] += float(toks.get("prompt_tokens") or 0)
                _counters["tokens_completion_total"] += float(toks.get("completion_tokens") or 0)
                _counters["estimated_cost_cny_total"] += float(toks.get("estimated_cost_cny") or 0)
            elif self.kind == "retrieval":
                _counters["retrieval_calls_total"] += 1
        structlog.get_logger("opspilot.trace").info(
            "span",
            span=self.name,
            kind=self.kind,
            status=self.status,
            duration_ms=self.duration_ms,
            error_type=self.error_type,
            **{k: v for k, v in (self.attrs or {}).items() if k not in ("prompt", "completion")},
        )


def record_span(**kwargs: Any) -> Span:
    sp = Span(**kwargs)
    sp.emit()
    return sp


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return round(s[k], 2)


def snapshot(limit: int = 100) -> dict[str, Any]:
    with _lock:
        lat = {k: list(v) for k, v in _latency_ms.items()}
        counters = dict(_counters)
        spans = list(_spans)[:limit]
    return {
        "counters": counters,
        "latency_ms": {
            k: {
                "count": len(v),
                "p50": _pct(v, 50),
                "p95": _pct(v, 95),
                "p99": _pct(v, 99),
                "max": round(max(v), 2) if v else None,
            }
            for k, v in lat.items()
        },
        "recent_spans": spans,
        "note": (
            "In-process ring buffer (lost on restart). "
            "Durable traces live in agent_events / tool_calls / "
            "mcp_tool_audit / llm ledger."
        ),
    }


def prometheus_text() -> str:
    snap = snapshot(limit=0)
    lines = [
        "# HELP opspilot_info OpsPilot lightweight metrics",
        "# TYPE opspilot_info gauge",
        'opspilot_info{component="api"} 1',
    ]
    for k, v in snap["counters"].items():
        lines.append(f"# TYPE opspilot_{k} counter")
        lines.append(f"opspilot_{k} {v}")
    for kind, stats in snap["latency_ms"].items():
        if stats["p50"] is None:
            continue
        lines.append(f"# TYPE opspilot_{kind}_latency_ms summary")
        lines.append(f'opspilot_{kind}_latency_ms{{quantile="0.5"}} {stats["p50"]}')
        lines.append(f'opspilot_{kind}_latency_ms{{quantile="0.95"}} {stats["p95"]}')
        lines.append(f'opspilot_{kind}_latency_ms{{quantile="0.99"}} {stats["p99"]}')
        lines.append(f"opspilot_{kind}_latency_ms_count {stats['count']}")
    return "\n".join(lines) + "\n"


def reset_for_tests() -> None:
    with _lock:
        _spans.clear()
        for k in _counters:
            _counters[k] = 0
        for d in _latency_ms.values():
            d.clear()
