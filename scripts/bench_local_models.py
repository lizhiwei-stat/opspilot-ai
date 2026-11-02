"""Measure CPU load time / throughput / memory of candidate local models (offline, cached).

    HF_HUB_OFFLINE=1 uv run --group local-embed python scripts/bench_local_models.py [--m3]

Prints JSON; used to choose the embedding model (docs/reports/STAGE2_5_EVAL.md §model choice).
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from opspilot.llm.local import CrossEncoderReranker, LocalSentenceEmbedding  # noqa: E402
from opspilot.rag.chunking import chunk_document  # noqa: E402
from opspilot.rag.parsers import validate_and_parse  # noqa: E402

CANDIDATES = {
    "BAAI/bge-small-zh-v1.5": "7999e1d3359715c523056ef9478215996d62a620",
    "BAAI/bge-m3": "5617a9f61b028005a4858fdac845db406aefb181",
}
RERANKER = ("BAAI/bge-reranker-base", "2cfc18c9415c912f9d8155881c133215df768a70")


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def corpus_chunks() -> list[str]:
    out = []
    for p in sorted((ROOT / "evals" / "corpus").rglob("*")):
        if p.suffix in {".md", ".txt", ".pdf"} and "_pdf_src" not in p.parts:
            doc = validate_and_parse(p.name, p.read_bytes(), max_bytes=10 * 2**20)
            out += [d.content for d in chunk_document(doc, chunk_size=500, overlap=80)]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--m3", action="store_true", help="also bench bge-m3 (2.2 GB)")
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()
    chunks = corpus_chunks()
    queries = ["发版后出问题了怎么退回旧版本", "ORD-5003", "登录凭证多长时间失效"] * 10
    res: dict[str, object] = {
        "n_chunks": len(chunks),
        "threads": args.threads,
        "cpu_count": os.cpu_count(),
    }
    for mid, rev in CANDIDATES.items():
        if mid.endswith("m3") and not args.m3:
            continue
        t0 = time.perf_counter()
        m = LocalSentenceEmbedding(
            model_id=mid, revision=rev, threads=args.threads, max_length=512, batch_size=16
        )
        load = time.perf_counter() - t0
        m.embed(chunks[:4])  # warm-up
        t0 = time.perf_counter()
        m.embed(chunks)
        enc = time.perf_counter() - t0
        lat = []
        for q in queries:
            t1 = time.perf_counter()
            m.embed_query(q)
            lat.append((time.perf_counter() - t1) * 1000)
        lat.sort()
        toks = m.count_tokens(chunks)
        res[mid] = {
            "dim": m.dim,
            "load_s": round(load, 2),
            "corpus_encode_s": round(enc, 3),
            "chunks_per_s": round(len(chunks) / enc, 1),
            "query_ms_p50": round(lat[len(lat) // 2], 2),
            "query_ms_p95": round(lat[int(len(lat) * 0.95) - 1], 2),
            "corpus_tokens": sum(toks),
            "max_chunk_tokens": max(toks),
            "peak_rss_mb_so_far": round(rss_mb()),
        }
        del m
    t0 = time.perf_counter()
    rr = CrossEncoderReranker(model_id=RERANKER[0], revision=RERANKER[1], threads=args.threads)
    load = time.perf_counter() - t0
    rr.score(queries[0], chunks[:4])
    lat = []
    for q in queries[:10]:
        t1 = time.perf_counter()
        rr.score(q, chunks[:20])
        lat.append((time.perf_counter() - t1) * 1000)
    lat.sort()
    res[RERANKER[0]] = {
        "load_s": round(load, 2),
        "rerank20_ms_p50": round(lat[5], 1),
        "rerank20_ms_max": round(lat[-1], 1),
        "peak_rss_mb_so_far": round(rss_mb()),
    }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
