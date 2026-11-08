"""Migration 0006 up/down on a DISPOSABLE database (created and dropped here; the main
dev DB and the shared test DB are never downgraded by this test)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parents[2]


def test_0006_upgrade_downgrade_preserves_users() -> None:
    base = make_url(make_settings().database_url)
    if not (base.database or "").endswith(("_test", "_rt")):
        pytest.skip("not a test database url")
    name = f"opspilot_s5rb_{uuid.uuid4().hex[:6]}_test"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"cannot create disposable db: {type(exc).__name__}")
    url = base.set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.cmd_opts = type("o", (), {"x": [f"db_url={url}"]})()  # type: ignore[assignment]
    eng = create_engine(url)
    try:
        command.upgrade(cfg, "head")
        with eng.begin() as c:
            c.execute(
                text(
                    "INSERT INTO users (id,email,display_name,password_hash,role,is_active,created_at,"
                    "updated_at) VALUES (gen_random_uuid(),'s@x.io','S','h','support',true,now(),now())"
                )
            )
        command.downgrade(cfg, "0005")
        with eng.connect() as c:
            assert c.scalar(text("SELECT role FROM users WHERE email='s@x.io'")) == "user"
            assert c.scalar(text("SELECT to_regclass('agent_events')")) is None
        command.upgrade(cfg, "head")
        with eng.connect() as c:
            assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0006"
    finally:
        eng.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
