"""Stage 4 HTTP API over the real app with agent_tool_transport=mcp: incident run, approval
queue, return-for-revision, approve -> ticket via Ticket MCP, tickets + events, audit, discovery."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opspilot.main import create_app
from tests.agent.test_api_agent import login

Q = "ORD-5003 错误，查文档和日志，分析并创建待审核工单"


@pytest.fixture(scope="module")
def api(mcp_settings):
    with TestClient(create_app(mcp_settings)) as c:
        yield c


def test_incident_workflow_over_http(api) -> None:
    alice, admin, bob = login(api, "alice"), login(api, "admin"), login(api, "bob")
    v = api.post("/api/v1/agent/runs", headers=alice, json={"question": Q}).json()
    assert v["status"] == "awaiting_approval" and v["intent"] == "incident_ticket", v
    assert {t["tool"] for t in v["tools"]} >= {"query_system_logs", "create_ticket_draft"}
    assert api.get("/api/v1/agent/approvals", headers=alice).status_code == 403
    queue = api.get("/api/v1/agent/approvals", headers=admin).json()
    assert v["run_id"] in [q["run_id"] for q in queue]
    url = f"/api/v1/agent/runs/{v['run_id']}/approval"
    assert api.post(url, headers=admin, json={"decision": "returned"}).status_code == 409
    r = api.post(url, headers=admin, json={"decision": "returned", "comment": "补充影响范围"})
    assert r.status_code == 200 and r.json()["draft"]["revision"] == 2
    assert api.post(url, headers=alice, json={"decision": "approved"}).status_code == 403
    done = api.post(url, headers=admin, json={"decision": "approved", "comment": "ok"}).json()
    num = done["draft_result"]["ticket_number"]
    assert done["draft"]["status"] == "executed" and num.startswith("TCK-")
    t = api.get(f"/api/v1/tickets/{num}", headers=alice).json()
    assert t["status"] == "open" and t["events"][0]["approver_id"]
    assert api.get(f"/api/v1/tickets/{num}", headers=bob).status_code == 404
    assert num in [x["number"] for x in api.get("/api/v1/tickets", headers=alice).json()]
    aud = api.get(f"/api/v1/mcp/audit?thread_id={v['thread_id']}", headers=admin).json()
    tools = {(a["side"], a["tool"], a["status"]) for a in aud}
    assert ("server", "update_ticket_status", "ok") in tools
    assert ("server", "revise_ticket_draft", "ok") in tools
    mine = api.get(f"/api/v1/mcp/audit?thread_id={v['thread_id']}", headers=bob).json()
    assert mine == []
    blob = str(aud)
    assert "eyJ" not in blob and "Bearer" not in blob


def test_mcp_discovery_endpoint(api) -> None:
    assert api.get("/api/v1/mcp/servers", headers=login(api, "alice")).status_code == 403
    d = api.get("/api/v1/mcp/servers", headers=login(api, "admin")).json()
    assert d["transport"] == "mcp-stdio"
    assert set(d["servers"]) == {"knowledge", "ticket", "diagnostics"}
    assert all(s["alive"] for s in d["servers"].values())
