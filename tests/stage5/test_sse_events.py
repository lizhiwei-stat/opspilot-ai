"""SSE node/event streaming: durable agent_events, Last-Event-ID replay, cursor, auth,
cooperative interrupt + resume."""

from __future__ import annotations

import time

from tests.stage5.conftest import login, parse_sse


def _wait_idle(api, h, run_id, timeout=30.0):
    t = time.time()
    while time.time() - t < timeout:
        v = api.get(f"/api/v1/agent/runs/{run_id}", headers=h).json()
        if v["status"] != "running":
            return v
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_stream_run_events_replay_and_reconnect(api) -> None:
    h = login(api, "alice")
    r = api.post(
        "/api/v1/agent/runs", headers=h, json={"question": "ORD-5003 是什么错误", "stream": True}
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    # stream: blocks until the run is idle, then `end`
    s = api.get(f"/api/v1/agent/runs/{run_id}/events?poll_ms=50", headers=h)
    assert s.status_code == 200 and s.headers["content-type"].startswith("text/event-stream")
    evs = parse_sse(s.text)
    types = [e["event"] for e in evs]
    assert types[0] == "run_started" and "node" in types and types[-2:] == ["final", "end"]
    ids = [int(e["id"]) for e in evs if "id" in e]
    assert ids == sorted(ids) and ids == list(range(1, len(ids) + 1))  # gap-free per run
    nodes = [e["data"]["data"]["node"] for e in evs if e["event"] == "node"]
    assert nodes and all(isinstance(n, str) and n for n in nodes)
    final = next(e for e in evs if e["event"] == "final")["data"]["data"]
    view = api.get(f"/api/v1/agent/runs/{run_id}", headers=h).json()
    assert final["status"] == view["status"]
    # no chain-of-thought / prompt text in any event payload
    assert all("prompt" not in str(e["data"]).lower() for e in evs)
    # reconnect with Last-Event-ID: only later events are replayed
    mid = ids[len(ids) // 2]
    s2 = api.get(
        f"/api/v1/agent/runs/{run_id}/events?poll_ms=50",
        headers=h | {"Last-Event-ID": str(mid)},
    )
    ids2 = [int(e["id"]) for e in parse_sse(s2.text) if "id" in e]
    assert ids2 == [i for i in ids if i > mid]
    # ?after takes precedence; cursor at the end -> immediate end
    s3 = api.get(f"/api/v1/agent/runs/{run_id}/events?after={ids[-1]}", headers=h)
    assert [e["event"] for e in parse_sse(s3.text)] == ["end"]


def test_events_auth_and_isolation(api) -> None:
    h = login(api, "alice")
    run_id = api.post("/api/v1/agent/runs", headers=h, json={"question": "你好"}).json()["run_id"]
    assert api.get(f"/api/v1/agent/runs/{run_id}/events").status_code == 401
    assert (
        api.get(f"/api/v1/agent/runs/{run_id}/events", headers=login(api, "bob")).status_code == 404
    )
    admin = api.get(f"/api/v1/agent/runs/{run_id}/events", headers=login(api, "admin"))
    assert admin.status_code == 200
    bad = api.get(f"/api/v1/agent/runs/{run_id}/events?after=-1", headers=h)
    assert bad.status_code == 422


def test_approval_streams_interrupt_then_resume_events(api) -> None:
    alice, admin = login(api, "alice"), login(api, "admin")
    v = api.post(
        "/api/v1/agent/runs",
        headers=alice,
        json={"question": "订单超时影响下单，帮我创建一个工单", "stream": True},
    ).json()
    v = _wait_idle(api, alice, v["run_id"])
    assert v["status"] == "awaiting_approval"
    evs = parse_sse(api.get(f"/api/v1/agent/runs/{v['run_id']}/events", headers=alice).text)
    assert "interrupt" in [e["event"] for e in evs]
    last = max(int(e["id"]) for e in evs if "id" in e)
    r = api.post(
        f"/api/v1/agent/runs/{v['run_id']}/approval?stream=true",
        headers=admin,
        json={"decision": "approved", "comment": "ok"},
    )
    assert r.status_code == 200
    v2 = _wait_idle(api, alice, v["run_id"])
    assert v2["draft"]["status"] == "executed"
    later = parse_sse(
        api.get(f"/api/v1/agent/runs/{v['run_id']}/events?after={last}", headers=alice).text
    )
    kinds = [e["event"] for e in later]
    assert kinds[0] == "run_started" and kinds[-2:] == ["final", "end"]
    assert any(e["event"] == "tool" for e in later)


def test_cancel_then_resume_from_checkpoint(api) -> None:
    from opspilot.api.routes.agent import get_agent

    class _Req:
        app = api.app

    svc = get_agent(_Req())  # type: ignore[arg-type]
    orig = svc._emit_update
    seen: list[str] = []

    def trip(run_id, node, upd):  # simulate the user's cancel landing after the first node
        orig(run_id, node, upd)
        seen.append(node)
        if len(seen) == 1:
            svc._cancel.add(run_id)

    h = login(api, "alice")
    svc._emit_update = trip
    try:
        v = api.post(
            "/api/v1/agent/runs", headers=h, json={"question": "ORD-5003 是什么错误"}
        ).json()
    finally:
        svc._emit_update = orig
    assert v["status"] == "aborted" and v["failure_reason"] == "cancelled_by_user"
    # a new turn on the same thread is blocked until the interrupted run is resumed
    blocked = api.post(
        "/api/v1/agent/runs", headers=h, json={"question": "再问一次", "thread_id": v["thread_id"]}
    )
    assert blocked.status_code == 409
    # cancel endpoint refuses non-running runs
    assert api.post(f"/api/v1/agent/runs/{v['run_id']}/cancel", headers=h).status_code == 409
    done = api.post(f"/api/v1/agent/runs/{v['run_id']}/resume", headers=h).json()
    assert done["status"] in ("completed", "refused")
    nodes = [n["node"] for n in done["node_trace"]]
    assert nodes.count(seen[0]) == 1  # first node was not re-run on resume
    evs = parse_sse(api.get(f"/api/v1/agent/runs/{v['run_id']}/events", headers=h).text)
    finals = [e["data"]["data"]["status"] for e in evs if e["event"] == "final"]
    assert finals == ["aborted", done["status"]]


def test_threads_sessions(api) -> None:
    h = login(api, "bob")
    a = api.post("/api/v1/agent/runs", headers=h, json={"question": "你好"}).json()
    api.post(
        "/api/v1/agent/runs", headers=h, json={"question": "谢谢", "thread_id": a["thread_id"]}
    )
    ts = api.get("/api/v1/agent/threads", headers=h).json()
    t = next(x for x in ts if x["thread_id"] == a["thread_id"])
    assert t["turns"] == 2 and t["title"] == "你好"
    runs = api.get(f"/api/v1/agent/threads/{a['thread_id']}", headers=h).json()
    assert [r["turn"] for r in runs] == [1, 2]
    assert (
        api.get(f"/api/v1/agent/threads/{a['thread_id']}", headers=login(api, "alice")).status_code
        == 404
    )
