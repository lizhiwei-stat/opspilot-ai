"""Provider-agnostic LLM / embedding interfaces. Agents depend on these, never on a vendor SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class ProviderHealth:
    ok: bool
    provider: str
    model: str
    detail: str


class LLMError(RuntimeError):
    """Raised when a provider call fails after retries (callers degrade gracefully)."""


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def chat(
        self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> ChatResult: ...

    def health(self) -> ProviderHealth: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
