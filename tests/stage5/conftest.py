"""Stage 5 fixtures: the real FastAPI app on the seeded agent test DB (mock LLM, no paid
calls). KB tests create their own uniquely named KB and never touch the seeded corpus."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from opspilot.main import create_app
from tests.agent.conftest import agent_db  # noqa: F401
from tests.integration.conftest import migrated_db  # noqa: F401


@pytest.fixture(scope="module")
def api(agent_db):  # noqa: F811
    from opspilot.agent import checkpoint

    checkpoint.setup(agent_db["settings"])
    with TestClient(create_app(agent_db["settings"])) as c:
        yield c


def login(c: TestClient, who: str) -> dict[str, str]:
    key = "OPSPILOT_SEED_ADMIN_PASSWORD" if who == "admin" else "OPSPILOT_SEED_USER_PASSWORD"
    r = c.post(
        "/api/v1/auth/login",
        json={"email": f"{who}@opspilot.local", "password": os.environ[key]},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def parse_sse(text: str) -> list[dict]:
    """Minimal SSE parser -> [{'id','event','data'}] (comments / retry lines skipped)."""
    import json

    out = []
    for block in text.split("\n\n"):
        ev: dict = {}
        for line in block.splitlines():
            if not line or line.startswith(":"):
                continue
            k, _, v = line.partition(": ")
            if k in ("id", "event"):
                ev[k] = v
            elif k == "data":
                ev["data"] = json.loads(v)
        if "event" in ev:
            out.append(ev)
    return out
