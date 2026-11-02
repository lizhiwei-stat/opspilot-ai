"""Knowledge-base persistence + retrieval SQL. ACL is applied INSIDE every retrieval query
(kb_id IN accessible_kb_ids(user)) before ORDER BY / LIMIT, never as a post-filter."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, delete, exists, func, or_, select, text, update
from sqlalchemy.orm import Session

from opspilot.db.models import Chunk, Document, DocumentVersion, KbPermission, KnowledgeBase, User

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
        return int(
            self.s.scalar(
                select(func.count())
                .select_from(Chunk)
                .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
                .where(
                    Chunk.kb_id.in_(accessible_kb_ids(user)),
                    DocumentVersion.embedding_model != model,
                )
            )
            or 0
        )

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

    def vector_search(
        self,
        user: User,
        qvec: list[float],
        *,
        model: str,
        limit: int,
        kb_ids: list[uuid.UUID] | None = None,
    ) -> list[ChunkHit]:
        # Larger ef_search so ACL/model filters applied during the HNSW scan still yield `limit`.
        self.s.execute(
            text("SELECT set_config('hnsw.ef_search', :v, true)"),
            {"v": str(max(40, min(1000, limit * 4)))},
        )
        dist = Chunk.embedding.cosine_distance(qvec)
        stmt = (
            select(Chunk.id, dist.label("dist"))
            .join(Document, Document.id == Chunk.document_id)
            .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
            .where(*self._searchable(user, kb_ids), DocumentVersion.embedding_model == model)
            .order_by(dist)
            .limit(limit)
        )
        return [ChunkHit(r.id, 1.0 - float(r.dist)) for r in self.s.execute(stmt)]

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
