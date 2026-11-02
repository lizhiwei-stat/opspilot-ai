from __future__ import annotations

from functools import lru_cache

from opspilot.core.config import Settings
from opspilot.core.logging import get_logger
from opspilot.llm.base import EmbeddingProvider, LLMError, LLMProvider
from opspilot.llm.mock import MockEmbedding, MockLLM
from opspilot.llm.openai_compat import OpenAICompatEmbedding, OpenAICompatLLM
from opspilot.rag.rerank import LexicalOverlapReranker, MockReranker, Reranker

_GATEWAYS: dict[str, object] = {}


def default_gateway(settings: Settings, run_id: str = "api-process"):  # type: ignore[no-untyped-def]
    """One gateway (budget + ledger) per run id; the API process is a single 'run'."""
    from opspilot.llm.gateway import ModelGateway

    gw = _GATEWAYS.get(run_id)
    if gw is None:
        gw = _GATEWAYS[run_id] = ModelGateway(settings, run_id=run_id)
    return gw


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockLLM()
    from opspilot.llm.gateway import GuardedLLM

    return GuardedLLM(_raw_llm(settings), default_gateway(settings))


def _raw_llm(settings: Settings) -> LLMProvider:
    return OpenAICompatLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )


@lru_cache(maxsize=4)
def _local_embedding(
    model_id: str, revision: str, max_length: int, batch: int, threads: int, instr: str
) -> EmbeddingProvider:
    from opspilot.llm.local import LocalSentenceEmbedding

    return LocalSentenceEmbedding(
        model_id=model_id,
        revision=revision,
        max_length=max_length,
        batch_size=batch,
        threads=threads,
        query_instruction=instr,
    )


@lru_cache(maxsize=2)
def _cross_encoder(model_id: str, revision: str, max_length: int, threads: int) -> Reranker:
    from opspilot.llm.local import CrossEncoderReranker

    return CrossEncoderReranker(
        model_id=model_id, revision=revision, max_length=max_length, threads=threads
    )


def build_embedding(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "mock":
        return MockEmbedding(dim=settings.embedding_dim)
    if settings.embedding_provider == "local":
        # Loaded once per process (model load is seconds, not per request).
        return _local_embedding(
            settings.local_embedding_model,
            settings.local_embedding_revision,
            settings.local_embedding_max_length,
            settings.local_embedding_batch_size,
            settings.local_threads,
            settings.local_query_instruction,
        )
    from opspilot.llm.gateway import GuardedEmbedding

    raw = OpenAICompatEmbedding(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.embedding_model,
        dim=settings.embedding_dim,
        timeout_s=settings.llm_timeout_s,
    )
    return GuardedEmbedding(raw, default_gateway(settings))


def build_reranker(settings: Settings, kind: str | None = None) -> Reranker:
    kind = kind or settings.reranker
    if kind == "rule":
        return LexicalOverlapReranker()
    if kind == "mock":
        return MockReranker()
    if kind == "cross_encoder":
        return _cross_encoder(
            settings.reranker_model,
            settings.reranker_revision,
            settings.reranker_max_length,
            settings.local_threads,
        )
    raise ValueError(f"unknown reranker {kind!r}")


class UnavailableEmbedding:
    """Stand-in when the configured embedding model cannot be loaded (optional deps missing,
    model not cached, ...). embed() raises LLMError, so retrieval degrades to keyword-only and
    flags the result instead of the whole API failing to start."""

    name = "unavailable"

    def __init__(self, model: str, dim: int, reason: str) -> None:
        self.model, self.dim, self.reason = model, dim, reason

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise LLMError(f"embedding model unavailable: {self.reason}")

    def metadata(self) -> dict[str, object]:
        return {"provider": "unavailable", "model_id": self.model, "reason": self.reason}


def build_embedding_or_degraded(settings: Settings) -> EmbeddingProvider:
    try:
        return build_embedding(settings)
    except LLMError as exc:  # LocalModelUnavailable is an LLMError
        get_logger("opspilot.llm").error("embedding_unavailable", reason=str(exc))
        name = settings.local_embedding_model.rsplit("/", 1)[-1]
        return UnavailableEmbedding(name, 0, str(exc))
