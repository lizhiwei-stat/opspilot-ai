"""per-model embedding index (chunk_embeddings) + rename chunks.token_count -> char_count

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02 13:00:00

Why a separate table instead of a second vector(512) column: every new model would otherwise
need a schema change, and the old 1024-dim mock column would block 512-dim bge-small-zh.
chunk_embeddings stores a dimension-less `vector`; each model gets a partial HNSW expression
index ((embedding::vector(dim)) WHERE model_id = id), which is the pgvector-documented way to
index mixed dimensions in one column.

Data safety: existing mock vectors are COPIED (chunks.embedding is kept, not dropped);
downgrade drops only the new table/registry and restores the column name.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MOCK_MODEL = "mock-hash-embedding-v1"


def upgrade() -> None:
    op.create_table(
        "embedding_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("revision", sa.String(length=64), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("dim > 0 AND dim <= 4096", name=op.f("ck_embedding_models_dim_valid")),
        sa.CheckConstraint(
            "provider IN ('mock','local','openai_compatible')",
            name=op.f("ck_embedding_models_provider_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embedding_models")),
        sa.UniqueConstraint("name", name=op.f("uq_embedding_models_name")),
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_chunk_embeddings_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["embedding_models.id"],
            name=op.f("fk_chunk_embeddings_model_id_embedding_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", "model_id", name=op.f("pk_chunk_embeddings")),
    )
    op.create_index(
        op.f("ix_chunk_embeddings_model_id"), "chunk_embeddings", ["model_id"], unique=False
    )
    op.alter_column("chunks", "token_count", new_column_name="char_count")
    # legacy column: no longer written for new chunks (vectors live in chunk_embeddings)
    op.alter_column("chunks", "embedding", nullable=True)

    # Copy existing (mock) vectors so Stage 2 data keeps working unchanged.
    conn = op.get_bind()
    has = conn.execute(sa.text("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")).scalar()
    if has:
        conn.execute(
            sa.text(
                "INSERT INTO embedding_models (name, provider, dim, revision, config) "
                "VALUES (:n, 'mock', 1024, NULL, CAST(:c AS jsonb))"
            ),
            {"n": MOCK_MODEL, "c": '{"note": "backfilled from chunks.embedding by 0003"}'},
        )
        mid = conn.execute(
            sa.text("SELECT id FROM embedding_models WHERE name = :n"), {"n": MOCK_MODEL}
        ).scalar()
        conn.execute(
            sa.text(
                "INSERT INTO chunk_embeddings (chunk_id, model_id, embedding) "
                "SELECT c.id, :mid, c.embedding FROM chunks c "
                "JOIN document_versions v ON v.id = c.document_version_id "
                "WHERE c.embedding IS NOT NULL AND v.embedding_model = :n"
            ),
            {"mid": mid, "n": MOCK_MODEL},
        )
        conn.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS ix_chunk_emb_hnsw_m{int(mid)} ON chunk_embeddings "
                "USING hnsw ((embedding::vector(1024)) vector_cosine_ops) "
                f"WITH (m = 16, ef_construction = 64) WHERE model_id = {int(mid)}"
            )
        )


def downgrade() -> None:
    # Chunks created after 0003 have no legacy vector; restore it from the mock index where
    # possible, otherwise downgrade refuses rather than silently dropping rows.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE chunks c SET embedding = ce.embedding::vector(1024) FROM chunk_embeddings ce "
            "JOIN embedding_models m ON m.id = ce.model_id "
            "WHERE ce.chunk_id = c.id AND c.embedding IS NULL AND m.dim = 1024 AND m.name = :n"
        ),
        {"n": MOCK_MODEL},
    )
    left = conn.execute(sa.text("SELECT count(*) FROM chunks WHERE embedding IS NULL")).scalar()
    if left:
        raise RuntimeError(
            f"{left} chunks have no 1024-dim mock vector; reindex with the mock model before "
            "downgrading below 0003"
        )
    op.alter_column("chunks", "embedding", nullable=False)
    op.alter_column("chunks", "char_count", new_column_name="token_count")
    op.drop_index(op.f("ix_chunk_embeddings_model_id"), table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")  # per-model HNSW indexes go with the table
    op.drop_table("embedding_models")
