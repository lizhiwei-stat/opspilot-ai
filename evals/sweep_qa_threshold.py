"""Tune the QA evidence threshold on the DEV split only (held-out is refused).

Retrieval is computed once per query (memoised), then the *real* QAService.answer runs for
every threshold, so the decision logic (threshold + anchor check + quote/policy checks) is
exactly production's. Output: evals/results/<ts>_sweep_<embedding>_<reranker>.json/.md

    HF_HUB_OFFLINE=1 uv run --group local-embed python evals/sweep_qa_threshold.py \
        --embedding local --reranker cross_encoder
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_retrieval_eval as ev
from sqlalchemy import select
from sqlalchemy.orm import Session

GRIDS = {
    "lexical": [0.2, 0.25, 0.3, 0.33, 0.35, 0.4, 0.45, 0.5],
    "reranker": [0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedding", choices=["mock", "local"], default="local")
    ap.add_argument("--reranker", choices=["rule", "cross_encoder", "mock"], default="rule")
    ap.add_argument("--dataset", default="dev", choices=["dev"], help="dev only, by design")
    ap.add_argument("--anchor", choices=["on", "off"], default="on")
    args = ap.parse_args()
    signal = "reranker" if args.reranker == "cross_encoder" else "lexical"
    base = ev.get_settings()
    base = ev.Settings(
        **{
            **base.model_dump(),
            "embedding_provider": args.embedding,
            "qa_evidence": signal,
            "qa_require_anchor_terms": args.anchor == "on",
        }
    )
    cases = [json.loads(x) for x in ev.DATASETS["dev"].read_text("utf-8").splitlines() if x]
    embedder = ev.build_embedding(base)
    rr = ev.build_reranker(base, args.reranker)
    llm = ev.build_llm(base)
    engine = ev.build_engine(base)
    with Session(engine) as s, s.begin():
        ev.ensure_index(s, base, embedder)
    cache: dict = {}
    rows = []
    with Session(engine) as s:
        users = {
            k: s.scalar(select(ev.User).where(ev.User.email == v)) for k, v in ev.USERS.items()
        }
        for thr in GRIDS[signal]:
            key = "qa_min_rerank_score" if signal == "reranker" else "qa_min_evidence"
            st = ev.Settings(**{**base.model_dump(), key: thr})
            qa = ev.QAService(s, st, embedder, llm, rr)
            orig = qa.retrieval.search

            def memo(user, q, _o=orig, **kw):
                k = (user.id, q)
                if k not in cache:
                    cache[k] = _o(user, q, **kw)
                return cache[k]

            qa.retrieval.search = memo
            res = []
            for c in cases:
                a = qa.answer(users[c["user"]], c["query"])
                res.append(
                    {
                        "case_id": c["id"],
                        "category": c["category"],
                        "expect_answer": c["expect_answer"],
                        "answered": a.evidence_sufficient,
                        "evidence_score": a.evidence_score,
                        "refusal_reason": a.refusal_reason,
                    }
                )
            na = [r for r in res if not r["expect_answer"]]
            an = [r for r in res if r["expect_answer"]]
            rows.append(
                {
                    "threshold": thr,
                    "refusal_rate": round(sum(not r["answered"] for r in na) / len(na), 3),
                    "answer_rate": round(sum(r["answered"] for r in an) / len(an), 3),
                    "accuracy": round(
                        sum(r["answered"] == r["expect_answer"] for r in res) / len(res), 4
                    ),
                    "false_accepts": [r["case_id"] for r in na if r["answered"]],
                    "false_refusals": [r["case_id"] for r in an if not r["answered"]],
                    "cases": res,
                }
            )
    # selection rule fixed in advance: max accuracy, tie -> higher refusal rate -> higher thr
    best = max(rows, key=lambda r: (r["accuracy"], r["refusal_rate"], r["threshold"]))
    out = {
        "meta": {
            "embedding": args.embedding,
            "reranker": args.reranker,
            "signal": signal,
            "anchor": args.anchor,
            "dataset": "dev",
            "grid": GRIDS[signal],
            "selection_rule": "max accuracy; tie -> higher refusal; tie -> higher thr",
            "commit": ev.git_commit(),
            "started_at": datetime.now(UTC).isoformat(),
        },
        "selected": best["threshold"],
        "rows": rows,
    }
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = ev.RESULTS / f"{ts}_sweep_{args.embedding}_{args.reranker}_anchor{args.anchor}"
    stem.with_suffix(".json").write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    md = [
        f"# QA 阈值扫描（仅 dev）：{args.embedding} + {args.reranker}（信号 {signal}，"
        f"anchor={args.anchor}）",
        "",
        "| 阈值 | 正确拒答率 | 作答率 | 准确率 | 误答 | 误拒 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        md.append(
            f"| {r['threshold']} | {r['refusal_rate']} | {r['answer_rate']} | "
            f"{r['accuracy']} | {','.join(r['false_accepts']) or '-'} | "
            f"{','.join(r['false_refusals']) or '-'} |"
        )
    md += ["", f"选定阈值（规则：{out['meta']['selection_rule']}）：**{best['threshold']}**"]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n", "utf-8")
    print("\n".join(md))
    print("written:", stem.with_suffix(".json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
