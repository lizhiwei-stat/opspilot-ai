"""Unit tests: RRF and rerankers."""

from __future__ import annotations

import pytest

from opspilot.rag.fusion import rrf
from opspilot.rag.rerank import LexicalOverlapReranker, NoopReranker, coverage


def test_rrf_formula() -> None:
    out = dict(rrf([["a", "b", "c"], ["b", "d"]], k=60))
    assert out["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert out["a"] == pytest.approx(1 / 61)
    assert out["d"] == pytest.approx(1 / 62)
    assert out["c"] == pytest.approx(1 / 63)


def test_rrf_order_and_tiebreak() -> None:
    ranked = [x for x, _ in rrf([["a", "b"], ["b", "a"]], k=60)]
    assert ranked == ["a", "b"]  # equal scores -> first seen wins (deterministic)
    assert next(x for x, _ in rrf([["a", "b", "c"], ["c", "b", "a"], ["b"]])) == "b"


def test_rrf_k_small_favours_head_large_favours_consensus() -> None:
    # "a" is mediocre in both lists, "y" is #1 in only one list.
    lists = [["x", "w", "a"], ["y", "z", "v", "u", "a"]]
    small, big = dict(rrf(lists, k=1)), dict(rrf(lists, k=1000))
    assert small["y"] > small["a"]  # tiny k: a single top-1 hit dominates
    assert big["a"] > big["y"]  # large k: appearing in both lists dominates


def test_rrf_empty() -> None:
    assert rrf([[], []]) == []


def test_lexical_reranker_prefers_coverage_and_codes() -> None:
    r = LexicalOverlapReranker()
    s = r.score(
        "ERR_PAY_TIMEOUT 怎么重试",
        ["支付网关超时 ERR_PAY_TIMEOUT，客户端可以重试 3 次", "订单列表分页", "重试策略说明"],
    )
    assert s[0] > s[2] > s[1] == 0.0
    assert all(0.0 <= x <= 1.0 for x in s)


def test_reranker_edge_cases() -> None:
    r = LexicalOverlapReranker()
    assert r.score("", ["a"]) == [0.0]
    assert r.score("x", []) == []
    assert NoopReranker().score("q", ["a", "b"]) == [0.0, 0.0]


def test_coverage() -> None:
    assert coverage("连接池 耗尽", "数据库连接池耗尽") == 1.0
    assert coverage("年会 地点", "数据库连接池") == 0.0
