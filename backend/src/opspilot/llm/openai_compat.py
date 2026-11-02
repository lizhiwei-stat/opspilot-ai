"""OpenAI-compatible provider (Qwen via DashScope compatible-mode). Not exercised in Stage 1."""

from __future__ import annotations

import openai
from openai import OpenAI

from opspilot.llm.base import ChatMessage, ChatResult, LLMError, ProviderHealth, Usage


class OpenAICompatLLM:
    name = "openai_compatible"

    def __init__(
        self, *, base_url: str, api_key: str, model: str, timeout_s: float, max_retries: int
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for openai_compatible provider")
        self.model = model
        self._base_url = base_url
        self._client = OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=max_retries
        )

    def chat(
        self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> ChatResult:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"{type(exc).__name__}: provider call failed") from exc
        choice = resp.choices[0].message.content or ""
        u = resp.usage
        usage = Usage(u.prompt_tokens, u.completion_tokens) if u else Usage()
        return ChatResult(content=choice, model=resp.model, usage=usage)

    def health(self) -> ProviderHealth:
        # Config-only check: readiness probes must not spend tokens on every poll.
        return ProviderHealth(True, self.name, self.model, f"configured base_url={self._base_url}")


class OpenAICompatEmbedding:
    name = "openai_compatible"

    def __init__(
        self, *, base_url: str, api_key: str, model: str, dim: int, timeout_s: float
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for openai_compatible provider")
        self.model = model
        self.dim = dim
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s)

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self._client.embeddings.create(
                model=self.model, input=texts, dimensions=self.dim
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"{type(exc).__name__}: embedding call failed") from exc
        vectors = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        if any(len(v) != self.dim for v in vectors):
            raise LLMError("embedding dimension mismatch")
        return vectors
