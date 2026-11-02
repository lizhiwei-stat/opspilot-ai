"""Integration tests run against a REAL PostgreSQL + pgvector, migrated with Alembic.

The test database (OPSPILOT_TEST_DATABASE_URL) is downgraded to base and upgraded to head
once per session, so the migration itself is exercised every run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session

from opspilot.core.config import Settings
from opspilot.main import create_app
from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


def _db_url() -> str:
    return make_settings().database_url


@pytest.fixture(scope="session")
def migrated_db() -> str:
    url = _db_url()
    # Safety: this fixture DROPS every table. Refuse anything that is not a throwaway DB.
    db_name = make_url(url).database or ""
    if not db_name.endswith(("_test", "_rt")):
        raise RuntimeError(f"refusing to reset non-test database {db_name!r}")
    try:
        with create_engine(url, connect_args={"connect_timeout": 2}).connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        if os.environ.get("OPSPILOT_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"test database unavailable: {type(exc).__name__}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.cmd_opts = type("o", (), {"x": [f"db_url={url}"]})()  # type: ignore[assignment]
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
def test_settings(migrated_db: str) -> Settings:
    return make_settings(database_url=migrated_db)


@pytest.fixture
def db(test_settings: Settings) -> Iterator[Session]:
    engine = create_engine(test_settings.database_url)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture
def seeded(db: Session) -> Session:
    os.environ.setdefault("OPSPILOT_SEED_ADMIN_PASSWORD", "Admin#test")
    os.environ.setdefault("OPSPILOT_SEED_USER_PASSWORD", "User#test")
    import importlib.util

    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts/seed_demo.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.seed(db)
    db.commit()
    return db


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as c:
        yield c
