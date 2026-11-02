"""Free local CPU models via sentence-transformers (optional `local-embed` dependency group).

Nothing here is imported unless OPSPILOT_EMBEDDING_PROVIDER=local or OPSPILOT_RERANKER=
cross_encoder, so the default install / Docker image / mock tests never need torch.

Models are pinned to an exact Hugging Face commit (revision) and loaded on device="cpu"
only. Downloads use the normal HF cache (HF_HOME / HF_ENDPOINT are honoured); set
HF_HUB_OFFLINE=1 to guarantee no network access at run time.
"""

from __future__ import annotations

import math
import time
from typing import Any

from opspilot.llm.base import LLMError


class LocalModelUnavailable(LLMError):
    """local-embed group not installed or model files not cached / loadable."""


def _import_st() -> Any:
    try:
        import sentence_transformers
        import torch
    except ImportError as exc:  # pragma: no cover - depends on optional group
        raise LocalModelUnavailable(
            "local embedding needs the optional dependency group: uv sync --group local-embed"
        ) from exc
    return sentence_transformers, torch


class LocalSentenceEmbedding:
    """Bi-encoder on CPU. `model` (the index key) is the HF id's last path part, e.g.
    'bge-small-zh-v1.5'; the full id + revision + inference config are in `metadata()`."""

    name = "local"

    def __init__(
        self,
        *,
        model_id: str,
        revision: str,
        max_length: int = 512,
        batch_size: int = 16,
        threads: int = 8,
        normalize: bool = True,
        query_instruction: str = "",
    ) -> None:
        st, torch = _import_st()
        torch.set_num_threads(threads)
        t0 = time.perf_counter()
        try:
            self._m = st.SentenceTransformer(model_id, revision=revision, device="cpu")
        except Exception as exc:  # OSError (not cached / offline), HF errors, ...
            raise LocalModelUnavailable(
                f"cannot load local model {model_id}@{revision[:12]}: {type(exc).__name__}"
            ) from exc
        self.load_s = time.perf_counter() - t0
        self._m.max_seq_length = max_length
        self.model_id, self.revision = model_id, revision
        self.model = model_id.rsplit("/", 1)[-1]
        self.dim = int(self._m.get_sentence_embedding_dimension())
        self.max_length, self.batch_size, self.threads = max_length, batch_size, threads
        self.normalize, self.query_instruction = normalize, query_instruction
        self.torch_version = torch.__version__
        self.st_version = st.__version__

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": "local",
            "model_id": self.model_id,
            "revision": self.revision,
            "dim": self.dim,
            "normalize": self.normalize,
            "batch_size": self.batch_size,
            "max_length": self.max_length,
            "device": "cpu",
            "threads": self.threads,
            "query_instruction": self.query_instruction,
            "pooling": "cls (from model's sentence-transformers config)",
            "torch": self.torch_version,
            "sentence_transformers": self.st_version,
            "load_s": round(self.load_s, 3),
        }

    def _encode(self, texts: list[str]) -> list[list[float]]:
        try:
            arr = self._m.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=self.normalize,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:  # pragma: no cover
            raise LLMError(f"local embedding failed: {type(exc).__name__}") from exc
        return [list(map(float, v)) for v in arr]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Passages (documents): no instruction prefix."""
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        """Queries get the model's retrieval instruction (bge-zh-v1.5 recommendation)."""
        return self._encode([self.query_instruction + text])[0]

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Real token counts from this model's own tokenizer (incl. [CLS]/[SEP]), untruncated."""
        tok = self._m.tokenizer
        return [
            len(ids) for ids in tok(texts, add_special_tokens=True, truncation=False)["input_ids"]
        ]


class CrossEncoderReranker:
    """Real cross-encoder (query, passage) relevance on CPU. score = sigmoid(logit) in (0,1)."""

    kind = "model"

    def __init__(self, *, model_id: str, revision: str, max_length: int = 512, threads: int = 8):
        st, torch = _import_st()
        torch.set_num_threads(threads)
        self._identity = torch.nn.Identity()
        t0 = time.perf_counter()
        try:
            self._m = st.CrossEncoder(
                model_id, revision=revision, device="cpu", max_length=max_length
            )
        except Exception as exc:
            raise LocalModelUnavailable(
                f"cannot load reranker {model_id}@{revision[:12]}: {type(exc).__name__}"
            ) from exc
        self.load_s = time.perf_counter() - t0
        self.model_id, self.revision, self.max_length = model_id, revision, max_length
        self.name = f"cross-encoder:{model_id.rsplit('/', 1)[-1]}"

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        logits = self._m.predict(
            [(query, p) for p in passages],
            convert_to_numpy=True,
            show_progress_bar=False,
            # NOTE: activation_fn=None means "use the model default" (Sigmoid for
            # num_labels=1) in sentence-transformers 5.x, not "raw logits". Passing None
            # and applying our own sigmoid squashed every score into (0.5, 0.73) -- the
            # Stage 2.5 double-sigmoid bug. Identity gives raw logits; sigmoid once here.
            activation_fn=self._identity,
        )
        return [round(1.0 / (1.0 + math.exp(-float(x))), 6) for x in logits]

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": "model",
            "model_id": self.model_id,
            "revision": self.revision,
            "max_length": self.max_length,
            "device": "cpu",
            "score": "sigmoid(logit)",
            "load_s": round(self.load_s, 3),
        }
