from __future__ import annotations

import os

import pytest

from opspilot.core.config import Settings

TEST_SECRET = "test-secret-0123456789-abcdefghijklmnopqrstuvwxyz"


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "test",
        "log_json": True,
        "jwt_secret": TEST_SECRET,
        "llm_provider": "mock",
        "embedding_provider": "mock",
        "llm_api_key": "",
        "database_url": os.environ.get(
            "OPSPILOT_TEST_DATABASE_URL",
            "postgresql+psycopg://opspilot:opspilot@127.0.0.1:5432/opspilot_test",
        ),
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()
