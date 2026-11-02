"""Deterministic offline providers: zero cost, reproducible tests, no network."""

from __future__ import annotations

import hashlib
import math
import re

from opspilot.llm.base import ChatMessage, ChatResult, ProviderHealth, Usage

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")


def approx_tokens(text: str) -> int:
    """Rough count: 1 per CJK char / latin word. Labelled 'approx' wherever reported."""
    return len(_TOKEN_RE.findall(text))


class MockLLM:
    """Echo-style model. Output is a pure function of input so tests can assert on it."""

    name = "mock"

    def __init__(self, model: str = "mock-llm-v1") -> None:
        self.model = model

    def chat(
        self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> ChatResult:
        if not messages:
            raise ValueError("messages must not be empty")
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        digest = hashlib.sha256(last_user.encode()).hexdigest()[:8]
        content = f"[mock:{digest}] {last_user[: max(0, max_tokens)]}"
        usage = Usage(
            prompt_tokens=sum(approx_tokens(m.content) for m in messages),
            completion_tokens=approx_tokens(content),
        )
        return ChatResult(content=content, model=self.model, usage=usage)

    def health(self) -> ProviderHealth:
        return ProviderHealth(True, self.name, self.model, "offline deterministic mock")


class MockEmbedding:
    """Feature-hashing bag-of-tokens embedding, L2-normalised.

    Texts sharing tokens get positive cosine similarity, so retrieval tests are meaningful
    (unlike random vectors) while staying fully offline.
    """

    name = "mock"

    def __init__(self, dim: int = 1024, model: str = "mock-hash-embedding-v1") -> None:
        self.dim = dim
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in _TOKEN_RE.findall(text.lower()):
                h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
                vec[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
            norm = math.sqrt(sum(v * v for v in vec))
            out.append([v / norm for v in vec] if norm else vec)
        return out
