"""Agent HTTP API (real FastAPI app + real Postgres checkpointer)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opspilot.main import create_app


@pytest.fixture(scope="module")
def api(agent_db):
    from opspilot.agent import checkpoint

    checkpoint.setup(agent_db["settings"])
    with TestClient(create_app(agent_db["settings"])) as c:
        yield c


def login(c: TestClient, who: str) -> dict[str, str]:
    import os

    key = "OPSPILOT_SEED_ADMIN_PASSWORD" if who == "admin" else "OPSPILOT_SEED_USER_PASSWORD"
    pw = os.environ[key]
    r = c.post("/api/v1/auth/login", json={"email": f"{who}@opspilot.local", "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_requires_auth(api) -> None:
    assert api.post("/api/v1/agent/runs", json={"question": "你好"}).status_code == 401


def test_run_view_shape_and_no_identity_from_body(api) -> None:
    h = login(api, "bob")
    r = api.post(
        "/api/v1/agent/runs",
        headers=h,
        json={"question": "ORD-5003 是什么错误", "user_id": "admin", "role": "admin"},
    )
    assert r.status_code == 200, r.text
    v = r.json()
    for k in (
        "run_id",
        "thread_id",
        "status",
        "node_trace",
        "evidence",
        "tools",
        "answer",
        "citations",
        "usage",
        "latency_ms",
        "failure_reason",
        "is_mock",
        "assessment",
    ):
        assert k in v
    assert v["is_mock"] is True and v["usage"]["cost_cny"] == 0
    # bob cannot read order-platform: error-code KB rows must not leak
    assert all("ORD-5003 | 503" not in (e.get("snippet") or "") for e in v["evidence"])
    assert api.get(f"/api/v1/agent/runs/{v['run_id']}", headers=h).status_code == 200
    assert (
        api.get(f"/api/v1/agent/runs/{v['run_id']}", headers=login(api, "alice")).status_code == 404
    )
    hist = api.get(f"/api/v1/agent/runs/{v['run_id']}/history", headers=h).json()
    assert hist and all("checkpoint_id" in x for x in hist)
    assert any(x["run_id"] == v["run_id"] for x in api.get("/api/v1/agent/runs", headers=h).json())


def test_approval_flow_over_http(api) -> None:
    alice, admin = login(api, "alice"), login(api, "admin")
    v = api.post(
        "/api/v1/agent/runs", headers=alice, json={"question": "订单超时影响下单，帮我创建一个工单"}
    ).json()
    assert (
        v["status"] == "awaiting_approval"
        and v["pending_approval"]["type"] == "ticket_draft_approval"
    )
    url = f"/api/v1/agent/runs/{v['run_id']}/approval"
    assert api.post(url, headers=alice, json={"decision": "approved"}).status_code == 403
    r = api.post(url, headers=admin, json={"decision": "approved", "comment": "ok"})
    assert r.status_code == 200 and r.json()["draft"]["status"] == "approved"
    assert api.post(url, headers=admin, json={"decision": "approved"}).status_code == 409
    rs = api.post(f"/api/v1/agent/runs/{v['run_id']}/resume", headers=alice)
    assert rs.status_code == 409


def test_validation(api) -> None:
    h = login(api, "alice")
    assert api.post("/api/v1/agent/runs", headers=h, json={"question": ""}).status_code == 422
    bad = api.post(
        "/api/v1/agent/runs", headers=h, json={"question": "x", "thread_id": "../../etc"}
    )
    assert bad.status_code == 422
