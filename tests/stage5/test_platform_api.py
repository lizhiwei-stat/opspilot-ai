"""Stage 5 platform API against the real app + Postgres (mock LLM; no paid calls)."""

from __future__ import annotations

import uuid

from tests.stage5.conftest import login

STRONG = "Passw0rd-" + "x" * 4


def test_dashboard_real_counts_and_denominators(api) -> None:
    h = login(api, "admin")
    api.post("/api/v1/agent/runs", headers=h, json={"question": "ORD-5003 是什么错误"})
    d = api.get("/api/v1/dashboard/summary", headers=h).json()
    t = d["tickets"]
    assert t["total"] == sum(t["by_status"].values()) >= 3
    assert t["pending"] + t["completed"] == t["total"]
    ts = d["agent_runs"]["task_success"]
    assert ts["denominator"] >= 1 and ts["numerator"] <= ts["denominator"]
    assert ts["value"] == round(ts["numerator"] / ts["denominator"], 4)
    tool = d["tool_success"]
    assert tool["denominator"] == sum(tool["by_status"].values())
    assert d["usage"]["mock"]["cost_cny"] == 0 and d["usage"]["real"]["runs"] == 0
    assert d["recent_runs"] and all(r["is_mock"] for r in d["recent_runs"])
    # ordinary user: own scope only
    u = api.get("/api/v1/dashboard/summary", headers=login(api, "bob")).json()
    assert u["scope"] == "own" and u["tickets"]["total"] < t["total"]
    assert api.get("/api/v1/dashboard/summary").status_code == 401


def _ticket_via_approval(api) -> str:
    alice, admin = login(api, "alice"), login(api, "admin")
    v = api.post(
        "/api/v1/agent/runs", headers=alice, json={"question": "订单超时影响下单，帮我创建一个工单"}
    ).json()
    assert v["status"] == "awaiting_approval"
    r = api.post(
        f"/api/v1/agent/runs/{v['run_id']}/approval",
        headers=admin,
        json={"decision": "approved", "comment": "ok"},
    ).json()
    return r["draft_result"]["ticket_number"]


def test_ticket_filters_transitions_and_audit(api) -> None:
    number = _ticket_via_approval(api)
    sam, bob, alice = login(api, "sam"), login(api, "bob"), login(api, "alice")
    # support sees all tickets; filters work
    all_ = api.get("/api/v1/tickets?limit=100", headers=sam).json()
    assert any(t["number"] == number for t in all_) and any(t["number"] == "TCK-0002" for t in all_)
    p1 = api.get("/api/v1/tickets?priority=P1&limit=100", headers=sam).json()
    assert p1 and all(t["priority"] == "P1" for t in p1)
    kw = api.get("/api/v1/tickets?q=ERR_PAY", headers=sam).json()
    assert [t["number"] for t in kw] == ["TCK-0002"]
    assert api.get("/api/v1/tickets?status=bogus", headers=sam).status_code == 422
    # bob (ordinary user) cannot see or change alice's ticket
    assert api.get(f"/api/v1/tickets/{number}", headers=bob).status_code == 404
    body = {"status": "in_progress", "note": "开始处理"}
    assert api.patch(f"/api/v1/tickets/{number}", headers=alice, json=body).status_code == 403
    r = api.patch(f"/api/v1/tickets/{number}", headers=sam, json=body)
    assert r.status_code == 200 and r.json()["status"] == "in_progress"
    bad = api.patch(
        f"/api/v1/tickets/{number}", headers=sam, json={"status": "in_progress", "note": "x"}
    )
    assert bad.status_code == 409  # not a legal transition from in_progress
    nores = api.patch(
        f"/api/v1/tickets/{number}", headers=sam, json={"status": "resolved", "note": "x"}
    )
    assert nores.status_code == 409
    ok = api.patch(
        f"/api/v1/tickets/{number}",
        headers=sam,
        json={"status": "resolved", "note": "已修复", "resolution": "扩容 + 限流"},
    )
    assert ok.status_code == 200
    detail = api.get(f"/api/v1/tickets/{number}", headers=alice).json()
    tos = [e["to_status"] for e in detail["events"]]
    assert tos[-2:] == ["in_progress", "resolved"] and detail["resolution"] == "扩容 + 限流"
    assert detail["events"][0]["approval_id"]  # creation event carries the Stage 4 approval
    assert detail["events"][-1]["actor_name"].startswith("Sam")
    assert (
        api.patch(f"/api/v1/tickets/{number}", headers=sam, json={"status": "open"}).status_code
        == 422
    )


def test_drafts_list_scoping(api) -> None:
    alice, bob, sam = login(api, "alice"), login(api, "bob"), login(api, "sam")
    mine = api.get("/api/v1/ticket-drafts", headers=alice).json()
    assert mine and all(d["created_by"].startswith("Alice") for d in mine)
    assert any(d["approval"] and d["approval"]["decision"] == "approved" for d in mine)
    assert all(
        d["created_by"].startswith("Bob")
        for d in api.get("/api/v1/ticket-drafts", headers=bob).json()
    )
    assert len(api.get("/api/v1/ticket-drafts", headers=sam).json()) >= len(mine)


def test_admin_users_roles_last_admin_and_audit(api) -> None:
    admin = login(api, "admin")
    assert api.get("/api/v1/admin/users", headers=login(api, "sam")).status_code == 403
    users = api.get("/api/v1/admin/users", headers=admin).json()
    assert all("password_hash" not in u for u in users)
    assert {u["role"] for u in users} >= {"admin", "support", "user"}
    seed_admin = next(u for u in users if u["email"] == "admin@opspilot.local")
    # last active admin cannot be demoted or deactivated
    for patch in ({"role": "user"}, {"is_active": False}):
        r = api.patch(f"/api/v1/admin/users/{seed_admin['id']}", headers=admin, json=patch)
        assert r.status_code == 409, r.text
    email = f"s5-{uuid.uuid4().hex[:8]}@opspilot.local"
    weak = api.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"email": email, "display_name": "x", "role": "user", "password": "aaaaaaaaaaaa"},
    )
    assert weak.status_code == 409
    assert (
        api.post(
            "/api/v1/admin/users",
            headers=admin,
            json={"email": "nope", "display_name": "x", "password": STRONG},
        ).status_code
        == 422
    )
    c = api.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"email": email, "display_name": "Temp Admin", "role": "admin", "password": STRONG},
    )
    assert c.status_code == 201 and "password" not in c.text
    dup = api.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"email": email, "display_name": "x", "password": STRONG},
    )
    assert dup.status_code == 409
    new_id = c.json()["id"]
    # with two admins one may be demoted; then the guard bites again
    assert (
        api.patch(
            f"/api/v1/admin/users/{new_id}", headers=admin, json={"role": "support"}
        ).status_code
        == 200
    )
    assert (
        api.patch(
            f"/api/v1/admin/users/{seed_admin['id']}", headers=admin, json={"role": "user"}
        ).status_code
        == 409
    )
    # deactivated user can no longer log in; reset password marker never contains the value
    assert (
        api.patch(
            f"/api/v1/admin/users/{new_id}",
            headers=admin,
            json={"is_active": False, "password": STRONG + "9"},
        ).status_code
        == 200
    )
    r = api.post("/api/v1/auth/login", json={"email": email, "password": STRONG + "9"})
    assert r.status_code == 401
    audit = api.get("/api/v1/admin/audit", headers=admin).json()
    mine = [a for a in audit if a["target_id"] == new_id]
    assert {a["action"] for a in mine} == {"user.create", "user.update"}
    assert STRONG not in str(audit)
    roles = api.get("/api/v1/admin/roles", headers=login(api, "bob")).json()
    assert [r["label"] for r in roles] == ["系统管理员", "技术支持工程师", "普通业务用户"]


def test_settings_never_returns_secrets(api) -> None:
    admin = login(api, "admin")
    s = api.get("/api/v1/admin/settings", headers=admin)
    assert s.status_code == 200
    j = s.json()
    assert isinstance(j["models"]["api_key_configured"], bool)
    assert "llm_api_key" not in s.text and "jwt_secret" not in s.text and "password" not in s.text
    assert j["budget"]["allow_paid_api"] is False and j["stage"] == 5
    assert j["health"]["status"] in ("ready", "not_ready")
    assert api.get("/api/v1/admin/settings", headers=login(api, "alice")).status_code == 403


def test_kb_create_permissions_isolated_fixture(api) -> None:
    sam, admin, bob, alice = (
        login(api, "sam"),
        login(api, "admin"),
        login(api, "bob"),
        login(api, "alice"),
    )
    slug = f"s5-test-{uuid.uuid4().hex[:8]}"
    assert (
        api.post(
            "/api/v1/knowledge-bases", headers=bob, json={"slug": slug, "name": "x"}
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/api/v1/knowledge-bases", headers=sam, json={"slug": "Bad Slug!", "name": "x"}
        ).status_code
        == 422
    )
    kb = api.post(
        "/api/v1/knowledge-bases",
        headers=sam,
        json={"slug": slug, "name": "Stage5 测试库", "visibility": "restricted"},
    )
    assert kb.status_code == 201
    kb_id = kb.json()["id"]
    assert (
        api.post(
            "/api/v1/knowledge-bases", headers=sam, json={"slug": slug, "name": "dup"}
        ).status_code
        == 409
    )
    users = {
        u["email"].split("@")[0]: u["id"]
        for u in api.get("/api/v1/users/directory", headers=sam).json()
    }
    assert all(kb_id != k["id"] for k in api.get("/api/v1/knowledge-bases", headers=bob).json())
    assert api.get(f"/api/v1/knowledge-bases/{kb_id}/permissions", headers=bob).status_code == 403
    put = api.put(
        f"/api/v1/knowledge-bases/{kb_id}/permissions",
        headers=sam,
        json={
            "visibility": "restricted",
            "grants": [{"user_id": users["bob"], "permission": "read"}],
        },
    )
    assert put.status_code == 200 and [g["permission"] for g in put.json()["grants"]] == ["read"]
    assert any(kb_id == k["id"] for k in api.get("/api/v1/knowledge-bases", headers=bob).json())
    assert all(kb_id != k["id"] for k in api.get("/api/v1/knowledge-bases", headers=alice).json())
    # upload into the isolated KB, check chunks + search, then delete only that document
    tag = f"S5UNIQ{uuid.uuid4().hex[:6].upper()}"
    up = api.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        headers=sam,
        files={
            "file": (
                "s5.md",
                f"# Stage5\n\n{tag} 是阶段五测试文档的唯一标记。".encode(),
                "text/markdown",
            )
        },
    )
    assert up.status_code in (200, 201), up.text
    doc_id = up.json()["document"]["id"]
    ch = api.get(f"/api/v1/documents/{doc_id}/chunks", headers=bob).json()
    assert ch["total"] >= 1
    hits = api.post("/api/v1/search", headers=bob, json={"query": tag, "top_k": 3}).json()
    assert any(tag in (h["content"]) for h in hits["candidates"])
    assert api.delete(f"/api/v1/documents/{doc_id}", headers=bob).status_code in (403, 404)
    assert api.delete(f"/api/v1/documents/{doc_id}", headers=sam).status_code == 200
    # invalid grants
    bad = api.put(
        f"/api/v1/knowledge-bases/{kb_id}/permissions",
        headers=admin,
        json={
            "visibility": "public",
            "grants": [{"user_id": str(uuid.uuid4()), "permission": "read"}],
        },
    )
    assert bad.status_code == 404
    seeded = {k["slug"] for k in api.get("/api/v1/knowledge-bases", headers=admin).json()}
    assert {"order-platform", "public-faq", "hr-confidential"} <= seeded  # corpus untouched


def test_eval_results_rows_are_not_pooled(api) -> None:
    h = login(api, "alice")
    j = api.get("/api/v1/evals/results", headers=h).json()
    assert j["available"] and j["rows"]
    kinds = {r["kind"] for r in j["rows"]}
    assert kinds == {"rag", "agent"}
    agent = [r for r in j["rows"] if r["kind"] == "agent"]
    assert any(r["is_mock"] for r in agent) and any(not r["is_mock"] for r in agent)
    rag = [r for r in j["rows"] if r["kind"] == "rag"]
    assert all("recall_at_5" in r["metrics"] for r in rag)
    assert all(r["metrics"]["recall_at_5"] is None for r in agent)  # unavailable, not 0
    assert "bench_local_models.json" in j["skipped_files"]
