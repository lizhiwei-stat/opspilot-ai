"""Stage 5 real-browser E2E: full business flow on the enterprise web platform.

admin creates an isolated KB + uploads a unique doc -> ordinary user asks in the Agent
workspace (real retrieval + MCP tools, SSE node events) -> ticket draft -> admin approves in
the ticket center -> support engineer moves the ticket -> dashboard reflects the change.
Also: form validation, wrong password, refresh persistence, SSE reconnect (forced drop +
Last-Event-ID replay), denied pages/APIs, repeated login without spurious 401, console
errors, destructive-action confirmation, screenshots.

Usage (passwords from env, never on the command line):
  OPSPILOT_E2E_ADMIN_PASSWORD=... OPSPILOT_E2E_USER_PASSWORD=... \
  uv run --with playwright==1.64.1 python frontend/e2e/stage5_e2e.py --url http://localhost:5173 \
      [--channel msedge] [--screenshots DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page, Route, expect, sync_playwright

T = 30_000


class Run:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.checks: dict[str, Any] = {}
        self.console: list[str] = []
        self.unexpected_401: list[str] = []
        self.expected_fail: set[str] = set()

    def shot(self, page: Page, name: str) -> None:
        if self.a.screenshots:
            Path(self.a.screenshots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(self.a.screenshots) / name), full_page=True)

    def page(self, browser: Browser) -> Page:
        ctx = browser.new_context(viewport={"width": 1440, "height": 960}, locale="zh-CN")
        p = ctx.new_page()
        p.on("console", lambda m: m.type == "error" and self.console.append(m.text[:300]))
        p.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"[:300]))

        def on_resp(r: Any) -> None:
            if r.status == 401 and "/auth/login" not in r.url:
                self.unexpected_401.append(r.url)

        p.on("response", on_resp)
        return p

    def login(self, page: Page, email: str, pw: str) -> None:
        page.goto(self.a.url + "/")
        expect(page).to_have_url(re.compile(r"/login"), timeout=T)
        page.get_by_label("邮箱").fill(email)
        page.get_by_label("密码").fill(pw)
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.get_by_label("当前用户")).to_be_visible(timeout=T)

    def logout(self, page: Page) -> None:
        page.get_by_role("button", name="退出登录").click()
        expect(page).to_have_url(re.compile(r"/login"), timeout=T)

    def api(self, page: Page, path: str) -> tuple[int, Any]:
        r = page.evaluate(
            """async (p) => { const t = sessionStorage.getItem('opspilot.token');
              const r = await fetch(p, {headers: {Authorization: 'Bearer ' + t}});
              let b = null; try { b = await r.json(); } catch {} return [r.status, b]; }""",
            path,
        )
        return r[0], r[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5173")
    ap.add_argument("--channel", default=None)
    ap.add_argument("--screenshots")
    a = ap.parse_args()
    a.url = a.url.rstrip("/")
    apw = os.environ.get("OPSPILOT_E2E_ADMIN_PASSWORD")
    upw = os.environ.get("OPSPILOT_E2E_USER_PASSWORD")
    if not apw or not upw:
        print("OPSPILOT_E2E_ADMIN_PASSWORD / OPSPILOT_E2E_USER_PASSWORD not set", file=sys.stderr)
        return 2
    R = Run(a)
    tag = f"S5E2E{uuid.uuid4().hex[:6].upper()}"
    slug = f"e2e-s5-{tag[-6:].lower()}"
    code = f"PGW-{uuid.uuid4().int % 90000 + 10000}"  # unique error code -> KB lookup via MCP
    t0 = time.perf_counter()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel=a.channel)
        page = R.page(browser)

        # ---- unauthenticated redirect + login form validation + wrong password
        page.goto(a.url + "/tickets")
        expect(page).to_have_url(re.compile(r"/login\?next=%2Ftickets"), timeout=T)
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.get_by_text("请输入有效邮箱")).to_be_visible()
        expect(page.get_by_text("请输入密码")).to_be_visible()
        page.get_by_label("邮箱").fill("admin@opspilot.local")
        page.get_by_label("密码").fill("definitely-wrong-password-1")
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.get_by_text("邮箱或密码错误")).to_be_visible(timeout=T)
        R.checks["login_validation"] = "empty-form errors + wrong password message"
        R.shot(page, "s5_login.png")

        # ---- admin: dashboard baseline
        R.login(page, "admin@opspilot.local", apw)
        expect(page.get_by_label("stat-工单总数")).to_be_visible(timeout=T)
        _, d0 = R.api(page, "/api/v1/dashboard/summary")
        expect(page.get_by_label("stat-工单总数")).to_contain_text(str(d0["tickets"]["total"]))
        R.checks["dashboard_baseline"] = {
            "tickets_total": d0["tickets"]["total"],
            "runs_total": d0["agent_runs"]["total"],
        }
        R.shot(page, "s5_dashboard_before.png")

        # ---- admin: create isolated KB (public) and upload a unique doc
        page.get_by_role("link", name="知识库").click()
        page.get_by_role("tab", name="知识库与权限").click()
        form = page.get_by_label("create-kb")
        form.get_by_role("button", name="创建").click()
        expect(form.get_by_text("2-63 位小写字母")).to_be_visible()
        form.get_by_label("标识").fill(slug)
        form.get_by_label("名称").fill(f"Stage5 E2E {tag}")
        form.get_by_label("可见性").select_option("public")
        form.get_by_role("button", name="创建").click()
        expect(form).to_contain_text(f"已创建 {slug}", timeout=T)
        perms = page.get_by_label("kb-permissions")
        expect(perms).to_contain_text(slug, timeout=T)
        R.shot(page, "s5_kb_permissions.png")
        page.get_by_role("tab", name="文档管理").click()
        page.get_by_label("kb-list").get_by_role("button", name=re.compile(tag)).click()
        doc = (
            f"# {tag} 支付网关熔断处置手册\n\n"
            f"错误码 {code}（{tag}）：支付网关 payment-gateway 熔断后下单失败。"
            "处置：检查熔断器状态，回滚最近发布，并通知值班。\n"
        )
        page.get_by_label("上传文件").set_input_files(
            {"name": f"{tag}.md", "mimeType": "text/markdown", "buffer": doc.encode()}
        )
        docs = page.get_by_label("documents")
        expect(docs).to_contain_text(f"{tag}.md", timeout=T)
        expect(docs).to_contain_text("已索引", timeout=T)
        R.checks["admin_upload"] = {"kb": slug, "doc": f"{tag}.md", "status": "已索引"}
        R.shot(page, "s5_knowledge_docs.png")

        # ---- search test hits the new doc
        page.get_by_role("tab", name="检索测试").click()
        page.get_by_label("查询").fill(tag)
        page.get_by_role("button", name="检索", exact=True).click()
        expect(page.get_by_label("results-keyword", exact=True)).to_contain_text(
            f"{tag}.md", timeout=T
        )
        R.checks["search_test"] = "keyword mode returns the uploaded doc"
        R.logout(page)

        # ---- ordinary user: workspace, SSE with a forced drop + Last-Event-ID replay
        R.login(page, "alice@opspilot.local", upw)
        page.get_by_role("link", name="智能体工作台").click()
        page.get_by_role("button", name="发送").click()
        expect(page.get_by_text("请输入问题")).to_be_visible()
        seen_ids: list[str] = []
        state = {"n": 0}

        def cut_first(route: Route) -> None:
            state["n"] += 1
            seen_ids.append(route.request.headers.get("last-event-id", ""))
            if state["n"] == 1:
                resp = route.fetch()
                body = resp.text()
                blocks = [b for b in body.split("\n\n") if b.startswith("id: ")]
                # deliver only the first two events, then close without `end` (simulated drop)
                route.fulfill(
                    status=200,
                    headers={"content-type": "text/event-stream"},
                    body="retry: 300\n\n" + "\n\n".join(blocks[:2]) + "\n\n",
                )
            else:
                route.continue_()

        page.route("**/api/v1/agent/runs/*/events", cut_first)
        q = f"{code} 支付网关熔断导致下单失败，请分析日志并帮我创建一个工单"
        page.get_by_label("输入问题").fill(q)
        page.get_by_role("button", name="发送").click()
        expect(page.get_by_label("stream-state")).to_contain_text("已结束", timeout=60_000)
        page.unroute("**/api/v1/agent/runs/*/events")
        seqs = [
            int(x)
            for x in page.get_by_label("事件列表")
            .locator("li")
            .evaluate_all("els => els.map(e => e.dataset.seq)")
        ]
        assert seqs == list(range(1, len(seqs) + 1)), seqs
        assert seen_ids[:2] == ["0", "2"], seen_ids
        expect(page.get_by_label("stream-state")).to_contain_text("已重连 1 次")
        R.checks["sse_reconnect"] = {
            "requests_last_event_id": seen_ids,
            "events": len(seqs),
            "gap_free_no_dupes": True,
        }
        thread_url = page.url
        msg = page.get_by_label("消息")
        expect(msg).to_contain_text("待审批", timeout=T)
        expect(page.get_by_label("待审批草稿")).to_contain_text("等待系统管理员审批")
        details = page.get_by_label("workspace-details")
        expect(details.get_by_label("evidence")).to_contain_text(code, timeout=T)
        tools = details.get_by_label("tool-trace")
        expect(tools).to_contain_text("search_knowledge")
        expect(tools).to_contain_text("create_ticket_draft")
        graph = page.get_by_label("workspace-graph")
        expect(graph.locator('[data-node="approval"]')).to_have_attribute("data-state", "waiting")
        for t in ("query_system_logs", "get_service_status", "analyze_error_code"):
            expect(tools).to_contain_text(t)
        R.checks["user_agent_run"] = {
            "question_code": code,
            "evidence_from_uploaded_doc": True,
            "mcp_tools": [
                "search_knowledge",
                "analyze_error_code",
                "query_system_logs",
                "get_service_status",
                "create_ticket_draft",
            ],
            "status": "awaiting_approval",
        }
        R.shot(page, "s5_workspace_pending.png")

        # ---- refresh persistence (session + thread history survive reload)
        page.reload()
        expect(page.get_by_label("当前用户")).to_contain_text("Alice", timeout=T)
        expect(page.get_by_label("消息")).to_contain_text(code, timeout=T)
        assert page.url == thread_url
        R.checks["refresh_persistence"] = "token in sessionStorage, thread reloaded from API"

        # ---- denied page + denied API for an ordinary user
        page.goto(a.url + "/admin/users")
        expect(page.get_by_label("forbidden")).to_be_visible(timeout=T)
        st, _ = R.api(page, "/api/v1/admin/users")
        R.expected_fail.add("/api/v1/admin/users")
        assert st == 403, st
        expect(page.get_by_role("link", name="用户与权限")).to_have_count(0)
        R.checks["denied_auth"] = {"page": "forbidden view", "api_status": st}
        R.logout(page)

        # ---- admin approves the draft in the ticket center
        R.login(page, "admin@opspilot.local", apw)
        page.get_by_role("link", name="工单中心").click()
        page.get_by_role("tab", name="草稿与审批").click()
        item = page.get_by_label(re.compile(f"草稿 .*{code}")).first
        expect(item).to_be_visible(timeout=T)
        R.shot(page, "s5_drafts_pending.png")
        item.get_by_role("button", name="拒绝").click()
        expect(page.get_by_role("alertdialog")).to_be_visible()
        page.get_by_role("button", name="取消").click()
        item.get_by_label("审批意见").fill("同意，按手册处置")
        item.get_by_role("button", name="批准").click()
        res = page.get_by_label("审批结果")
        expect(res).to_contain_text("已批准并创建工单", timeout=60_000)
        number = re.search(r"TCK-\d{4}", res.inner_text()).group(0)  # type: ignore[union-attr]
        R.checks["admin_approval"] = {"ticket": number, "reject_confirm_dialog": True}
        R.logout(page)

        # ---- support engineer moves the ticket (form validation + transition)
        R.login(page, "sam@opspilot.local", upw)
        page.goto(f"{a.url}/tickets/{number}")
        det = page.get_by_label("ticket-detail")
        expect(det.get_by_label("ticket-events")).to_contain_text("审批", timeout=T)
        form = det.get_by_role("form", name="状态流转")
        form.get_by_role("button", name="提交流转").click()
        expect(form.get_by_text("请选择目标状态")).to_be_visible()
        form.get_by_label("目标状态").select_option("in_progress")
        form.get_by_label("处理说明").fill("技术支持已接手，正在回滚")
        form.get_by_role("button", name="提交流转").click()
        expect(det.get_by_label("ticket-events")).to_contain_text("处理中", timeout=T)
        expect(det.get_by_label("ticket-events")).to_contain_text("Sam")
        R.checks["support_transition"] = f"{number}: open -> in_progress by support"
        R.shot(page, "s5_ticket_detail.png")
        page.get_by_role("link", name="工单中心").click()
        R.shot(page, "s5_tickets_list.png")
        R.logout(page)

        # ---- admin: dashboard reflects it; repeated login must not cause 401s
        before_401 = len(R.unexpected_401)
        R.login(page, "admin@opspilot.local", apw)
        expect(page.get_by_label("stat-工单总数")).to_contain_text(
            str(d0["tickets"]["total"] + 1), timeout=T
        )
        _, d1 = R.api(page, "/api/v1/dashboard/summary")
        assert d1["tickets"]["total"] == d0["tickets"]["total"] + 1, (d0, d1)
        assert d1["agent_runs"]["total"] >= d0["agent_runs"]["total"] + 1
        R.checks["dashboard_after"] = {
            "tickets_total": d1["tickets"]["total"],
            "runs_total": d1["agent_runs"]["total"],
            "in_progress": d1["tickets"]["by_status"].get("in_progress"),
        }
        R.shot(page, "s5_dashboard_after.png")
        R.logout(page)
        R.login(page, "admin@opspilot.local", apw)
        page.get_by_role("link", name="执行可视化").click()
        page.get_by_label("run-list").get_by_role("link").first.click()
        expect(page.get_by_label("exec-graph")).to_be_visible(timeout=T)
        expect(page.get_by_label("exec-events")).to_contain_text("final", timeout=T)
        R.shot(page, "s5_executions.png")
        assert len(R.unexpected_401) == before_401, R.unexpected_401
        R.checks["repeated_login_no_401"] = True

        # ---- remaining pages: evals, admin users, settings
        page.get_by_role("link", name="评测与监控").click()
        expect(page.get_by_label("eval-group").first).to_be_visible(timeout=T)
        R.shot(page, "s5_evals.png")
        page.get_by_role("link", name="用户与权限").click()
        expect(page.get_by_label("user-table")).to_contain_text("sam@opspilot.local", timeout=T)
        R.shot(page, "s5_admin_users.png")
        page.get_by_role("link", name="系统设置").click()
        expect(page.get_by_label("settings-health")).to_be_visible(timeout=T)
        assert "sk-" not in page.content()
        R.shot(page, "s5_settings.png")

        # ---- cleanup our own uploaded doc via the confirm dialog (destructive action)
        page.get_by_role("link", name="知识库").click()
        page.get_by_label("kb-list").get_by_role("button", name=re.compile(tag)).click()
        row = page.get_by_label("documents").locator("tr", has_text=f"{tag}.md")
        row.get_by_role("button", name="删除").click()
        dlg = page.get_by_role("alertdialog")
        expect(dlg).to_contain_text("删除文档")
        dlg.get_by_role("button", name="删除").click()
        expect(page.get_by_label("documents")).to_contain_text("已删除", timeout=T)
        R.checks["destructive_confirm_cleanup"] = f"{tag}.md deleted after confirm"
        browser.close()

    allowed = ("401", "403", "Failed to load resource")
    real_errors = [c for c in R.console if not any(x in c for x in allowed)]
    R.checks["console_errors"] = {"total": len(R.console), "unexpected": real_errors}
    ok = not real_errors
    print(
        json.dumps(
            {"ok": ok, "elapsed_s": round(time.perf_counter() - t0, 1), "checks": R.checks},
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
