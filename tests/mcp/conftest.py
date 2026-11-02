"""Stage 4 fixtures: the agent test DB (seeded + corpus ingested) and REAL MCP servers
launched as stdio subprocesses through MCPHub (no mocks of the protocol)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from opspilot.agent import checkpoint
from opspilot.agent.llm import MockAgentLLM
from opspilot.agent.service import AgentService
from opspilot.db.models import User
from opspilot.llm.mock import MockEmbedding
from opspilot.mcp.client import CallerCtx, MCPHub
from tests.agent.conftest import agent_db, session_factory  # noqa: F401
from tests.conftest import make_settings
from tests.integration.conftest import migrated_db  # noqa: F401


@pytest.fixture(scope="module")
def mcp_settings(agent_db):  # noqa: F811
    s = make_settings(
        database_url=agent_db["url"],
        upload_dir=agent_db["settings"].upload_dir,
        agent_tool_transport="mcp",
        mcp_call_timeout_s=4.0,
    )
    checkpoint.setup(s)
    return s


@pytest.fixture(scope="module")
def hub(mcp_settings, agent_db) -> Iterator[MCPHub]:  # noqa: F811
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(agent_db["url"])
    h = MCPHub(mcp_settings, session_factory=sessionmaker(eng, expire_on_commit=False))
    h.warm()
    yield h
    h.close()
    eng.dispose()


def caller(agent_db, name: str, role: str | None = None, **kw: Any) -> CallerCtx:  # noqa: F811
    r = role or ("admin" if name == "admin" else "user")
    return CallerCtx(
        agent_db["users"][name],
        r,
        kw.get("thread_id", "t-test-mcp"),
        kw.get("run_id"),
        kw.get("call_id"),
    )


def user(sf, name: str) -> User:
    with sf() as s:
        return s.scalar(select(User).where(User.email == f"{name}@opspilot.local"))


@contextmanager
def process(settings, sf, llm=None):
    """One backend 'process': own pool + saver + graph. MCP hubs are shared per DB."""
    pool = checkpoint.open_pool(settings, max_size=2)
    try:
        yield AgentService(
            settings, sf, MockEmbedding(), PostgresSaver(pool), llm=llm or MockAgentLLM()
        )
    finally:
        pool.close()
