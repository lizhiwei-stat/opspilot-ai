"""Verifies the migrated schema really supports pgvector + full-text, constraints and indexes."""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opspilot.db.models import Chunk, Document, DocumentVersion, KnowledgeBase, Ticket, User
from opspilot.llm.mock import MockEmbedding

EXPECTED_TABLES = {
    "users",
    "knowledge_bases",
    "kb_permissions",
    "documents",
    "document_versions",
    "chunks",
    "chunk_embeddings",
    "embedding_models",
    "tickets",
    "ticket_drafts",
    "approvals",
    "agent_runs",
    "agent_steps",
    "tool_calls",
    "eval_runs",
    "eval_case_results",
    "audit_logs",
    "alembic_version",
}


def test_all_tables_and_extension(db: Session) -> None:
    names = set(db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).all())
    assert names == EXPECTED_TABLES
    assert db.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))


def test_indexes_exist(db: Session) -> None:
    rows = dict(
        db.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='chunks'")
        ).all()
    )
    assert "USING hnsw" in rows["ix_chunks_embedding_hnsw"]
    assert "vector_cosine_ops" in rows["ix_chunks_embedding_hnsw"]
    assert "USING gin" in rows["ix_chunks_tsv"]


def _make_doc(s: Session) -> tuple[KnowledgeBase, Document, DocumentVersion]:
    u = User(email=f"{uuid.uuid4().hex}@t.local", display_name="t", password_hash="x")
    s.add(u)
    s.flush()
    kb = KnowledgeBase(slug=uuid.uuid4().hex[:12], name="kb", owner_id=u.id)
    s.add(kb)
    s.flush()
    d = Document(
        kb_id=kb.id,
        source_name="a.md",
        title="A",
        mime_type="text/markdown",
        uploaded_by=u.id,
        current_version=1,
    )
    s.add(d)
    s.flush()
    v = DocumentVersion(
        document_id=d.id,
        version=1,
        content_sha256="0" * 64,
        size_bytes=1,
        storage_path="x",
        created_by=u.id,
    )
    s.add(v)
    s.flush()
    return kb, d, v


def test_vector_and_fulltext_search_roundtrip(db: Session) -> None:
    kb, d, v = _make_doc(db)
    emb = MockEmbedding()
    texts = ["order api returns http 500 after upgrade", "annual leave policy for staff"]
    for i, (t, e) in enumerate(zip(texts, emb.embed(texts), strict=True)):
        db.add(
            Chunk(
                kb_id=kb.id,
                document_id=d.id,
                document_version_id=v.id,
                chunk_index=i,
                content=t,
                search_text=t,
                embedding=e,
                char_start=0,
                char_end=len(t),
            )
        )
    db.flush()
    q = emb.embed(["http 500 order api"])[0]
    top = db.scalars(
        select(Chunk.content)
        .where(Chunk.kb_id == kb.id)
        .order_by(Chunk.embedding.cosine_distance(q))
        .limit(1)
    ).one()
    assert top == texts[0]
    hit = db.scalars(
        select(Chunk.content).where(
            Chunk.kb_id == kb.id, Chunk.tsv.op("@@")(func.plainto_tsquery("simple", "leave"))
        )
    ).all()
    assert hit == [texts[1]]
    db.rollback()


def test_check_constraint_enforced(db: Session) -> None:
    u = User(email=f"{uuid.uuid4().hex}@t.local", display_name="t", password_hash="x")
    db.add(u)
    db.flush()
    db.add(
        Ticket(
            number="T-1", title="t", description="d", category="c", priority="P9", reporter_id=u.id
        )
    )
    with pytest.raises(IntegrityError, match="priority_valid"):
        db.flush()
    db.rollback()


def test_document_version_unique(db: Session) -> None:
    _, d, v = _make_doc(db)
    db.add(
        DocumentVersion(
            document_id=d.id,
            version=1,
            content_sha256="1" * 64,
            size_bytes=1,
            storage_path="y",
            created_by=v.created_by,
        )
    )
    with pytest.raises(IntegrityError, match="uq_document_versions_doc_version"):
        db.flush()
    db.rollback()
