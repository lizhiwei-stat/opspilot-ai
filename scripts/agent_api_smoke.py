"""Live-stack agent smoke + persistence check over HTTP (no paid calls; mock model).

    python scripts/agent_api_smoke.py start   -> run several turns, leave one awaiting approval
    python scripts/agent_api_smoke.py approve -> (after a backend restart) approve it, verify

Passwords come from OPSPILOT_SEED_*_PASSWORD (this project's own env); never printed.
State between the two phases is kept in data/agent_smoke_state.json (run ids only).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = os.environ.get("OPSPILOT_SMOKE_BASE", "http://127.0.0.1:8020")
STATE = Path(__file__).resolve().parents[1] / "data" / "agent_smoke_state.json"


def call(
    method: str, path: str, token: str | None = None, body: dict | None = None
) -> tuple[int, dict]:
    req = urllib.request.Request(  # noqa: S310
        BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def login(who: str) -> str:
    pw = os.environ[
        "OPSPILOT_SEED_ADMIN_PASSWORD" if who == "admin" else "OPSPILOT_SEED_USER_PASSWORD"
    ]
    code, b = call(
        "POST", "/api/v1/auth/login", body={"email": f"{who}@opspilot.local", "password": pw}
    )
    assert code == 200, code
    return b["access_token"]


def brief(v: dict) -> dict:
    return {
        "status": v["status"],
        "final_status": v.get("final_status"),
        "turn": v["turn"],
        "nodes": [n["node"] for n in v["node_trace"]],
        "tools": [f"{t['tool']}:{t['status']}" for t in v["tools"]],
        "refusal_reason": v.get("refusal_reason"),
        "latency_ms": v["latency_ms"],
        "tokens": [v["usage"]["tokens_in"], v["usage"]["tokens_out"]],
        "is_mock": v["is_mock"],
        "answer": (v["answer"] or "")[:120],
    }


def start() -> None:
    a = login("alice")
    out = {}
    for key, q in [
        ("knowledge", "访问令牌有效期是多久"),
        ("error_code", "ORD-5003 是什么错误，怎么处理"),
        ("h029", "支付网关的商户号是多少"),
        ("contradiction", "连接池最大连接数现在是多少"),
    ]:
        code, v = call("POST", "/api/v1/agent/runs", a, {"question": q})
        out[key] = {"http": code, **brief(v)}
    _, t1 = call("POST", "/api/v1/agent/runs", a, {"question": "查一下工单 TCK-0001 的状态"})
    _, t2 = call(
        "POST",
        "/api/v1/agent/runs",
        a,
        {"question": "它现在是什么优先级", "thread_id": t1["thread_id"]},
    )
    out["multi_turn"] = {"turn1": brief(t1), "turn2": brief(t2)}
    code, d = call(
        "POST", "/api/v1/agent/runs", a, {"question": "订单接口 500 了，帮我创建一个工单"}
    )
    out["draft"] = {"http": code, **brief(d), "draft": d["draft"]}
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"run_id": d["run_id"]}))
    print(json.dumps(out, ensure_ascii=False, indent=1))


def approve() -> None:
    rid = json.loads(STATE.read_text())["run_id"]
    a, adm = login("alice"), login("admin")
    _, before = call("GET", f"/api/v1/agent/runs/{rid}", a)
    c_forbid, _ = call("POST", f"/api/v1/agent/runs/{rid}/approval", a, {"decision": "approved"})
    code, v = call(
        "POST",
        f"/api/v1/agent/runs/{rid}/approval",
        adm,
        {"decision": "approved", "comment": "smoke"},
    )
    c_again, _ = call("POST", f"/api/v1/agent/runs/{rid}/approval", adm, {"decision": "approved"})
    _, hist = call("GET", f"/api/v1/agent/runs/{rid}/history", a)
    print(
        json.dumps(
            {
                "status_after_restart_before_approval": before["status"],
                "alice_approve_http": c_forbid,
                "admin_approve_http": code,
                "after": brief(v),
                "draft": v["draft"],
                "create_ticket_draft_executions": sum(
                    1
                    for t in v["tools"]
                    if t["tool"] == "create_ticket_draft" and not t["deduplicated"]
                ),
                "second_approve_http": c_again,
                "checkpoints_in_thread": len(hist),
            },
            ensure_ascii=False,
            indent=1,
        )
    )


if __name__ == "__main__":
    {"start": start, "approve": approve}[sys.argv[1]]()
