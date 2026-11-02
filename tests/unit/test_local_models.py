"""Real local-model tests (CPU). Skipped unless the local-embed group is installed and the
pinned revisions are already in the HF cache (they never download)."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.local_model

st = pytest.importorskip("sentence_transformers")
hub = pytest.importorskip("huggingface_hub")

EMB = ("BAAI/bge-small-zh-v1.5", "7999e1d3359715c523056ef9478215996d62a620")
RR = ("BAAI/bge-reranker-base", "2cfc18c9415c912f9d8155881c133215df768a70")


def _cached(repo: str, rev: str) -> bool:
    p = hub.try_to_load_from_cache(repo, "config.json", revision=rev)
    return isinstance(p, str) and os.path.exists(p)


@pytest.fixture(scope="module", autouse=True)
def _offline():
    old = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    yield
    if old is None:
        os.environ.pop("HF_HUB_OFFLINE", None)
    else:
        os.environ["HF_HUB_OFFLINE"] = old


@pytest.fixture(scope="module")
def emb():
    if not _cached(*EMB):
        pytest.skip("bge-small-zh-v1.5 not cached")
    from opspilot.llm.local import LocalSentenceEmbedding

    return LocalSentenceEmbedding(model_id=EMB[0], revision=EMB[1], threads=4)


@pytest.fixture(scope="module")
def rr():
    if not _cached(*RR):
        pytest.skip("bge-reranker-base not cached")
    from opspilot.llm.local import CrossEncoderReranker

    return CrossEncoderReranker(model_id=RR[0], revision=RR[1], threads=4)


def test_embedding_dim_normalized_and_semantic(emb):
    import math

    assert emb.dim == 512
    q = emb.embed_query("发版出问题怎么退回上一个版本")
    a, b = emb.embed(["回滚：执行 kubectl rollout undo 回到上一版本", "食堂周五供应饺子"])
    assert abs(math.sqrt(sum(x * x for x in q)) - 1.0) < 1e-3

    def cos(u, v):
        return sum(x * y for x, y in zip(u, v, strict=True))

    assert cos(q, a) > cos(q, b) + 0.1
    md = emb.metadata()
    assert md["revision"] == EMB[1] and md["device"] == "cpu" and md["normalize"] is True


def test_real_tokenizer_counts(emb):
    n = emb.count_tokens(["ERR-PAY-TIMEOUT 支付超时"])[0]
    assert 5 < n < 40  # real wordpiece count incl. [CLS]/[SEP]; not len(text)


def test_cross_encoder_scores_are_calibrated_not_double_sigmoid(rr):
    s_irr, s_rel = rr.score(
        "如何回滚发布", ["香蕉是一种水果", "回滚：执行 kubectl rollout undo 回到上一版本"]
    )
    # regression: activation_fn=None applied Sigmoid twice -> every score in (0.5, 0.73)
    assert s_irr < 0.05
    assert s_rel > 0.5
