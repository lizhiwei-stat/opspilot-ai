from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from opspilot.core.config import get_settings
from opspilot.db import models  # noqa: F401  (register tables on metadata)
from opspilot.db.base import Base
from opspilot.db.models import DYNAMIC_INDEX_PREFIX

target_metadata = Base.metadata
if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Ignore objects alembic does not own: per-model HNSW indexes (created by the reindex
    path) and anything reflected outside our metadata (e.g. a future LangGraph checkpointer
    schema)."""
    if type_ == "index" and name and name.startswith(DYNAMIC_INDEX_PREFIX):
        return False
    # LangGraph checkpointer tables live in schema `langgraph`, owned by PostgresSaver.setup()
    if getattr(obj, "schema", None) == "langgraph":
        return False
    return not (type_ == "table" and reflected and compare_to is None)


def _url() -> str:
    return context.get_x_argument(as_dictionary=True).get("db_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
