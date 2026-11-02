from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from opspilot.core.config import Settings

_DOTENV = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
# Process env wins, then the repo .env, then the local default.
TEST_DATABASE_URL = (
    os.environ.get("OPSPILOT_TEST_DATABASE_URL")
    or _DOTENV.get("OPSPILOT_TEST_DATABASE_URL")
    or "postgresql+psycopg://opspilot:opspilot@127.0.0.1:5432/opspilot_test"
)
TEST_SECRET = "test-secret-0123456789-abcdefghijklmnopqrstuvwxyz"


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "test",
        "log_json": True,
        "jwt_secret": TEST_SECRET,
        "llm_provider": "mock",
        "embedding_provider": "mock",
        "llm_api_key": "",
        "database_url": TEST_DATABASE_URL,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()
