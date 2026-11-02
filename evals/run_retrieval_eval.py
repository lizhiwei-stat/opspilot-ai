"""Reproducible retrieval + grounded-QA evaluation over the synthetic Chinese corpus.

    uv run python evals/run_retrieval_eval.py                              # mock (default, free)
    uv run python evals/run_retrieval_eval.py --embedding local \
        --rerankers rule,cross_encoder                                    # free local CPU models
    uv run python evals/run_retrieval_eval.py --dataset heldout ...       # frozen held-out set
    uv run python evals/run_retrieval_eval.py --embedding dashscope --i-understand-this-costs-money
        # PAID: also needs OPSPILOT_ALLOW_PAID_API=true, OPSPILOT_PAID_APPROVAL_ID, a budget cap
        # OPSPILOT_PAID_BUDGET_CNY and the key in env; every call is booked in the cost ledger.

Steps: seed + ingest corpus (idempotent) -> build the selected model's isolated index ->
verify every gold phrase exists -> run every query in every mode as its own user -> metrics
+ per-query error analysis -> evals/results/<ts>_<tag>.{json,md} + eval_runs rows.

Relevance: a chunk is relevant to gold item (doc, phrase) iff it belongs to that document
and its content contains the phrase. Recall@k counts gold *items* covered in top-k;
MRR/nDCG use chunk-level binary relevance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from load_demo_corpus import load  # noqa: E402
from opspilot.core.config import Settings, get_settings  # noqa: E402
from opspilot.db.models import (  # noqa: E402
    Chunk,
    Document,
    EvalCaseResult,
    EvalRun,
    User,
)
from opspilot.db.session import build_engine  # noqa: E402
from opspilot.llm.base import embedder_metadata  # noqa: E402
from opspilot.llm.factory import build_embedding, build_llm, build_reranker  # noqa: E402
from opspilot.services.qa import QAService  # noqa: E402
from opspilot.services.retrieval import MODES, RetrievalService  # noqa: E402
from reindex_embeddings import ensure_index  # noqa: E402

DATASETS = {
    "dev": ROOT / "evals" / "datasets" / "retrieval_zh.jsonl",
    "heldout": ROOT / "evals" / "heldout" / "retrieval_zh_heldout.jsonl",
}
HELDOUT_HASH = ROOT / "evals" / "heldout" / "FROZEN.sha256"
RESULTS = ROOT / "evals" / "results"
USERS = {
    "admin": "admin@opspilot.local",
    "alice": "alice@opspilot.local",
    "bob": "bob@opspilot.local",
}
# Expected ACL matrix, written independently of the repository code (from seed_demo.py intent).
ALLOWED_KBS = {
    "admin": {"order-platform", "public-faq", "hr-confidential"},
    "alice": {"order-platform", "public-faq"},
    "bob": {"public-faq"},
}
TOP_K = 10


@dataclass
class Gold:
    doc: str
    contains: str


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        return out.stdout.strip()[:40] + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("OPSPILOT_GIT_COMMIT")  # e.g. inside the container (no .git)


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))
    return round(s[i], 2)


def verify_gold(session: Session, cases: list[dict]) -> None:
    bad: list[str] = []
    for c in cases:
        for g in c["relevant"]:
            hit = session.scalar(
                select(Chunk.id)
                .join(Document, Document.id == Chunk.document_id)
                .where(
                    Document.source_name == g["doc"],
                    Chunk.is_active.is_(True),
                    Chunk.content.contains(g["contains"]),
                )
                .limit(1)
            )
            if hit is None:
                bad.append(f"{c['id']} phrase {g['contains']!r} not in {g['doc']}")
    if bad:
        raise SystemExit("gold check failed:\n  " + "\n  ".join(bad))


def score_case(case: dict, cands: list) -> dict:
    gold = [Gold(**g) for g in case["relevant"]]

    def rel(c) -> bool:
        return any(c.document_name == g.doc and g.contains in c.content for g in gold)

    flags = [rel(c) for c in cands[:TOP_K]]
    out: dict[str, float | None] = {"recall_at_5": None, "mrr_at_10": None, "ndcg_at_10": None}
    if gold:
        covered = sum(
            any(c.document_name == g.doc and g.contains in c.content for c in cands[:5])
            for g in gold
        )
        out["recall_at_5"] = covered / len(gold)
        first = next((i for i, f in enumerate(flags) if f), None)
        out["mrr_at_10"] = 0.0 if first is None else 1.0 / (first + 1)
        dcg = sum(1 / math.log2(i + 2) for i, f in enumerate(flags) if f)
        n_rel = max(1, min(TOP_K, sum(flags) or 1))
        idcg = sum(1 / math.log2(i + 2) for i in range(n_rel))
        out["ndcg_at_10"] = dcg / idcg
    return out


def failure_category(case: dict, cands: list) -> str:
    """Why a case failed (or 'ok'), from the gold labels only."""
    if not case["relevant"]:
        return "n/a (no gold: no_answer/no_permission)"
    gold = [Gold(**g) for g in case["relevant"]]
    top5, top10 = cands[:5], cands[:TOP_K]
    cov5 = [any(c.document_name == g.doc and g.contains in c.content for c in top5) for g in gold]
    if all(cov5):
        return "ok"
    if any(cov5):
        return "partial_multi_gold"  # some gold items in top-5, others missing
    in10 = any(c.document_name == g.doc and g.contains in c.content for c in top10 for g in gold)
    if in10:
        return "ranked_low (relevant only at 6-10)"
    if any(c.document_name in {g.doc for g in gold} for c in top10):
        return "right_doc_wrong_chunk"
    return "not_retrieved"


def parse_modes(spec: str, rerankers: list[str]) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for m in [x for x in spec.split(",") if x]:
        if m == "hybrid_rerank":
            out += [("hybrid_rerank", r) for r in rerankers]
        else:
            out.append((m, None))
    return out


def mode_key(mode: str, rr: str | None) -> str:
    return f"{mode}:{rr}" if rr else mode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedding", choices=["mock", "local", "dashscope", "real"], default="mock")
    ap.add_argument("--i-understand-this-costs-money", action="store_true")
    ap.add_argument("--dataset", choices=sorted(DATASETS), default="dev")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--rerankers", default="rule", help="comma list of rule,mock,cross_encoder")
    ap.add_argument("--qa-reranker", default=None, help="reranker used by QA (default: last)")
    ap.add_argument(
        "--qa-profile",
        choices=["current", "stage2"],
        default="current",
        help="stage2 = Stage 2 QA config (lexical evidence 0.5, no anchor check)",
    )
    ap.add_argument("--tag", default=None, help="output name suffix")
    ap.add_argument("--no-db", action="store_true", help="do not write eval_runs rows")
    ap.add_argument("--no-qa", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    emb = {"real": "dashscope"}.get(args.embedding, args.embedding)
    if emb == "dashscope":
        if not args.i_understand_this_costs_money or not settings.allow_paid_api:
            raise SystemExit(
                "dashscope embedding eval calls a PAID API: pass --i-understand-this-costs-money "
                "AND set OPSPILOT_ALLOW_PAID_API=true, OPSPILOT_PAID_APPROVAL_ID, "
                "OPSPILOT_PAID_BUDGET_CNY (and the key in env)"
            )
        provider = "openai_compatible"
    else:
        provider = emb
    overrides: dict = {"embedding_provider": provider}
    if args.qa_profile == "stage2":
        overrides |= {
            "qa_evidence": "lexical",
            "qa_min_evidence": 0.5,
            "qa_require_anchor_terms": False,
        }
    settings = Settings(**{**settings.model_dump(), **overrides})
    rerankers = [r for r in args.rerankers.split(",") if r]
    modes = parse_modes(args.modes, rerankers)
    ds_path = DATASETS[args.dataset]
    ds_sha = hashlib.sha256(ds_path.read_bytes()).hexdigest()
    if args.dataset == "heldout":
        frozen = HELDOUT_HASH.read_text().split()[0]
        if frozen != ds_sha:
            raise SystemExit(f"held-out set changed! sha256 {ds_sha} != frozen {frozen}")
    cases = [json.loads(x) for x in ds_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    t_load = time.perf_counter()
    embedder = build_embedding(settings)
    emb_load_s = time.perf_counter() - t_load
    rr_objs = {}
    rr_load = {}
    for r in rerankers:
        t0 = time.perf_counter()
        rr_objs[r] = build_reranker(settings, r)
        rr_load[r] = round(time.perf_counter() - t0, 3)
    llm = build_llm(settings)
    engine = build_engine(settings)
    started = datetime.now(UTC)
    commit = git_commit()

    with Session(engine) as s, s.begin():
        # ingest with mock (idempotent; Stage 2 data stays as is), then build THIS model's index
        load(s)
        index_info = ensure_index(s, settings, embedder)
        verify_gold(s, cases)

    retr_rows: dict[str, list[dict]] = {mode_key(m, r): [] for m, r in modes}
    qa_rows: list[dict] = []
    qa_rr = args.qa_reranker or rerankers[-1]
    with Session(engine) as s:
        users = {k: s.scalar(select(User).where(User.email == v)) for k, v in USERS.items()}
        retrs = {r: RetrievalService(s, settings, embedder, rr_objs[r]) for r in rerankers}
        base = RetrievalService(s, settings, embedder, rr_objs[rerankers[0]])
        qa = QAService(s, settings, embedder, llm, rr_objs[qa_rr])
        for m, r in modes:  # warm-up (jieba dict load, first embed, plan cache) excluded
            (retrs[r] if r else base).search(users["admin"], "预热", mode=m, top_k=TOP_K)
        for case in cases:
            user = users[case["user"]]
            for m, r in modes:
                key = mode_key(m, r)
                svc = retrs[r] if r else base
                row = {
                    "case_id": case["id"],
                    "category": case["category"],
                    "mode": key,
                    "query": case["query"],
                    "user": case["user"],
                    "ground_truth": case["relevant"],
                }
                try:
                    t0 = time.perf_counter()
                    res = svc.search(user, case["query"], mode=m, top_k=TOP_K)
                    row["latency_ms"] = (time.perf_counter() - t0) * 1000
                    row["timings_ms"] = res.timings_ms
                    row.update(score_case(case, res.candidates))
                    leaks = [
                        c for c in res.candidates if c.kb_slug not in ALLOWED_KBS[case["user"]]
                    ]
                    row["acl_leaks"] = len(leaks)
                    row["degraded"] = res.degraded
                    gold = [Gold(**g) for g in case["relevant"]]
                    row["top"] = [
                        {
                            "rank": c.rank,
                            "doc": c.document_name,
                            "chunk_id": c.chunk_id,
                            "page": c.page_number,
                            "heading": c.heading_path,
                            "score": c.score,
                            "scores": c.scores,
                            "ranks": c.ranks,
                            "relevant": any(
                                c.document_name == g.doc and g.contains in c.content for g in gold
                            ),
                            "preview": c.content[:80],
                        }
                        for c in res.candidates[:TOP_K]
                    ]
                    row["hit_at_5"] = bool(row["recall_at_5"]) if gold else None
                    row["failure_category"] = failure_category(case, res.candidates)
                    row["error"] = None
                except Exception as exc:
                    s.rollback()
                    row.update(
                        error=f"{type(exc).__name__}: {exc}",
                        latency_ms=None,
                        acl_leaks=0,
                        failure_category="error",
                    )
                retr_rows[key].append(row)
            if args.no_qa:
                continue
            t0 = time.perf_counter()
            ans = qa.answer(user, case["query"])
            qa_rows.append(
                {
                    "case_id": case["id"],
                    "category": case["category"],
                    "query": case["query"],
                    "expect_answer": case["expect_answer"],
                    "answered": ans.evidence_sufficient,
                    "correct": ans.evidence_sufficient == case["expect_answer"],
                    "evidence_signal": ans.evidence_signal,
                    "evidence_score": ans.evidence_score,
                    "threshold": ans.threshold,
                    "refusal_reason": ans.refusal_reason,
                    "policy_violations": ans.policy_violations,
                    "citations": [c.document_name for c in ans.citations],
                    "citation_leaks": sum(
                        1 for c in ans.citations if c.kb_slug not in ALLOWED_KBS[case["user"]]
                    ),
                    "top_candidates": [
                        {k: c[k] for k in ("document_name", "evidence", "score")}
                        for c in ans.candidates[:5]
                    ],
                    "latency_ms": (time.perf_counter() - t0) * 1000,
                    "generator": ans.generator,
                }
            )

    metrics = {k: aggregate(rows) for k, rows in retr_rows.items()}
    qa_metrics = aggregate_qa(qa_rows) if qa_rows else None
    if qa_metrics:
        qa_metrics["mode"] = f"hybrid_rerank:{qa_rr}"
    emeta = embedder_metadata(embedder)
    meta = {
        "started_at": started.isoformat(),
        "git_commit": commit,
        "dataset": ds_path.name,
        "dataset_split": args.dataset,
        "dataset_sha256": ds_sha,
        "n_cases": len(cases),
        "label_source": "synthetic",
        "embedding_provider": embedder.name,
        "embedding_model": embedder.model,
        "embedding_metadata": emeta,
        "embedding_load_s": round(emb_load_s, 3),
        "embedding_has_semantics": embedder.name not in ("mock", "unavailable"),
        "index": index_info,
        "rerankers": {
            r: {
                "name": o.name,
                "kind": getattr(o, "kind", "?"),
                "load_s": rr_load[r],
                **(o.metadata() if hasattr(o, "metadata") else {}),
            }
            for r, o in rr_objs.items()
        },
        "llm_generator": qa_rows[0]["generator"] if qa_rows else None,
        "qa_profile": args.qa_profile,
        "config": {
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "rrf_k": settings.rrf_k,
            "retrieval_candidates": settings.retrieval_candidates,
            "rerank_candidates": settings.rerank_candidates,
            "qa_evidence": settings.qa_evidence,
            "qa_min_evidence": settings.qa_min_evidence,
            "qa_min_rerank_score": settings.qa_min_rerank_score,
            "qa_require_anchor_terms": settings.qa_require_anchor_terms,
            "hnsw_iterative_scan": settings.hnsw_iterative_scan,
            "hnsw_ef_search": settings.hnsw_ef_search,
            "top_k": TOP_K,
        },
        "warning": (
            "mock embedding = feature-hashed character/word unigrams: it measures lexical overlap only "
            "and has NO semantic understanding; vector/hybrid numbers here say nothing about real "
            "embedding quality."
            if embedder.name == "mock"
            else None
        ),
    }
    report = {
        "meta": meta,
        "retrieval": metrics,
        "qa": qa_metrics,
        "cases": {"retrieval": retr_rows, "qa": qa_rows},
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    tag = args.tag or f"{embedder.model}_{args.dataset}"
    stem = f"{started.strftime('%Y%m%dT%H%M%SZ')}_{tag}"
    blob = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    (RESULTS / f"{stem}.json").write_text(blob, encoding="utf-8")
    (RESULTS / f"{stem}.md").write_text(render_md(report), encoding="utf-8")
    (RESULTS / f"latest_{tag}.json").write_text(blob, encoding="utf-8")
    if not args.no_db:
        write_db(engine, report, cases, ds_sha)
    print(render_md(report))
    print(f"\nwritten: evals/results/{stem}.json / .md")
    return 0


def _mean(xs: list[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return round(statistics.fmean(v), 4) if v else None


def aggregate(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("error") is None]
    lat = [r["latency_ms"] for r in ok]
    by_cat: dict[str, dict] = {}
    for cat in sorted({r["category"] for r in rows}):
        rs = [r for r in ok if r["category"] == cat]
        by_cat[cat] = {
            "n": len(rs),
            "recall_at_5": _mean([r["recall_at_5"] for r in rs]),
            "mrr_at_10": _mean([r["mrr_at_10"] for r in rs]),
        }
    return {
        "n": len(rows),
        "recall_at_5": _mean([r["recall_at_5"] for r in ok]),
        "mrr_at_10": _mean([r["mrr_at_10"] for r in ok]),
        "ndcg_at_10": _mean([r["ndcg_at_10"] for r in ok]),
        "latency_p50_ms": pct(lat, 50),
        "latency_p95_ms": pct(lat, 95),
        "failure_rate": round(1 - len(ok) / len(rows), 4) if rows else 0.0,
        "acl_leaked_chunks": sum(r.get("acl_leaks", 0) for r in rows),
        "acl_isolation_correct_rate": round(
            sum(1 for r in ok if r.get("acl_leaks", 0) == 0) / len(ok), 4
        )
        if ok
        else None,
        "degraded": sum(1 for r in ok if r.get("degraded")),
        "failure_categories": dict(
            sorted(
                {
                    c: sum(1 for r in rows if r.get("failure_category") == c)
                    for c in {r.get("failure_category") for r in rows}
                }.items()
            )
        ),
        "by_category": by_cat,
    }


def aggregate_qa(rows: list[dict]) -> dict:
    neg = [r for r in rows if not r["expect_answer"]]
    pos = [r for r in rows if r["expect_answer"]]
    lat = [r["latency_ms"] for r in rows]
    return {
        "mode": "hybrid_rerank",
        "n": len(rows),
        "no_answer_correct_refusal_rate": round(sum(not r["answered"] for r in neg) / len(neg), 4)
        if neg
        else None,
        "answerable_answer_rate": round(sum(r["answered"] for r in pos) / len(pos), 4)
        if pos
        else None,
        "citation_acl_leaks": sum(r["citation_leaks"] for r in rows),
        "false_accepts": [r["case_id"] for r in neg if r["answered"]],
        "false_refusals": [r["case_id"] for r in pos if not r["answered"]],
        "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 4) if rows else None,
        "latency_p50_ms": pct(lat, 50),
        "latency_p95_ms": pct(lat, 95),
        "by_category": {
            cat: {"n": len(rs), "answered_rate": round(sum(r["answered"] for r in rs) / len(rs), 4)}
            for cat in sorted({r["category"] for r in rows})
            for rs in [[r for r in rows if r["category"] == cat]]
        },
    }


def _f(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def render_md(rep: dict) -> str:
    m = rep["meta"]
    lines = [
        "# 检索评测结果",
        "",
        f"- 时间(UTC)：{m['started_at']}",
        f"- commit：`{m['git_commit']}`",
        f"- 数据集：{m['dataset']}（split={m['dataset_split']}，{m['n_cases']} 条，label_source={m['label_source']}，sha256 `{m['dataset_sha256'][:12]}`）",
        f"- Embedding：{m['embedding_provider']} / {m['embedding_model']}（revision `{str(m['embedding_metadata'].get('revision'))[:12]}`，dim {m['index']['dim']}）；QA 生成器：{m['llm_generator']}",
        "- Reranker："
        + ", ".join(f"{k}={v['name']}({v['kind']})" for k, v in m["rerankers"].items()),
        f"- 配置：`{json.dumps(m['config'], ensure_ascii=False)}`",
        "",
    ]
    if m["warning"]:
        lines += [f"> ⚠️ {m['warning']}", ""]
    lines += [
        "| 模式 | Recall@5 | MRR@10 | nDCG@10 | P50 ms | P95 ms | 失败率 | ACL 泄露块数 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for mode, x in rep["retrieval"].items():
        lines.append(
            f"| {mode} | {_f(x['recall_at_5'])} | {_f(x['mrr_at_10'])} | {_f(x['ndcg_at_10'])} | "
            f"{x['latency_p50_ms']} | {x['latency_p95_ms']} | {x['failure_rate']} | {x['acl_leaked_chunks']} |"
        )
    cats = sorted({c for x in rep["retrieval"].values() for c in x["by_category"]})
    lines += [
        "",
        "按类别 Recall@5（无答案/无权限类无 gold，不计）：",
        "",
        "| 模式 | " + " | ".join(cats) + " |",
        "|---|" + "---|" * len(cats),
    ]
    for mode, x in rep["retrieval"].items():
        lines.append(
            f"| {mode} | " + " | ".join(_f(x["by_category"][c]["recall_at_5"]) for c in cats) + " |"
        )
    q = rep["qa"]
    if not q:
        return "\n".join(lines) + "\n"
    lines += [
        "",
        f"QA（{q['mode']}，{m['llm_generator']}，profile={m['qa_profile']}，证据信号 "
        f"{m['config']['qa_evidence']}，词面阈值 {m['config']['qa_min_evidence']} / 重排阈值 "
        f"{m['config']['qa_min_rerank_score']}，anchor={m['config']['qa_require_anchor_terms']}）：",
        f"- 无答案/无权限问题正确拒答率：{_f(q['no_answer_correct_refusal_rate'])}",
        f"- 可回答问题的作答率：{_f(q['answerable_answer_rate'])}",
        f"- 引用 ACL 泄露：{q['citation_acl_leaks']}",
        f"- QA 延迟 P50/P95：{q['latency_p50_ms']} / {q['latency_p95_ms']} ms",
    ]
    return "\n".join(lines) + "\n"


def write_db(engine, rep: dict, cases: list[dict], ds_sha: str) -> None:
    m = rep["meta"]
    label = {c["id"]: c["label_source"] for c in cases}
    with Session(engine) as s, s.begin():
        for mode, metrics in rep["retrieval"].items():
            run = EvalRun(
                name=f"retrieval-{mode}-{m['embedding_model']}-{m['dataset_split']}",
                dataset_name=m["dataset"],
                dataset_sha256=ds_sha,
                retriever=mode[:32],
                llm_provider=(m["llm_generator"] or "none")[:32],
                git_commit=(m["git_commit"] or "")[:40] or None,
                config={
                    **m["config"],
                    "embedding_model": m["embedding_model"],
                    "embedding_provider": m["embedding_provider"],
                    "embedding_metadata": m["embedding_metadata"],
                    "embedding_has_semantics": m["embedding_has_semantics"],
                    "dataset_split": m["dataset_split"],
                    "rerankers": m["rerankers"],
                    "qa_profile": m["qa_profile"],
                    "warning": m["warning"],
                },
                metrics={
                    **metrics,
                    "qa": rep["qa"] if rep["qa"] and mode == rep["qa"]["mode"] else None,
                },
                status="completed",
                finished_at=datetime.now(UTC),
            )
            s.add(run)
            s.flush()
            for r in rep["cases"]["retrieval"][mode]:
                s.add(
                    EvalCaseResult(
                        eval_run_id=run.id,
                        case_id=r["case_id"],
                        label_source=label[r["case_id"]],
                        recall_at_5=r.get("recall_at_5"),
                        latency_ms=int(r["latency_ms"])
                        if r.get("latency_ms") is not None
                        else None,
                        details={
                            k: r.get(k)
                            for k in (
                                "category",
                                "query",
                                "user",
                                "ground_truth",
                                "mrr_at_10",
                                "ndcg_at_10",
                                "hit_at_5",
                                "failure_category",
                                "acl_leaks",
                                "degraded",
                                "top",
                                "error",
                            )
                        },
                    )
                )


if __name__ == "__main__":
    sys.exit(main())
