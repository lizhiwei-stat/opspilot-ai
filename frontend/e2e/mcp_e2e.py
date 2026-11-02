"""Real-browser Stage 4 E2E: real UI -> real API -> LangGraph -> REAL MCP servers (stdio
subprocesses of the backend) -> Postgres. Mock model only (no paid API).

  alice: "ORD-5003 错误，查文档和日志，分析并创建待审核工单" -> incident_ticket pipeline ->
         awaiting approval (no approve buttons for a normal user)
  admin: 待审批队列 -> open alice's run -> 退回修改 (comment) -> draft v2 -> 批准 ->
         ticket created via Ticket MCP -> ticket + status events -> MCP audit table

Usage: uv run --with playwright==1.64.1 python frontend/e2e/mcp_e2e.py --url http://localhost:5173
       [--screenshots DIR] [--channel msedge]
Passwords: OPSPILOT_E2E_USER_PASSWORD / OPSPILOT_E2E_ADMIN_PASSWORD (never printed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from agent_e2e import ask, login, shot
from playwright.sync_api import expect, sync_playwright

Q = "ORD-5003 错误，查文档和日志，分析并创建待审核工单"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5173")
    ap.add_argument("--screenshots")
    ap.add_argument("--channel", default=None)
    a = ap.parse_args()
    upw, apw = (
        os.environ.get("OPSPILOT_E2E_USER_PASSWORD"),
        os.environ.get("OPSPILOT_E2E_ADMIN_PASSWORD"),
    )
    if not upw or not apw:
        print("passwords not set", file=sys.stderr)
        return 2
    t0 = time.perf_counter()
    checks: dict[str, object] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=a.channel)
        page = login(browser, a.url, "alice@opspilot.local", upw)
        ask(page, Q)
        result = page.get_by_label("agent-result")
        expect(result).to_contain_text("等待审批", timeout=60_000)
        expect(result).to_contain_text("incident_ticket")
        trace = page.get_by_label("node-trace")
        for n in (
            "classify",
            "retrieve",
            "agent",
            "tools",
            "collect",
            "generate",
            "verify",
            "draft",
        ):
            expect(trace).to_contain_text(n)
        tools = page.get_by_label("tool-calls")
        for t in (
            "search_knowledge",
            "analyze_error_code",
            "query_system_logs",
            "get_service_status",
            "create_ticket_draft",
        ):
            expect(tools).to_contain_text(t)
        expect(page.get_by_label("approval", exact=True)).to_contain_text("需要管理员审批")
        expect(page.get_by_role("button", name="批准")).to_have_count(0)
        checks["alice_incident_pipeline"] = "KB+logs+error code+status via MCP -> draft -> awaiting"
        shot(page, a.screenshots, "stage4_incident_pending_user.png")
        run_prefix = page.get_by_label("approval", exact=True).inner_text()
        m = re.search(r"run ([0-9a-f]{8})", run_prefix)
        rid = m.group(1) if m else ""

        adm = login(browser, a.url, "admin@opspilot.local", apw)
        adm.get_by_role("button", name="刷新队列").click()
        queue = adm.get_by_label("approval-queue")
        expect(queue).to_contain_text(rid or "打开", timeout=20_000)
        adm.get_by_role("button", name=f"打开 {rid}" if rid else re.compile("打开")).first.click()
        approval = adm.get_by_label("approval", exact=True)
        expect(approval).to_contain_text("第 1 版")
        expect(adm.get_by_label("草稿内容")).to_contain_text("ORD-5003")
        expect(adm.get_by_role("button", name="退回修改")).to_be_disabled()
        shot(adm, a.screenshots, "stage4_approval_queue.png")
        adm.get_by_label("审批意见").fill("请补充影响范围与回滚方案")
        adm.get_by_role("button", name="退回修改").click()
        expect(approval).to_contain_text("第 2 版", timeout=60_000)
        expect(adm.get_by_label("tool-calls")).to_contain_text("revise_ticket_draft")
        checks["return_for_revision"] = "returned with comment -> revise_ticket_draft -> v2 pending"
        shot(adm, a.screenshots, "stage4_returned_v2.png")
        adm.get_by_label("审批意见").fill("同意")
        adm.get_by_role("button", name="批准").click()
        res = adm.get_by_label("approval-result")
        expect(res).to_contain_text("已创建工单", timeout=60_000)
        num = re.search(r"TCK-\d{4}", res.inner_text())
        checks["ticket"] = num.group(0) if num else None
        expect(adm.get_by_label("tool-calls")).to_contain_text("update_ticket_status")
        expect(adm.get_by_label("node-trace")).to_contain_text("apply_approval")
        adm.get_by_role("button", name="查看工单与状态流转").click()
        expect(adm.get_by_label("ticket-view")).to_contain_text("→ open", timeout=20_000)
        adm.get_by_role("button", name="加载审计").click()
        audit = adm.get_by_label("mcp-audit")
        expect(audit).to_contain_text("update_ticket_status", timeout=20_000)
        expect(audit).to_contain_text("revise_ticket_draft")
        expect(audit).to_contain_text("query_system_logs")
        checks["approve_business_op"] = "approved -> Ticket MCP update_ticket_status -> ticket open"
        checks["audit_rows_visible"] = True
        shot(adm, a.screenshots, "stage4_ticket_created_audit.png")
        browser.close()
    print(
        json.dumps(
            {"ok": True, "elapsed_s": round(time.perf_counter() - t0, 1), "checks": checks},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
