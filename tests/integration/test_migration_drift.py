"""ORM models and Alembic head must agree (Stage 3 added migration 0004)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from opspilot.db import models  # noqa: F401
from opspilot.db.base import Base


def test_models_match_migrations(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    with engine.connect() as conn:
        from opspilot.db.models import DYNAMIC_INDEX_PREFIX

        def include(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
            if type_ == "index" and name and name.startswith(DYNAMIC_INDEX_PREFIX):
                return False
            return not (type_ == "table" and reflected and compare_to is None)

        ctx = MigrationContext.configure(conn, opts={"include_object": include})
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diff == [], diff
