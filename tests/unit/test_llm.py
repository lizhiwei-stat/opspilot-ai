import math

import pytest

from opspilot.llm.base import ChatMessage, EmbeddingProvider, LLMProvider
from opspilot.llm.factory import build_embedding, build_llm
from opspilot.llm.mock import MockEmbedding, MockLLM
from opspilot.llm.openai_compat import OpenAICompatLLM
from tests.conftest import make_settings


def test_mock_llm_deterministic_and_counts_tokens() -> None:
    llm = MockLLM()
    msgs = [ChatMessage("system", "你是助手"), ChatMessage("user", "订单接口 HTTP 500")]
    a, b = llm.chat(msgs), llm.chat(msgs)
    assert a == b
    assert a.content.startswith("[mock:") and "订单接口" in a.content
    assert a.usage.prompt_tokens > 0 and a.usage.total_tokens > a.usage.prompt_tokens


def test_mock_llm_rejects_empty() -> None:
    with pytest.raises(ValueError):
        MockLLM().chat([])


def test_mock_embedding_normalised_and_semantic_overlap() -> None:
    emb = MockEmbedding(dim=1024)
    q, near, far = emb.embed(["order api 500 error", "order api returns 500", "vacation policy"])
    assert len(q) == 1024
    assert math.isclose(sum(v * v for v in q), 1.0, rel_tol=1e-9)

    def cos(x: list[float], y: list[float]) -> float:
        return sum(a * b for a, b in zip(x, y, strict=True))

    assert cos(q, near) > cos(q, far)


def test_mock_embedding_empty_text_is_zero_vector() -> None:
    assert MockEmbedding(dim=8).embed([""]) == [[0.0] * 8]


def test_factory_builds_mock_by_default() -> None:
    s = make_settings()
    assert isinstance(build_llm(s), LLMProvider)
    assert isinstance(build_embedding(s), EmbeddingProvider)
    assert build_llm(s).name == "mock"


def test_openai_compat_constructs_without_network() -> None:
    # Construction must not call the network; we never call .chat() in Stage 1.
    llm = OpenAICompatLLM(
        base_url="http://127.0.0.1:9/v1",
        api_key="sk-fake-for-test",
        model="qwen-plus",
        timeout_s=1,
        max_retries=0,
    )
    h = llm.health()
    assert h.ok and h.provider == "openai_compatible" and "sk-" not in h.detail


def test_openai_compat_wraps_errors() -> None:
    from opspilot.llm.base import LLMError

    llm = OpenAICompatLLM(
        base_url="http://127.0.0.1:9/v1",
        api_key="sk-fake-for-test",
        model="qwen-plus",
        timeout_s=0.5,
        max_retries=0,
    )
    with pytest.raises(LLMError, match="provider call failed"):
        llm.chat([ChatMessage("user", "hi")])
