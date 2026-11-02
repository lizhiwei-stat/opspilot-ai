"""Reproducible retrieval + grounded-QA evaluation over the synthetic Chinese corpus.

    uv run python evals/run_retrieval_eval.py                 # mock embedding (default, free)
    uv run python evals/run_retrieval_eval.py --embedding real --i-understand-this-costs-money
        # real path: needs OPSPILOT_EMBEDDING_PROVIDER=dashscope + key + OPSPILOT_ALLOW_PAID_API=true;
        # re-embeds the whole corpus with the real model first. NOT run in Stage 2.

Steps: seed + ingest corpus (idempotent) -> verify every gold phrase exists -> run every
query in every mode as its own user -> metrics -> evals/results/<ts>_<provider>.{json,md}
+ eval_runs / eval_case_results rows (one run per mode) for the frontend.

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
from opspilot.llm.factory import build_embedding, build_llm  # noqa: E402
from opspilot.services.ingestion import IngestionService  # noqa: E402
from opspilot.services.qa import QAService  # noqa: E402
from opspilot.services.retrieval import MODES, RetrievalService  # noqa: E402

DATASET = ROOT / "evals" / "datasets" / "retrieval_zh.jsonl"
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
                raise SystemExit(
                    f"gold check failed: {c['id']} phrase {g['contains']!r} not in {g['doc']}"
                )


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedding", choices=["mock", "real"], default="mock")
    ap.add_argument("--i-understand-this-costs-money", action="store_true")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--no-db", action="store_true", help="do not write eval_runs rows")
    args = ap.parse_args()

    settings = get_settings()
    if args.embedding == "real":
        if not args.i_understand_this_costs_money or not settings.allow_paid_api:
            raise SystemExit(
                "real embedding eval calls a paid API: pass --i-understand-this-costs-money "
                "AND set OPSPILOT_ALLOW_PAID_API=true (plus provider/key in .env)"
            )
        if settings.embedding_provider == "mock":
            raise SystemExit("OPSPILOT_EMBEDDING_PROVIDER is still 'mock'")
    elif settings.embedding_provider != "mock":
        settings = Settings(**{**settings.model_dump(), "embedding_provider": "mock"})
    modes = [m for m in args.modes.split(",") if m]
    cases = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ds_sha = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    embedder = build_embedding(settings)
    llm = build_llm(settings)
    engine = build_engine(settings)
    started = datetime.now(UTC)
    commit = git_commit()

    with Session(engine) as s, s.begin():
        load(s)
        users = {k: s.scalar(select(User).where(User.email == v)) for k, v in USERS.items()}
        stale = s.scalars(select(Document).where(Document.status == "active")).all()
        if args.embedding == "real":  # re-embed every doc with the real model (paid!)
            ing = IngestionService(s, settings, embedder)
            for d in stale:
                ing.reindex(users["admin"], d.id)
        verify_gold(s, cases)

    retr_rows: dict[str, list[dict]] = {m: [] for m in modes}
    qa_rows: list[dict] = []
    with Session(engine) as s:
        users = {k: s.scalar(select(User).where(User.email == v)) for k, v in USERS.items()}
        retr = RetrievalService(s, settings, embedder)
        qa = QAService(s, settings, embedder, llm)
        for m in modes:  # warm-up (jieba dict load, first embed, plan cache) excluded from latency
            retr.search(users["admin"], "预热", mode=m, top_k=TOP_K)
        for case in cases:
            user = users[case["user"]]
            for m in modes:
                row = {"case_id": case["id"], "category": case["category"], "mode": m}
                try:
                    t0 = time.perf_counter()
                    res = retr.search(user, case["query"], mode=m, top_k=TOP_K)
                    row["latency_ms"] = (time.perf_counter() - t0) * 1000
                    row.update(score_case(case, res.candidates))
                    leaks = [
                        c for c in res.candidates if c.kb_slug not in ALLOWED_KBS[case["user"]]
                    ]
                    row["acl_leaks"] = len(leaks)
                    row["top"] = [
                        {
                            "rank": c.rank,
                            "doc": c.document_name,
                            "page": c.page_number,
                            "score": c.score,
                            "chunk_id": c.chunk_id,
                        }
                        for c in res.candidates[:5]
                    ]
                    row["error"] = None
                except Exception as exc:
                    row.update(error=f"{type(exc).__name__}: {exc}", latency_ms=None, acl_leaks=0)
                retr_rows[m].append(row)
            t0 = time.perf_counter()
            ans = qa.answer(user, case["query"])
            qa_rows.append(
                {
                    "case_id": case["id"],
                    "category": case["category"],
                    "expect_answer": case["expect_answer"],
                    "answered": ans.evidence_sufficient,
                    "evidence_score": ans.evidence_score,
                    "citations": [c.document_name for c in ans.citations],
                    "citation_leaks": sum(
                        1 for c in ans.citations if c.kb_slug not in ALLOWED_KBS[case["user"]]
                    ),
                    "latency_ms": (time.perf_counter() - t0) * 1000,
                    "generator": ans.generator,
                }
            )

    metrics = {m: aggregate(rows) for m, rows in retr_rows.items()}
    qa_metrics = aggregate_qa(qa_rows)
    meta = {
        "started_at": started.isoformat(),
        "git_commit": commit,
        "dataset": DATASET.name,
        "dataset_sha256": ds_sha,
        "n_cases": len(cases),
        "label_source": "synthetic",
        "embedding_provider": embedder.name,
        "embedding_model": embedder.model,
        "embedding_has_semantics": embedder.name != "mock",
        "llm_generator": qa_rows[0]["generator"] if qa_rows else None,
        "config": {
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "rrf_k": settings.rrf_k,
            "retrieval_candidates": settings.retrieval_candidates,
            "rerank_candidates": settings.rerank_candidates,
            "qa_min_evidence": settings.qa_min_evidence,
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
    stem = f"{started.strftime('%Y%m%dT%H%M%SZ')}_{embedder.name}"
    (RESULTS / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (RESULTS / f"{stem}.md").write_text(render_md(report), encoding="utf-8")
    (RESULTS / f"latest_{embedder.name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
        f"- 数据集：{m['dataset']}（{m['n_cases']} 条，label_source={m['label_source']}，sha256 `{m['dataset_sha256'][:12]}`）",
        f"- Embedding：{m['embedding_provider']} / {m['embedding_model']}；QA 生成器：{m['llm_generator']}",
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
    lines += [
        "",
        f"QA（{q['mode']}，mock 抽取式，证据阈值 {m['config']['qa_min_evidence']}）：",
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
                name=f"retrieval-{mode}-{m['embedding_provider']}",
                dataset_name=m["dataset"],
                dataset_sha256=ds_sha,
                retriever=mode,
                llm_provider=m["llm_generator"] or "none",
                git_commit=(m["git_commit"] or "")[:40] or None,
                config={
                    **m["config"],
                    "embedding_model": m["embedding_model"],
                    "embedding_has_semantics": m["embedding_has_semantics"],
                    "warning": m["warning"],
                },
                metrics={**metrics, "qa": rep["qa"] if mode == "hybrid_rerank" else None},
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
                                "mrr_at_10",
                                "ndcg_at_10",
                                "acl_leaks",
                                "top",
                                "error",
                            )
                        },
                    )
                )


if __name__ == "__main__":
    sys.exit(main())
