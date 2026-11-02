"""Knowledge-base persistence + retrieval SQL. ACL is applied INSIDE every retrieval query
(kb_id IN accessible_kb_ids(user)) before ORDER BY / LIMIT, never as a post-filter."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Select, and_, cast, delete, exists, func, literal, or_, select, text, update
from sqlalchemy.orm import Session

from opspilot.core.errors import AppError
from opspilot.db.models import (
    DYNAMIC_INDEX_PREFIX,
    Chunk,
    ChunkEmbedding,
    Document,
    DocumentVersion,
    EmbeddingModel,
    KbPermission,
    KnowledgeBase,
    User,
)

WRITE_PERMS = ("write", "admin")


def accessible_kb_ids(user: User) -> Select[tuple[uuid.UUID]]:
    """Subquery of KB ids `user` may READ. Admin: all. Others: public, owned, or granted."""
    stmt = select(KnowledgeBase.id)
    if user.role == "admin":
        return stmt
    granted = exists().where(
        KbPermission.kb_id == KnowledgeBase.id, KbPermission.user_id == user.id
    )
    return stmt.where(
        or_(KnowledgeBase.visibility == "public", KnowledgeBase.owner_id == user.id, granted)
    )


class EmbeddingDimensionMismatch(AppError):
    """A registered model name is being used with a different vector dimension."""

    status_code = 409
    code = "embedding_dimension_mismatch"


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: uuid.UUID
    score: float


class KnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    # ---------------------------------------------------------------- ACL helpers
    def can_read(self, user: User, kb_id: uuid.UUID) -> bool:
        sub = accessible_kb_ids(user).where(KnowledgeBase.id == kb_id).subquery()
        return bool(self.s.scalar(select(func.count()).select_from(sub)))

    def can_write(self, user: User, kb: KnowledgeBase) -> bool:
        if user.role == "admin" or kb.owner_id == user.id:
            return True
        return bool(
            self.s.scalar(
                select(func.count())
                .select_from(KbPermission)
                .where(
                    KbPermission.kb_id == kb.id,
                    KbPermission.user_id == user.id,
                    KbPermission.permission.in_(WRITE_PERMS),
                )
            )
        )

    # ---------------------------------------------------------------- KB / documents
    def list_kbs(self, user: User) -> list[tuple[KnowledgeBase, int, int]]:
        docs = (
            select(func.count())
            .select_from(Document)
            .where(Document.kb_id == KnowledgeBase.id, Document.status == "active")
            .scalar_subquery()
        )
        chunks = (
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.kb_id == KnowledgeBase.id, Chunk.is_active)
            .scalar_subquery()
        )
        rows = self.s.execute(
            select(KnowledgeBase, docs, chunks)
            .where(KnowledgeBase.id.in_(accessible_kb_ids(user)))
            .order_by(KnowledgeBase.slug)
        )
        return [(r[0], int(r[1]), int(r[2])) for r in rows]

    def get_kb(self, kb_id: uuid.UUID) -> KnowledgeBase | None:
        return self.s.get(KnowledgeBase, kb_id)

    def get_kb_by_slug(self, slug: str) -> KnowledgeBase | None:
        return self.s.scalar(select(KnowledgeBase).where(KnowledgeBase.slug == slug))

    def list_documents(self, kb_id: uuid.UUID, *, include_deleted: bool = False) -> list[Document]:
        stmt = select(Document).where(Document.kb_id == kb_id)
        if not include_deleted:
            stmt = stmt.where(Document.status == "active")
        return list(self.s.scalars(stmt.order_by(Document.source_name)))

    def get_document(self, doc_id: uuid.UUID, *, lock: bool = False) -> Document | None:
        stmt = select(Document).where(Document.id == doc_id)
        if lock:
            stmt = stmt.with_for_update()
        return self.s.scalar(stmt)

    def find_document(
        self, kb_id: uuid.UUID, source_name: str, *, lock: bool = False
    ) -> Document | None:
        stmt = select(Document).where(Document.kb_id == kb_id, Document.source_name == source_name)
        if lock:
            stmt = stmt.with_for_update()
        return self.s.scalar(stmt)

    def find_active_by_hash(
        self, kb_id: uuid.UUID, sha256: str
    ) -> tuple[Document, DocumentVersion] | None:
        row = self.s.execute(
            select(Document, DocumentVersion)
            .join(
                DocumentVersion,
                and_(
                    DocumentVersion.document_id == Document.id,
                    DocumentVersion.version == Document.current_version,
                ),
            )
            .where(
                Document.kb_id == kb_id,
                Document.status == "active",
                DocumentVersion.content_sha256 == sha256,
                DocumentVersion.index_status == "indexed",
            )
        ).first()
        return (row[0], row[1]) if row else None

    def versions(self, doc_id: uuid.UUID) -> list[DocumentVersion]:
        return list(
            self.s.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc_id)
                .order_by(DocumentVersion.version.desc())
            )
        )

    def current_version(self, doc: Document) -> DocumentVersion | None:
        return self.s.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.version == doc.current_version,
            )
        )

    def next_version_number(self, doc_id: uuid.UUID) -> int:
        return (
            int(
                self.s.scalar(
                    select(func.coalesce(func.max(DocumentVersion.version), 0)).where(
                        DocumentVersion.document_id == doc_id
                    )
                )
                or 0
            )
            + 1
        )

    def chunks_of(self, doc_id: uuid.UUID, *, limit: int, offset: int) -> tuple[list[Chunk], int]:
        base = select(Chunk).where(Chunk.document_id == doc_id, Chunk.is_active)
        total = self.s.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = self.s.scalars(base.order_by(Chunk.chunk_index).limit(limit).offset(offset))
        return list(rows), int(total)

    # ---------------------------------------------------------------- writes
    def replace_document_chunks(self, doc_id: uuid.UUID, keep_version_id: uuid.UUID) -> int:
        """Delete every chunk of the document that does not belong to keep_version_id."""
        res = self.s.execute(
            delete(Chunk).where(
                Chunk.document_id == doc_id, Chunk.document_version_id != keep_version_id
            )
        )
        return int(res.rowcount or 0)

    def delete_version_chunks(self, version_id: uuid.UUID) -> int:
        res = self.s.execute(delete(Chunk).where(Chunk.document_version_id == version_id))
        return int(res.rowcount or 0)

    def delete_document_chunks(self, doc_id: uuid.UUID) -> int:
        res = self.s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        return int(res.rowcount or 0)

    def supersede_other_versions(self, doc_id: uuid.UUID, keep_version_id: uuid.UUID) -> None:
        self.s.execute(
            update(DocumentVersion)
            .where(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.id != keep_version_id,
                DocumentVersion.index_status == "indexed",
            )
            .values(index_status="superseded")
        )

    def count_stale_chunks(self, user: User, model: str) -> int:
        """Readable active chunks that have NO vector for `model` (need reindex)."""
        m = self.get_model(model)
        has = (
            exists().where(ChunkEmbedding.chunk_id == Chunk.id, ChunkEmbedding.model_id == m.id)
            if m is not None
            else literal(False)
        )
        return int(
            self.s.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.is_active, Chunk.kb_id.in_(accessible_kb_ids(user)), ~has)
            )
            or 0
        )

    # ---------------------------------------------------------------- embedding index
    def get_model(self, name: str) -> EmbeddingModel | None:
        return self.s.scalar(select(EmbeddingModel).where(EmbeddingModel.name == name))

    def register_model(
        self, *, name: str, provider: str, dim: int, revision: str | None, config: dict[str, Any]
    ) -> EmbeddingModel:
        """Get-or-create the registry row and its partial HNSW index. Dimension is immutable
        per name: a mismatch is a hard, explicit error (never a silent mixed index)."""
        m = self.get_model(name)
        if m is not None:
            if m.dim != dim:
                raise EmbeddingDimensionMismatch(
                    f"embedding model '{name}' is registered with dim={m.dim} but the provider "
                    f"returns dim={dim}. Use a new model name (new isolated index) or run "
                    f"`scripts/reindex_embeddings.py --recreate` for this model."
                )
            if revision and m.revision and m.revision != revision:
                raise EmbeddingDimensionMismatch(
                    f"embedding model '{name}' index was built from revision {m.revision[:12]}, "
                    f"provider is {revision[:12]}; reindex with --recreate"
                )
            return m
        m = EmbeddingModel(name=name, provider=provider, dim=dim, revision=revision, config=config)
        self.s.add(m)
        self.s.flush()
        self.s.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {DYNAMIC_INDEX_PREFIX}{int(m.id)} ON chunk_embeddings "
                f"USING hnsw ((embedding::vector({int(dim)})) vector_cosine_ops) "
                f"WITH (m = 16, ef_construction = 64) WHERE model_id = {int(m.id)}"
            )
        )
        return m

    def drop_model_vectors(self, model: EmbeddingModel) -> int:
        res = self.s.execute(delete(ChunkEmbedding).where(ChunkEmbedding.model_id == model.id))
        return int(res.rowcount or 0)

    def add_embeddings(
        self,
        model: EmbeddingModel,
        rows: list[tuple[uuid.UUID, list[float], int | None]],
    ) -> None:
        for cid, vec, ntok in rows:
            if len(vec) != model.dim:
                raise EmbeddingDimensionMismatch(
                    f"vector dim {len(vec)} != registered dim {model.dim} for '{model.name}'"
                )
            self.s.merge(
                ChunkEmbedding(chunk_id=cid, model_id=model.id, embedding=vec, token_count=ntok)
            )

    def chunks_missing_model(self, model: EmbeddingModel) -> list[tuple[Chunk, Document]]:
        has = exists().where(
            ChunkEmbedding.chunk_id == Chunk.id, ChunkEmbedding.model_id == model.id
        )
        rows = self.s.execute(
            select(Chunk, Document)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.is_active, Document.status == "active", ~has)
            .order_by(Chunk.document_id, Chunk.chunk_index)
        )
        return [(r[0], r[1]) for r in rows]

    def index_coverage(self, model_name: str) -> tuple[int, int]:
        """(active chunks with a vector for model, all active chunks)."""
        total = self.s.scalar(select(func.count()).select_from(Chunk).where(Chunk.is_active)) or 0
        m = self.get_model(model_name)
        if m is None:
            return 0, int(total)
        have = (
            self.s.scalar(
                select(func.count())
                .select_from(ChunkEmbedding)
                .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
                .where(ChunkEmbedding.model_id == m.id, Chunk.is_active)
            )
            or 0
        )
        return int(have), int(total)

    # ---------------------------------------------------------------- retrieval
    def _searchable(self, user: User, kb_ids: list[uuid.UUID] | None) -> list[Any]:
        conds: list[Any] = [
            Chunk.is_active,
            Document.status == "active",
            Chunk.kb_id.in_(accessible_kb_ids(user)),
        ]
        if kb_ids:
            conds.append(Chunk.kb_id.in_(kb_ids))
        return conds

    _ITER_SUPPORT: dict[str, bool] = {}  # noqa: RUF012  (per-process cache, keyed by db url)

    def supports_iterative_scan(self) -> bool:
        """hnsw.iterative_scan exists since pgvector 0.8.0; older servers silently skip it."""
        key = str(self.s.get_bind().url)
        if key not in self._ITER_SUPPORT:
            v = self.s.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
            parts = tuple(int(x) for x in str(v or "0.0").split(".")[:2])
            self._ITER_SUPPORT[key] = parts >= (0, 8)
        return self._ITER_SUPPORT[key]

    def vector_search(
        self,
        user: User,
        qvec: list[float],
        *,
        model: str,
        limit: int,
        kb_ids: list[uuid.UUID] | None = None,
        ef_search: int = 100,
        iterative_scan: str = "relaxed_order",
    ) -> list[ChunkHit]:
        """Cosine kNN over THIS model's vectors only (model_id filter + its own partial HNSW
        index), ACL filter in the same WHERE. iterative_scan (pgvector >= 0.8) lets HNSW keep
        scanning when filters reject candidates, instead of returning < limit rows."""
        m = self.get_model(model)
        if m is None:
            return []
        if len(qvec) != m.dim:
            raise EmbeddingDimensionMismatch(
                f"query vector dim {len(qvec)} != index dim {m.dim} for '{model}'"
            )
        self.s.execute(
            text("SELECT set_config('hnsw.ef_search', :v, true)"),
            {"v": str(max(ef_search, min(1000, limit)))},
        )
        if iterative_scan != "off" and self.supports_iterative_scan():
            self.s.execute(
                text("SELECT set_config('hnsw.iterative_scan', :v, true)"), {"v": iterative_scan}
            )
        vec_t = Vector(m.dim)
        dist = cast(ChunkEmbedding.embedding, vec_t).cosine_distance(
            cast(literal(qvec, vec_t), vec_t)
        )
        stmt = (
            select(Chunk.id, dist.label("dist"))
            .select_from(ChunkEmbedding)
            .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .where(ChunkEmbedding.model_id == m.id, *self._searchable(user, kb_ids))
            .order_by(dist)
            .limit(limit)
        )
        hits = [ChunkHit(r.id, 1.0 - float(r.dist)) for r in self.s.execute(stmt)]
        # relaxed_order may return slightly out-of-order rows: re-sort (pgvector docs).
        return sorted(hits, key=lambda h: -h.score)

    def keyword_search(
        self, user: User, tokens: list[str], *, limit: int, kb_ids: list[uuid.UUID] | None = None
    ) -> list[ChunkHit]:
        if not tokens:
            return []
        q = " | ".join("'" + t.replace("'", "''").replace("\\", "") + "'" for t in tokens)
        tsq = func.to_tsquery("simple", q)
        rank = func.ts_rank_cd(Chunk.tsv, tsq, 1)  # 1 = divide by 1 + log(doc length)
        stmt = (
            select(Chunk.id, rank.label("rank"))
            .join(Document, Document.id == Chunk.document_id)
            .where(*self._searchable(user, kb_ids), Chunk.tsv.op("@@")(tsq))
            .order_by(rank.desc(), Chunk.id)
            .limit(limit)
        )
        return [ChunkHit(r.id, float(r.rank)) for r in self.s.execute(stmt)]

    def load_chunks(
        self, ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[Chunk, Document, DocumentVersion, KnowledgeBase]]:
        if not ids:
            return {}
        rows = self.s.execute(
            select(Chunk, Document, DocumentVersion, KnowledgeBase)
            .join(Document, Document.id == Chunk.document_id)
            .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
            .join(KnowledgeBase, KnowledgeBase.id == Chunk.kb_id)
            .where(Chunk.id.in_(ids))
        )
        return {r[0].id: (r[0], r[1], r[2], r[3]) for r in rows}
