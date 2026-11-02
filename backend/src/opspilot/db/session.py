"""Sync SQLAlchemy 2 engine + per-request session (psycopg 3). See ARCHITECTURE.md ADR-03."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from opspilot.core.config import Settings


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.db_connect_timeout_s},
    )


def get_db(request: Request) -> Iterator[Session]:
    """Unit of work per request: commit on success, rollback on any exception."""
    session: Session = request.app.state.sessionmaker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
