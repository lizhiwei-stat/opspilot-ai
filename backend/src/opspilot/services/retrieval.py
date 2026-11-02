"""Unified retrieval interface: one entry point, four modes, per-method scores and ranks.

vector         pgvector cosine (HNSW) over chunks embedded with the CURRENT model only
keyword        jieba tokens → PostgreSQL tsvector/tsquery (OR), ranked by ts_rank_cd
hybrid         RRF(vector, keyword), k = settings.rrf_k
hybrid_rerank  hybrid candidates re-scored by a pluggable Reranker
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from opspilot.core.config import Settings
from opspilot.core.logging import get_logger
from opspilot.db.models import User
from opspilot.llm.base import EmbeddingProvider, embed_query
from opspilot.llm.factory import build_reranker
from opspilot.rag.fusion import rrf
from opspilot.rag.rerank import Reranker
from opspilot.rag.sanitize import is_suspicious
from opspilot.rag.tokenize import query_tokens
from opspilot.repositories.knowledge import KnowledgeRepository

log = get_logger("opspilot.retrieval")

Mode = Literal["vector", "keyword", "hybrid", "hybrid_rerank"]
MODES: tuple[Mode, ...] = ("vector", "keyword", "hybrid", "hybrid_rerank")


@dataclass
class Candidate:
    chunk_id: str
    rank: int
    score: float  # score of the requested mode (cosine sim / ts_rank_cd / RRF / rerank)
    content: str
    document_id: str
    document_name: str
    document_title: str
    version: int
    kb_slug: str
    page_number: int | None
    heading_path: str | None
    char_start: int
    char_end: int
    suspicious: bool
    scores: dict[str, float | None] = field(default_factory=dict)
    ranks: dict[str, int | None] = field(default_factory=dict)

    @property
    def locator(self) -> str:
        loc = f"p.{self.page_number}" if self.page_number else (self.heading_path or "全文")
        return (
            f"{self.document_name} v{self.version} · {loc} · 字符 {self.char_start}-{self.char_end}"
        )


@dataclass
class RetrievalResult:
    query: str
    mode: Mode
    tokens: list[str]
    candidates: list[Candidate]
    timings_ms: dict[str, float]
    embedding_model: str
    reranker: str | None
    reranker_kind: str | None = None
    # Set when the requested mode could not run as asked, e.g. "embedding_unavailable" or
    # "no_vectors_for_model": vector part skipped, result is keyword-only (flagged in UI/API).
    degraded: str | None = None
    effective_mode: str | None = None

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        for c, cand in zip(d["candidates"], self.candidates, strict=True):  # type: ignore[arg-type]
            c["locator"] = cand.locator
        return d


class RetrievalService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        embedder: EmbeddingProvider,
        reranker: Reranker | None = None,
    ) -> None:
        self.repo = KnowledgeRepository(session)
        self.settings = settings
        self.embedder = embedder
        self.reranker: Reranker = reranker or build_reranker(settings)

    def search(
        self,
        user: User,
        query: str,
        *,
        mode: Mode = "hybrid",
        top_k: int = 5,
        kb_ids: list[uuid.UUID] | None = None,
    ) -> RetrievalResult:
        t: dict[str, float] = {}
        t0 = time.perf_counter()
        tokens = query_tokens(query)
        n = max(self.settings.retrieval_candidates, top_k)
        vec_hits, kw_hits = [], []
        degraded: str | None = None
        requested = mode
        if mode != "keyword":
            s = time.perf_counter()
            have, total = self.repo.index_coverage(self.embedder.model)
            try:
                if total and not have:
                    raise _NoVectors(self.embedder.model)
                qvec = embed_query(self.embedder, query)
                t["embed_ms"] = (time.perf_counter() - s) * 1000
            except _NoVectors:
                degraded = "no_vectors_for_model"
            except Exception as exc:  # embedding model down / timeout / not installed
                if not self.settings.embedding_fallback_keyword:
                    raise
                degraded = f"embedding_unavailable:{type(exc).__name__}"
            if not degraded:
                # DB errors are NOT degraded silently: they propagate (500), never hidden.
                vec_hits = self.repo.vector_search(
                    user,
                    qvec,
                    model=self.embedder.model,
                    limit=n,
                    kb_ids=kb_ids,
                    ef_search=self.settings.hnsw_ef_search,
                    iterative_scan=self.settings.hnsw_iterative_scan,
                )
            else:
                log.warning("vector_search_degraded", reason=degraded, model=self.embedder.model)
                # keyword-only fallback; rerank (if asked) still applies on keyword candidates
                mode = "keyword" if mode in ("vector", "hybrid") else mode
            t["vector_ms"] = (time.perf_counter() - s) * 1000
        if mode != "vector":
            s = time.perf_counter()
            kw_hits = self.repo.keyword_search(user, tokens, limit=n, kb_ids=kb_ids)
            t["keyword_ms"] = (time.perf_counter() - s) * 1000
        vs = {h.chunk_id: (i + 1, h.score) for i, h in enumerate(vec_hits)}
        ks = {h.chunk_id: (i + 1, h.score) for i, h in enumerate(kw_hits)}

        if mode == "vector":
            ordered = [(h.chunk_id, h.score) for h in vec_hits]
        elif mode == "keyword" or (degraded and mode == "hybrid_rerank"):
            ordered = [(h.chunk_id, h.score) for h in kw_hits]
        else:
            s = time.perf_counter()
            ordered = rrf(
                [[h.chunk_id for h in vec_hits], [h.chunk_id for h in kw_hits]],
                k=self.settings.rrf_k,
            )  # type: ignore[assignment]
            t["fusion_ms"] = (time.perf_counter() - s) * 1000
        fused = {cid: (i + 1, sc) for i, (cid, sc) in enumerate(ordered)}

        pool_n = self.settings.rerank_candidates if mode == "hybrid_rerank" else top_k
        pool = [cid for cid, _ in ordered[: max(pool_n, top_k)]]
        rows = self.repo.load_chunks(pool)  # type: ignore[arg-type]
        rr: dict[uuid.UUID, tuple[int, float]] = {}
        if mode == "hybrid_rerank" and pool:
            s = time.perf_counter()
            scores = self.reranker.score(query, [rows[c][0].content for c in pool])
            order = sorted(range(len(pool)), key=lambda i: (-scores[i], i))
            rr = {pool[i]: (r + 1, scores[i]) for r, i in enumerate(order)}
            pool = [pool[i] for i in order]
            t["rerank_ms"] = (time.perf_counter() - s) * 1000

        cands: list[Candidate] = []
        for rank, cid in enumerate(pool[:top_k], start=1):
            chunk, doc, ver, kb = rows[cid]
            main = rr[cid][1] if rr else fused[cid][1]
            cands.append(
                Candidate(
                    chunk_id=str(cid),
                    rank=rank,
                    score=round(float(main), 6),
                    content=chunk.content,
                    document_id=str(doc.id),
                    document_name=doc.source_name,
                    document_title=doc.title,
                    version=ver.version,
                    kb_slug=kb.slug,
                    page_number=chunk.page_number,
                    heading_path=chunk.heading_path,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    suspicious=is_suspicious(chunk.content),
                    scores={
                        "vector": _r(vs.get(cid)),
                        "keyword": _r(ks.get(cid)),
                        "rrf": _r(fused.get(cid)) if mode.startswith("hybrid") else None,
                        "rerank": _r(rr.get(cid)) if rr else None,
                    },
                    ranks={
                        "vector": _k(vs.get(cid)),
                        "keyword": _k(ks.get(cid)),
                        "rrf": _k(fused.get(cid)) if mode.startswith("hybrid") else None,
                        "rerank": _k(rr.get(cid)) if rr else None,
                    },
                )
            )
        t["total_ms"] = (time.perf_counter() - t0) * 1000
        return RetrievalResult(
            query,
            requested,
            tokens,
            cands,
            {k: round(v, 3) for k, v in t.items()},
            self.embedder.model,
            self.reranker.name if mode == "hybrid_rerank" else None,
            getattr(self.reranker, "kind", None) if mode == "hybrid_rerank" else None,
            degraded,
            mode if degraded else requested,
        )


class _NoVectors(Exception):
    pass


def _r(x: tuple[int, float] | None) -> float | None:
    return None if x is None else round(float(x[1]), 6)


def _k(x: tuple[int, float] | None) -> int | None:
    return None if x is None else x[0]
