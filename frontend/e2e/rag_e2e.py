"""Real-browser RAG E2E: knowledge base -> chunk viewer -> 4-mode search -> QA -> evals.

Runs against a live stack whose DB has the demo corpus loaded (scripts/load_demo_corpus.py)
and at least one eval run (evals/run_retrieval_eval.py). Logs in through the real UI.

Usage:
  uv run --with playwright==1.64.1 python frontend/e2e/rag_e2e.py --url http://localhost:5173 \
      --email alice@opspilot.local [--screenshots docs/assets] [--channel msedge]
Password from env OPSPILOT_E2E_PASSWORD.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


def shot(page: Page, folder: str | None, name: str) -> None:
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / name), full_page=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5173")
    ap.add_argument("--email", default="alice@opspilot.local")
    ap.add_argument("--screenshots")
    ap.add_argument("--channel", default=None)
    args = ap.parse_args()
    password = os.environ.get("OPSPILOT_E2E_PASSWORD")
    if not password:
        print("OPSPILOT_E2E_PASSWORD is not set", file=sys.stderr)
        return 2
    t0 = time.perf_counter()
    checks: dict[str, object] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=args.channel)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(args.url)
        page.get_by_label("邮箱").fill(args.email)
        page.get_by_label("密码").fill(password)
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.get_by_label("auth")).to_contain_text("已登录", timeout=10_000)

        # knowledge bases: alice sees order-platform + public-faq, never hr-confidential
        page.get_by_role("link", name="知识库").click()
        kb_list = page.get_by_label("kb-list")
        expect(kb_list).to_contain_text("订单平台技术文档", timeout=10_000)
        expect(kb_list).to_contain_text("公共 FAQ")
        expect(kb_list).not_to_contain_text("人力资源")
        checks["kb_acl"] = "order-platform+public-faq visible, hr-confidential hidden"
        docs = page.get_by_label("documents")
        expect(docs).to_contain_text("deployment-rollback-guide.pdf")
        expect(docs).to_contain_text("只读")  # alice has read-only grant
        docs.get_by_role("button", name="deployment-rollback-guide.pdf").click()
        viewer = page.get_by_label("chunk-viewer")
        expect(viewer).to_contain_text("第 2 页", timeout=10_000)
        expect(viewer).to_contain_text("kubectl rollout undo")
        checks["chunk_viewer"] = "pdf page numbers + content shown"
        shot(page, args.screenshots, "stage2-knowledge.png")

        # retrieval debug: all four modes
        page.get_by_role("link", name="检索调试").click()
        page.get_by_label("查询").fill("ERR_PAY_TIMEOUT 之后怎么重试")
        page.get_by_role("button", name="检索", exact=True).click()
        for m in ("vector", "keyword", "hybrid", "hybrid_rerank"):
            card = page.get_by_label(f"results-{m}", exact=True)
            expect(card).to_contain_text("#1", timeout=10_000)
        expect(page.get_by_role("note")).to_contain_text("没有语义理解")
        checks["search_modes"] = 4
        shot(page, args.screenshots, "stage2-search-debug.png")

        # grounded QA with citations, then a no-answer question
        page.get_by_role("link", name="问答").click()
        page.get_by_label("问题").fill("ORD-5003 是什么错误，怎么处理？")
        with page.expect_response("**/api/v1/qa") as qa_resp:
            page.get_by_role("button", name="提问").click()
        qa = qa_resp.value.json()
        assert qa["evidence_sufficient"] and qa["citations"], qa
        cites = page.get_by_label("citations")
        expect(cites).to_contain_text(qa["citations"][0]["chunk_id"])
        expect(page.get_by_label("answer")).to_contain_text("生成器：mock-extractive")
        checks["qa_citations"] = [c["document_name"] for c in qa["citations"]]
        shot(page, args.screenshots, "stage2-qa.png")
        page.get_by_label("问题").fill("值班补贴一周多少钱？")
        with page.expect_response("**/api/v1/qa") as qa2:
            page.get_by_role("button", name="提问").click()
        assert not qa2.value.json()["evidence_sufficient"]
        expect(page.get_by_label("answer")).to_contain_text("证据不足")
        checks["qa_no_permission"] = "证据不足 (hr doc not visible to alice)"

        # evals page reads saved runs
        page.get_by_role("link", name="评测").click()
        ev = page.get_by_label("evals")
        expect(ev).to_contain_text("hybrid_rerank", timeout=10_000)
        expect(ev).to_contain_text("无语义")
        # Stage 2.5: mock vs real side-by-side matrix
        mx = page.get_by_label("eval-matrix")
        expect(mx).to_contain_text("bge-small-zh-v1.5", timeout=10_000)
        expect(mx).to_contain_text("heldout")
        shot(page, args.screenshots, "stage25-evals-matrix.png")
        ev.get_by_text("hybrid_rerank").first.click()
        # latest run may be dev (q001) or held-out (h001)
        expect(page.get_by_label("eval-cases")).to_contain_text(
            re.compile(r"[qh]001"), timeout=10_000
        )
        checks["evals_page"] = "mock-vs-real matrix + runs + per-case drilldown"
        shot(page, args.screenshots, "stage25-evals-cases.png")
        browser.close()
    print(
        json.dumps(
            {
                "result": "PASS",
                "user": args.email,
                "checks": checks,
                "elapsed_s": round(time.perf_counter() - t0, 2),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
