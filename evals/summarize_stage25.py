"""Build the Stage 2.5 comparison tables from saved result JSONs (no model, no DB).

uv run python evals/summarize_stage25.py > docs/reports/STAGE2_5_TABLES.md
"""

from __future__ import annotations

import json
from pathlib import Path

R = Path(__file__).resolve().parent / "results"


def latest(tag: str) -> dict:
    files = sorted(R.glob(f"*Z_{tag}.json"))
    return json.loads(files[-1].read_text("utf-8")) | {"_file": files[-1].name}


def f(x: object) -> str:
    return "-" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))


def main() -> None:
    runs = [
        ("mock (hash, 无语义)", "mock"),
        ("bge-small-zh-v1.5", "local"),
        ("bge-m3", "m3"),
        ("百炼 text-embedding-v4（付费，1024 维）", "dashscope"),
    ]
    print("# Stage 2.5 评测汇总表（由 evals/summarize_stage25.py 从 evals/results/*.json 生成）\n")
    for split in ("dev", "heldout"):
        print(f"## 检索：{split}（{'55 条' if split == 'dev' else '37 条冻结 held-out'}）\n")
        print(
            "| Embedding | 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 |"
            " ACL 隔离正确率 | multi_doc R@5 | paraphrase R@5 |"
        )
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for label, tag in runs:
            d = latest(f"{tag}_{split}")
            for mode, m in d["retrieval"].items():
                bc = m.get("by_category", {})
                print(
                    f"| {label} | {mode} | {f(m['recall_at_5'])} | {f(m['mrr_at_10'])} | "
                    f"{f(m['ndcg_at_10'])} | {m['latency_p50_ms']} | {m['latency_p95_ms']} | "
                    f"{m['failure_rate']} | {f(m.get('acl_isolation_correct_rate'))} | "
                    f"{f(bc.get('multi_doc', {}).get('recall_at_5'))} | "
                    f"{f(bc.get('paraphrase', {}).get('recall_at_5'))} |"
                )
        print()
        print(f"## 问答拒答：{split}\n")
        print("| 配置 | 证据信号/阈值 | 正确拒答率 | 作答率 | 准确率 | 误答 | 误拒 | 来源文件 |")
        print("|---|---|---|---|---|---|---|---|")
        for label, tag in [
            ("Stage 2 基线（mock，lexical 0.5，无 anchor，replace）", f"before_mock_{split}"),
            ("Stage 2 基线（bge-small）", f"before_local_{split}"),
            ("当前：mock + rule（lexical 0.25 + anchor）", f"rule_mock_{split}"),
            ("当前：bge-small + rule", f"rule_local_{split}"),
            ("当前：bge-m3 + rule", f"m3_{split}"),
            ("当前：bge-small + cross-encoder（rerank 0.02 + anchor）", f"local_{split}"),
            ("当前：mock + cross-encoder", f"mock_{split}"),
            ("当前：text-embedding-v4（付费）+ cross-encoder", f"dashscope_{split}"),
        ]:
            d = latest(tag)
            q = d["qa"]
            row = d["cases"]["qa"][0]
            print(
                f"| {label} | {row['evidence_signal']} {row['threshold']} | "
                f"{f(q['no_answer_correct_refusal_rate'])} | {f(q['answerable_answer_rate'])} | "
                f"{f(q.get('accuracy'))} | {','.join(q['false_accepts']) or '-'} | "
                f"{','.join(q['false_refusals']) or '-'} | {d['_file']} |"
            )
        print()
    print("## Reranker 融合方式（修复 c）前后\n")
    print("| 数据 | reranker | fusion | Recall@5 | MRR@10 | multi_doc R@5 | 文件 |")
    print("|---|---|---|---|---|---|---|")
    for split in ("dev", "heldout"):
        for tag, rr, fu in [
            (f"before_mock_{split}", "rule", "replace"),
            (f"rule_mock_{split}", "rule", "rrf"),
            (f"before_local_{split}", "rule", "replace"),
            (f"rule_local_{split}", "rule", "rrf"),
            (f"ce_replace_local_{split}", "cross_encoder", "replace"),
            (f"local_{split}", "cross_encoder", "rrf"),
        ]:
            d = latest(tag)
            k = f"hybrid_rerank:{rr}"
            m = d["retrieval"][k]
            emb = "mock" if "mock" in tag else "bge-small"
            print(
                f"| {split}/{emb} | {rr} | {fu} | {f(m['recall_at_5'])} | {f(m['mrr_at_10'])} | "
                f"{f(m['by_category'].get('multi_doc', {}).get('recall_at_5'))} | {d['_file']} |"
            )
    print()


if __name__ == "__main__":
    main()
