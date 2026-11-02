from __future__ import annotations

from opspilot.core.config import Settings
from opspilot.llm.base import EmbeddingProvider, LLMProvider
from opspilot.llm.mock import MockEmbedding, MockLLM
from opspilot.llm.openai_compat import OpenAICompatEmbedding, OpenAICompatLLM


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockLLM()
    return OpenAICompatLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )


def build_embedding(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "mock":
        return MockEmbedding(dim=settings.embedding_dim)
    return OpenAICompatEmbedding(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.embedding_model,
        dim=settings.embedding_dim,
        timeout_s=settings.llm_timeout_s,
    )
