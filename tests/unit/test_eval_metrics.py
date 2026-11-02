"""Metric definitions used by evals/run_retrieval_eval.py."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_P = Path(__file__).resolve().parents[2] / "evals" / "run_retrieval_eval.py"
_spec = importlib.util.spec_from_file_location("run_retrieval_eval", _P)
ev = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["run_retrieval_eval"] = ev  # dataclasses need the module registered
_spec.loader.exec_module(ev)  # type: ignore[union-attr]


def C(doc: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(document_name=doc, content=content)


def test_score_case_recall_mrr_ndcg() -> None:
    case = {"relevant": [{"doc": "a.md", "contains": "X1"}, {"doc": "b.md", "contains": "Y2"}]}
    cands = (
        [C("c.md", "X1"), C("a.md", "has X1"), C("z.md", "")] + [C("z", "")] * 3 + [C("b.md", "Y2")]
    )
    m = ev.score_case(case, cands)
    assert m["recall_at_5"] == 0.5  # b.md hit is at rank 7
    assert m["mrr_at_10"] == 0.5
    dcg = 1 / math.log2(3) + 1 / math.log2(8)
    assert m["ndcg_at_10"] == pytest.approx(dcg / (1 + 1 / math.log2(3)))


def test_score_case_no_gold() -> None:
    assert ev.score_case({"relevant": []}, [C("a", "b")])["recall_at_5"] is None


def test_percentile_nearest_rank() -> None:
    xs = [float(i) for i in range(1, 101)]
    assert ev.pct(xs, 50) == 50.0 and ev.pct(xs, 95) == 95.0 and ev.pct([], 50) == 0.0


def test_dataset_shape() -> None:
    import json

    rows = [
        json.loads(x)
        for x in (_P.parent / "datasets" / "retrieval_zh.jsonl").read_text().splitlines()
    ]
    assert len(rows) >= 50 and len({r["id"] for r in rows}) == len(rows)
    cats = {r["category"] for r in rows}
    assert cats == {
        "exact_keyword",
        "error_code",
        "paraphrase",
        "multi_doc",
        "no_answer",
        "no_permission",
    }
    assert all(r["label_source"] == "synthetic" for r in rows)
    assert all(bool(r["relevant"]) == r["expect_answer"] for r in rows)
