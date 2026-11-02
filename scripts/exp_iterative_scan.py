"""pgvector iterative index scan experiment (Stage 2.5 fix d).

Problem: HNSW returns ef_search nearest candidates FIRST and applies the WHERE (ACL) filter
AFTERWARDS. With a sparse filter (a user may read 1% of rows) most candidates are filtered
out and a LIMIT 10 query returns far fewer than 10 rows. pgvector >= 0.8 `hnsw.iterative_scan`
keeps scanning the graph until enough rows pass the filter.

The experiment builds a SYNTHETIC table in its own throwaway schema `exp_iterscan`
(never touches OpsPilot tables), N random unit vectors, ACL groups with a chosen
selectivity, then for each setting measures: rows returned (of LIMIT k), recall vs exact
(brute-force) top-k, latency. The schema is dropped at the end.

    uv run python scripts/exp_iterative_scan.py --n 100000 --dim 128 --selectivity 0.01
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
from opspilot.core.config import get_settings  # noqa: E402

SCHEMA = "exp_iterscan"


def vec(rng: random.Random, dim: int) -> str:
    v = [rng.gauss(0, 1) for _ in range(dim)]
    n = sum(x * x for x in v) ** 0.5
    return "[" + ",".join(f"{x / n:.5f}" for x in v) + "]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--dim", type=int, default=128)
    ap.add_argument("--selectivity", type=float, default=0.01, help="share of rows user may read")
    ap.add_argument("--queries", type=int, default=30)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--ef-search", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "evals" / "results"))
    args = ap.parse_args()
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    rng = random.Random(args.seed)  # noqa: S311 - synthetic data, not crypto
    groups = max(1, round(1 / args.selectivity))
    res: dict = {"params": vars(args) | {"acl_groups": groups}, "settings": {}}
    with psycopg.connect(url, autocommit=True) as c:
        ver = c.execute("select extversion from pg_extension where extname='vector'").fetchone()
        res["pgvector"] = ver[0] if ver else None
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
        try:
            c.execute(
                f"CREATE TABLE {SCHEMA}.items (id int primary key, grp int not null, "
                f"embedding vector({args.dim}) not null)"
            )
            t0 = time.perf_counter()
            with c.cursor().copy(f"COPY {SCHEMA}.items (id, grp, embedding) FROM STDIN") as cp:
                for i in range(args.n):
                    cp.write_row((i, rng.randrange(groups), vec(rng, args.dim)))
            res["load_s"] = round(time.perf_counter() - t0, 1)
            t0 = time.perf_counter()
            c.execute("SET maintenance_work_mem = '512MB'")
            c.execute(
                f"CREATE INDEX ON {SCHEMA}.items USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64)"
            )
            c.execute(f"ANALYZE {SCHEMA}.items")
            res["index_build_s"] = round(time.perf_counter() - t0, 1)
            qs = [(vec(rng, args.dim), rng.randrange(groups)) for _ in range(args.queries)]
            sql = (
                f"SELECT id FROM {SCHEMA}.items WHERE grp = %s "  # noqa: S608 - constant schema
                "ORDER BY embedding <=> %s::vector LIMIT %s"
            )
            # exact ground truth: no index
            exact = []
            with c.transaction():
                c.execute("SET LOCAL enable_indexscan = off")
                c.execute("SET LOCAL enable_bitmapscan = off")
                for q, g in qs:
                    exact.append({r[0] for r in c.execute(sql, (g, q, args.k)).fetchall()})
            variants = [("off", None), ("relaxed_order", None), ("strict_order", None)]
            for mode, _ in variants:
                counts, recalls, lat = [], [], []
                with c.transaction():
                    c.execute(f"SET LOCAL hnsw.ef_search = {int(args.ef_search)}")
                    c.execute(f"SET LOCAL hnsw.iterative_scan = {mode}")
                    plan = c.execute("EXPLAIN " + sql, (qs[0][1], qs[0][0], args.k)).fetchall()
                    uses_index = any("hnsw" in r[0].lower() or "Index Scan" in r[0] for r in plan)
                    for (q, g), ex in zip(qs, exact, strict=True):
                        t1 = time.perf_counter()
                        got = [r[0] for r in c.execute(sql, (g, q, args.k)).fetchall()]
                        lat.append((time.perf_counter() - t1) * 1000)
                        counts.append(len(got))
                        recalls.append(len(set(got) & ex) / max(1, len(ex)))
                lat.sort()
                res["settings"][mode] = {
                    "uses_hnsw_index": uses_index,
                    "rows_returned_mean": round(statistics.mean(counts), 2),
                    "rows_returned_min": min(counts),
                    "queries_short_of_k": sum(1 for x in counts if x < args.k),
                    "recall_vs_exact": round(statistics.mean(recalls), 3),
                    "latency_ms_p50": round(lat[len(lat) // 2], 2),
                    "latency_ms_p95": round(lat[max(0, int(len(lat) * 0.95) - 1)], 2),
                }
        finally:
            c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    res["finished_at"] = datetime.now(UTC).isoformat()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_iterscan_n{args.n}_s{args.selectivity}.json"
    p.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))
    print("written:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
