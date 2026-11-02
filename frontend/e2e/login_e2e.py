"""Real-browser login E2E (Playwright for Python, headless Chromium).

Runs against a live stack (default http://localhost:5173). Fills the login form, submits,
and asserts the logged-in panel shows the user/role returned by the real /api/v1/auth/me.

Usage:
  uv run --with playwright==1.64.1 python frontend/e2e/login_e2e.py \
      --url http://localhost:5173 --email admin@opspilot.local --expect-role admin \
      [--screenshot docs/assets/e2e-login.png]
Password is read from env OPSPILOT_E2E_PASSWORD (never passed on the command line).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROLE_LABEL = {"admin": "管理员", "user": "普通用户"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5173")
    ap.add_argument("--email", required=True)
    ap.add_argument("--expect-role", choices=["admin", "user"], required=True)
    ap.add_argument("--screenshot")
    ap.add_argument("--channel", default=None, help="e.g. chrome / msedge to use a system browser")
    args = ap.parse_args()
    password = os.environ.get("OPSPILOT_E2E_PASSWORD")
    if not password:
        print("OPSPILOT_E2E_PASSWORD is not set", file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=args.channel)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(args.url)
        page.get_by_role("button", name="登录").first.wait_for()
        page.get_by_label("邮箱").fill(args.email)
        page.get_by_label("密码").fill(password)
        with page.expect_response("**/api/v1/auth/me") as me_resp:
            page.get_by_role("button", name="登录", exact=True).click()
        me = me_resp.value.json()
        panel = page.get_by_label("auth")
        expect(panel).to_contain_text("已登录", timeout=10_000)
        expect(panel).to_contain_text(me["email"])
        expect(panel).to_contain_text(ROLE_LABEL[args.expect_role])
        token = page.evaluate("() => sessionStorage.getItem('opspilot.token')")
        # Cross-check the UI against the real API with the browser's own token.
        req = urllib.request.Request(
            f"{args.url}/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            api_me = json.load(r)
        assert api_me == me, (api_me, me)
        assert me["role"] == args.expect_role, me
        page.reload()
        expect(page.get_by_label("auth")).to_contain_text(me["email"], timeout=10_000)
        if args.screenshot:
            page.screenshot(path=args.screenshot, full_page=True)
        browser.close()
    print(
        json.dumps(
            {
                "result": "PASS",
                "email": me["email"],
                "role": me["role"],
                "ui_role_label": ROLE_LABEL[args.expect_role],
                "survives_reload": True,
                "elapsed_s": round(time.perf_counter() - t0, 2),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
