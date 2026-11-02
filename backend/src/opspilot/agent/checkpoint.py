"""LangGraph Postgres checkpointer, isolated in its own schema.

Ownership boundary (ADR-003):
  * Alembic owns schema `public` (business tables) and creates the EMPTY schema `langgraph`.
  * PostgresSaver.setup() owns the tables inside `langgraph` (checkpoints, checkpoint_blobs,
    checkpoint_writes, checkpoint_migrations) and its own migration versioning.
  * Alembic's env.py ignores the `langgraph` schema, so autogenerate never touches it.

CLI:  python -m opspilot.agent.checkpoint setup   (idempotent; run after `alembic upgrade`)
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from opspilot.core.config import Settings, get_settings

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")  # no pickle fallback on load


def conninfo(settings: Settings) -> str:
    url = re.sub(r"^postgresql\+psycopg://", "postgresql://", settings.database_url)
    schema = settings.agent_checkpoint_schema
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", schema):
        raise ValueError("invalid checkpoint schema name")
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}options=-csearch_path%3D{schema}"


def open_pool(settings: Settings, max_size: int = 8) -> ConnectionPool:
    return ConnectionPool(
        conninfo(settings),
        min_size=1,
        max_size=max_size,
        open=True,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )


@contextmanager
def checkpointer(settings: Settings) -> Iterator[PostgresSaver]:
    pool = open_pool(settings)
    try:
        yield PostgresSaver(pool)  # type: ignore[arg-type]
    finally:
        pool.close()


def setup(settings: Settings) -> None:
    """Create/upgrade the checkpointer tables inside the `langgraph` schema (idempotent)."""
    import psycopg

    schema = settings.agent_checkpoint_schema
    base = re.sub(r"^postgresql\+psycopg://", "postgresql://", settings.database_url)
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')  # also created by Alembic 0004
    with checkpointer(settings) as saver:
        saver.setup()


def main(argv: list[str]) -> int:
    if argv[1:] != ["setup"]:
        print("usage: python -m opspilot.agent.checkpoint setup")
        return 2
    setup(get_settings())
    print("langgraph checkpointer ready")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
