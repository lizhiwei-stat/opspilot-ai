"""Stage 4: MCP tool audit, ticket events, draft revisions, return-for-revision decisions

Downgrade is data-preserving: returned drafts/decisions are mapped to rejected.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02 18:21:59.455454
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_D_OLD = "status IN ('pending_approval','approved','rejected','executed','expired')"
_D_NEW = "status IN ('pending_approval','approved','rejected','returned','executed','expired')"
_A_OLD = "decision IN ('approved','rejected')"
_A_NEW = "decision IN ('approved','rejected','returned')"


def upgrade() -> None:
    op.create_table(
        "mcp_tool_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("server", sa.String(length=32), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("user_role", sa.String(length=16), nullable=True),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("call_id", sa.String(length=64), nullable=True),
        sa.Column("jti", sa.String(length=64), nullable=True),
        sa.Column("args_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("args_sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_class", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("output_bytes", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "side IN ('server','client')", name=op.f("ck_mcp_tool_audit_side_valid")
        ),
        sa.CheckConstraint(
            "status IN ('ok','error','denied','invalid','timeout','unavailable','replay')",
            name=op.f("ck_mcp_tool_audit_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_mcp_tool_audit_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_tool_audit")),
    )
    op.create_index(
        "ix_mcp_tool_audit_thread_created",
        "mcp_tool_audit",
        ["thread_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_tool_audit_tool_created", "mcp_tool_audit", ["tool", "created_at"], unique=False
    )
    op.create_index(
        "ix_mcp_tool_audit_user_created", "mcp_tool_audit", ["user_id", "created_at"], unique=False
    )
    op.create_table(
        "ticket_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("draft_id", sa.UUID(), nullable=True),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("approver_id", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_ticket_events_actor_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], name=op.f("fk_ticket_events_approval_id_approvals")
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"], ["users.id"], name=op.f("fk_ticket_events_approver_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["ticket_drafts.id"], name=op.f("fk_ticket_events_draft_id_ticket_drafts")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_events_ticket_id_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_events")),
        sa.UniqueConstraint("approval_id", name="uq_ticket_events_approval_id"),
    )
    op.create_index(
        "ix_ticket_events_ticket_created",
        "ticket_events",
        ["ticket_id", "created_at"],
        unique=False,
    )
    op.add_column(
        "ticket_drafts", sa.Column("revision", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("ticket_drafts", sa.Column("parent_draft_id", sa.UUID(), nullable=True))
    op.add_column("ticket_drafts", sa.Column("target_status", sa.String(length=16), nullable=True))
    op.add_column("ticket_drafts", sa.Column("thread_id", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        op.f("fk_ticket_drafts_parent_draft_id_ticket_drafts"),
        "ticket_drafts",
        "ticket_drafts",
        ["parent_draft_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_ticket_drafts_revision_valid"), "ticket_drafts", "revision >= 1 AND revision <= 10"
    )
    op.drop_constraint(op.f("ck_ticket_drafts_status_valid"), "ticket_drafts", type_="check")
    op.create_check_constraint(op.f("ck_ticket_drafts_status_valid"), "ticket_drafts", _D_NEW)
    op.drop_constraint(op.f("ck_approvals_decision_valid"), "approvals", type_="check")
    op.create_check_constraint(op.f("ck_approvals_decision_valid"), "approvals", _A_NEW)


def downgrade() -> None:
    op.execute("UPDATE approvals SET decision = 'rejected' WHERE decision = 'returned'")
    op.execute("UPDATE ticket_drafts SET status = 'rejected' WHERE status = 'returned'")
    op.drop_constraint(op.f("ck_approvals_decision_valid"), "approvals", type_="check")
    op.create_check_constraint(op.f("ck_approvals_decision_valid"), "approvals", _A_OLD)
    op.drop_constraint(op.f("ck_ticket_drafts_status_valid"), "ticket_drafts", type_="check")
    op.create_check_constraint(op.f("ck_ticket_drafts_status_valid"), "ticket_drafts", _D_OLD)
    op.drop_constraint(op.f("ck_ticket_drafts_revision_valid"), "ticket_drafts", type_="check")
    op.drop_constraint(
        op.f("fk_ticket_drafts_parent_draft_id_ticket_drafts"), "ticket_drafts", type_="foreignkey"
    )
    op.drop_column("ticket_drafts", "thread_id")
    op.drop_column("ticket_drafts", "target_status")
    op.drop_column("ticket_drafts", "parent_draft_id")
    op.drop_column("ticket_drafts", "revision")
    op.drop_index("ix_ticket_events_ticket_created", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index("ix_mcp_tool_audit_user_created", table_name="mcp_tool_audit")
    op.drop_index("ix_mcp_tool_audit_tool_created", table_name="mcp_tool_audit")
    op.drop_index("ix_mcp_tool_audit_thread_created", table_name="mcp_tool_audit")
    op.drop_table("mcp_tool_audit")
