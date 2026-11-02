"""Stage 3 agent: per-turn agent_runs, tool-call idempotency, langgraph schema

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02 18:00:00

Ownership boundary (docs/adr/ADR-003): this migration only creates the EMPTY schema
`langgraph` and our own tables/columns. The checkpoint tables inside `langgraph` are created
and versioned by langgraph-checkpoint-postgres' PostgresSaver.setup() (scripts/setup_checkpointer.py),
never by Alembic; env.py's include_object ignores them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "status IN ('running','awaiting_approval','completed','aborted','failed')"
_NEW = (
    "status IN ('running','awaiting_approval','completed','refused','aborted','failed','expired')"
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
    # agent_runs: one row per turn
    op.drop_constraint(op.f("uq_agent_runs_thread_id"), "agent_runs", type_="unique")
    op.add_column("agent_runs", sa.Column("turn", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(
        "agent_runs", sa.Column("is_mock", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "agent_runs",
        sa.Column("node_trace", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "agent_runs", sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]")
    )
    op.add_column("agent_runs", sa.Column("assessment", postgresql.JSONB(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "agent_runs", sa.Column("cost_cny", sa.Float(), nullable=False, server_default="0")
    )
    op.create_unique_constraint(
        op.f("uq_agent_runs_thread_turn"), "agent_runs", ["thread_id", "turn"]
    )
    op.drop_constraint(op.f("ck_agent_runs_status_valid"), "agent_runs", type_="check")
    op.create_check_constraint(op.f("ck_agent_runs_status_valid"), "agent_runs", _NEW)
    # tool calls + drafts: idempotency
    op.add_column("tool_calls", sa.Column("call_id", sa.String(length=64), nullable=True))
    op.add_column("tool_calls", sa.Column("idempotency_key", sa.String(length=40), nullable=True))
    op.create_index(op.f("ix_tool_calls_idempotency_key"), "tool_calls", ["idempotency_key"])
    op.add_column(
        "ticket_drafts", sa.Column("idempotency_key", sa.String(length=40), nullable=True)
    )
    op.create_unique_constraint(
        op.f("uq_ticket_drafts_idempotency_key"), "ticket_drafts", ["idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_ticket_drafts_idempotency_key"), "ticket_drafts", type_="unique")
    op.drop_column("ticket_drafts", "idempotency_key")
    op.drop_index(op.f("ix_tool_calls_idempotency_key"), table_name="tool_calls")
    op.drop_column("tool_calls", "idempotency_key")
    op.drop_column("tool_calls", "call_id")
    op.execute("DELETE FROM agent_runs WHERE status IN ('refused','expired') OR turn > 1")
    op.drop_constraint(op.f("ck_agent_runs_status_valid"), "agent_runs", type_="check")
    op.create_check_constraint(op.f("ck_agent_runs_status_valid"), "agent_runs", _OLD)
    op.drop_constraint(op.f("uq_agent_runs_thread_turn"), "agent_runs", type_="unique")
    for c in (
        "cost_cny",
        "tool_call_count",
        "assessment",
        "evidence",
        "node_trace",
        "is_mock",
        "turn",
    ):
        op.drop_column("agent_runs", c)
    op.create_unique_constraint(op.f("uq_agent_runs_thread_id"), "agent_runs", ["thread_id"])
    # the langgraph schema holds checkpointer-owned tables; drop only if it is empty of ours
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
