"""Real-browser Agent workbench E2E (Stage 3): real UI -> real API -> LangGraph (mock model).

  alice: ORD-5003 (ReAct tools) -> h029 (refusal) -> follow-up turn -> ticket draft (awaiting)
  admin: own ticket draft -> approve in the UI -> draft approved

Usage: uv run --with playwright==1.64.1 python frontend/e2e/agent_e2e.py --url http://localhost:5173
       [--screenshots DIR] [--channel msedge]
Passwords: OPSPILOT_E2E_USER_PASSWORD / OPSPILOT_E2E_ADMIN_PASSWORD (never printed).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import Browser, Page, expect, sync_playwright


def shot(page: Page, folder: str | None, name: str) -> None:
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / name), full_page=True)


def login(browser: Browser, url: str, email: str, pw: str) -> Page:
    page = browser.new_context(viewport={"width": 1440, "height": 1000}).new_page()
    page.goto(url)
    page.get_by_label("邮箱").fill(email)
    page.get_by_label("密码").fill(pw)
    page.get_by_role("button", name="登录", exact=True).click()
    expect(page.get_by_label("auth")).to_contain_text("已登录", timeout=10_000)
    expect(page.locator("header")).to_contain_text("Stage 3")
    page.get_by_role("link", name="Agent").click()
    return page


def ask(page: Page, q: str, button: str = "运行") -> None:
    page.get_by_label("Agent 问题").fill(q)
    page.get_by_role("button", name=button).click()
    expect(page.get_by_role("button", name=button)).to_be_enabled(timeout=30_000)


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
        result = page.get_by_label("agent-result")

        ask(page, "ORD-5003 是什么错误，怎么处理")
        expect(result).to_contain_text("已完成", timeout=30_000)
        expect(result).to_contain_text("Mock 智能体")
        trace = page.get_by_label("node-trace")
        for n in (
            "guard_in",
            "retrieve",
            "assess",
            "agent",
            "tools",
            "collect",
            "generate",
            "verify",
            "respond",
        ):
            expect(trace).to_contain_text(n)
        tools = page.get_by_label("tool-calls")
        expect(tools).to_contain_text("analyze_error_code")
        expect(tools).to_contain_text("query_mock_logs")
        expect(page.get_by_label("引用")).to_be_visible()
        checks["react_tools"] = "analyze_error_code + query_mock_logs, cited answer, mock label"
        shot(page, a.screenshots, "stage3_agent_tools.png")

        page.get_by_role("button", name="新会话").click()
        ask(page, "支付网关的商户号是多少")
        expect(result).to_contain_text("已拒答", timeout=30_000)
        expect(result).to_contain_text("value_slot_empty")
        checks["h029_refused"] = True
        shot(page, a.screenshots, "stage3_agent_refusal.png")

        page.get_by_role("button", name="新会话").click()
        ask(page, "查一下工单 TCK-0001 的状态")
        ask(page, "它现在是什么优先级", button="继续追问")
        expect(result).to_contain_text("第 2 轮结果", timeout=30_000)
        expect(page.get_by_label("最终回答")).to_contain_text("P2")
        checks["multi_turn"] = "turn 2 resolved TCK-0001 from thread memory"
        shot(page, a.screenshots, "stage3_agent_multiturn.png")

        page.get_by_role("button", name="新会话").click()
        ask(page, "订单接口 500 了，帮我创建一个工单")
        expect(result).to_contain_text("等待审批", timeout=30_000)
        expect(page.get_by_label("approval")).to_contain_text("需要管理员审批")
        checks["user_cannot_approve"] = True

        adm = login(browser, a.url, "admin@opspilot.local", apw)
        ask(adm, "库存同步延迟，帮我提一个工单")
        expect(adm.get_by_label("approval")).to_be_visible(timeout=30_000)
        shot(adm, a.screenshots, "stage3_agent_approval_pending.png")
        adm.get_by_role("button", name="批准").click()
        expect(adm.get_by_label("draft-status")).to_contain_text("approved", timeout=30_000)
        expect(adm.get_by_label("node-trace")).to_contain_text("apply_approval")
        checks["admin_approve_resume"] = "interrupt -> approve -> apply_approval -> draft approved"
        shot(adm, a.screenshots, "stage3_agent_approved.png")
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
