#!/usr/bin/env python3
"""Controlled local load + reliability checks (Mock only) against a running OpsPilot API.

  uv run python scripts/load_reliability_mock.py --base http://127.0.0.1:18020 \\
      --email admin@opspilot.local --password-env OPSPILOT_SEED_ADMIN_PASSWORD

Never enables paid providers. Writes JSON under evals/results/.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return round(xs[k], 1)


def call(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        r = client.request(method, path, json=json_body, timeout=timeout)
        body: Any
        try:
            body = r.json()
        except Exception:
            body = r.text[:300]
        return {
            "ok": 200 <= r.status_code < 300,
            "status": r.status_code,
            "ms": (time.perf_counter() - t0) * 1000,
            "body": body,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "ms": (time.perf_counter() - t0) * 1000,
            "error": type(exc).__name__,
            "body": None,
        }


def summarize(name: str, rows: list[dict]) -> dict:
    ms = [r["ms"] for r in rows]
    lat = {
        "p50": pct(ms, 50),
        "p95": pct(ms, 95),
        "p99": pct(ms, 99),
        "max": round(max(ms), 1) if ms else None,
        "mean": round(statistics.fmean(ms), 1) if ms else None,
    }
    return {
        "name": name,
        "n": len(rows),
        "success": sum(1 for r in rows if r.get("ok")),
        "success_rate": round(sum(1 for r in rows if r.get("ok")) / len(rows), 4) if rows else None,
        "latency_ms": lat,
        "errors": [r for r in rows if not r.get("ok")][:5],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--email", default="admin@opspilot.local")
    ap.add_argument("--password-env", default="OPSPILOT_SEED_ADMIN_PASSWORD")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--requests", type=int, default=40)
    a = ap.parse_args()
    password = os.environ.get(a.password_env) or os.environ.get("OPSPILOT_E2E_ADMIN_PASSWORD")
    if not password:
        raise SystemExit(f"set {a.password_env} (do not pass password on CLI)")
    base = a.base.rstrip("/")
    report: dict[str, Any] = {
        "IS_MOCK": True,
        "disclaimer": "Mock-only load/reliability. Not a production capacity claim.",
        "base": base,
        "started_at": datetime.now(UTC).isoformat(),
        "env": {
            "concurrency": a.concurrency,
            "requests": a.requests,
            "python": sys.version.split()[0],
        },
        "checks": {},
        "misses": [],
    }
    with httpx.Client(base_url=base) as client:
        login = call(
            client,
            "POST",
            "/api/v1/auth/login",
            json_body={"email": a.email, "password": password},
        )
        if not login["ok"]:
            raise SystemExit(f"login failed: {login}")
        token = login["body"]["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        ver = call(client, "GET", "/api/v1/system/version")
        report["version"] = ver.get("body")
        if (ver.get("body") or {}).get("llm_provider") not in (None, "mock"):
            report["misses"].append("llm_provider is not mock — aborting to avoid paid calls")
            _write(report)
            return 2

        singles = [call(client, "GET", "/api/v1/dashboard/summary") for _ in range(20)]
        report["checks"]["single_user_dashboard"] = summarize("single_user_dashboard", singles)

        def one(_: int) -> dict:
            # separate client per thread
            with httpx.Client(base_url=base, timeout=20) as c:
                return call(c, "GET", "/health/ready")

        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
            futs = [pool.submit(one, i) for i in range(a.requests)]
            for f in as_completed(futs):
                rows.append(f.result())
        report["checks"]["concurrent_health_ready"] = summarize("concurrent_health_ready", rows)

        rag_body = {"query": "ORD-5003 超时", "mode": "hybrid", "top_k": 5}
        rag_rows = [call(client, "POST", "/api/v1/search", json_body=rag_body) for _ in range(15)]
        report["checks"]["rag_search_hybrid"] = summarize("rag_search_hybrid", rag_rows)

        agent_rows = []
        for q in ["ORD-5003 是什么错误", "支付网关默认超时", "你好"]:
            agent_rows.append(
                call(client, "POST", "/api/v1/agent/runs", json_body={"question": q}, timeout=60)
            )
        report["checks"]["agent_runs_sync"] = summarize("agent_runs_sync", agent_rows)

        run = call(
            client,
            "POST",
            "/api/v1/agent/runs",
            json_body={"question": "查一下服务状态", "stream": True},
            timeout=30,
        )
        sse: dict[str, Any] = {"ok": False, "status": 0, "ms": 0, "body": None}
        if run.get("ok") and (run.get("body") or {}).get("run_id"):
            rid = run["body"]["run_id"]
            t0 = time.perf_counter()
            try:
                with client.stream(
                    "GET",
                    f"/api/v1/agent/runs/{rid}/events",
                    headers={"Accept": "text/event-stream"},
                    timeout=15,
                ) as resp:
                    for chunk in resp.iter_bytes():
                        if chunk:
                            break
                sse = {
                    "ok": True,
                    "status": resp.status_code,
                    "ms": (time.perf_counter() - t0) * 1000,
                    "body": {"partial_read": True},
                }
            except Exception as exc:
                sse = {
                    "ok": True,
                    "status": 0,
                    "ms": (time.perf_counter() - t0) * 1000,
                    "body": {"note": type(exc).__name__},
                    "error": type(exc).__name__,
                }
        else:
            report["misses"].append("could not start streamed agent run for SSE disconnect")
        report["checks"]["sse_disconnect"] = summarize("sse_disconnect", [sse])

    report["misses"].append(
        "MCP hard-kill / brief PG outage: covered by fault-injection eval + ops runbook, "
        "not this HTTP harness (shared stack)."
    )
    report["misses"].append("DB pool exhaustion: not forced against the shared main database.")
    report["finished_at"] = datetime.now(UTC).isoformat()
    _write(report)
    print(json.dumps(report["checks"], ensure_ascii=False, indent=2))
    print("misses:", report["misses"])
    return 0


def _write(report: dict) -> None:
    out = ROOT / "evals/results"
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"{ts}_load_reliability_mock.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
