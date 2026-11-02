"""Agent test fixtures: real Postgres (box test DB), the full evals corpus ingested with the
mock embedding, seeded users/tickets, and a graph builder with an in-memory checkpointer."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from opspilot.agent.graph import AgentDeps, build_graph
from opspilot.agent.llm import MockAgentLLM
from opspilot.core.config import Settings
from opspilot.db.models import KnowledgeBase, User
from opspilot.llm.mock import MockEmbedding
from opspilot.services.ingestion import IngestionService
from tests.conftest import make_settings
from tests.integration.conftest import migrated_db  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evals" / "corpus"


@pytest.fixture(scope="session")
def agent_db(migrated_db: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:  # noqa: F811
    os.environ.setdefault("OPSPILOT_SEED_ADMIN_PASSWORD", "Admin#test")
    os.environ.setdefault("OPSPILOT_SEED_USER_PASSWORD", "User#test")
    import importlib.util

    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts/seed_demo.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    settings = make_settings(
        database_url=migrated_db, upload_dir=str(tmp_path_factory.mktemp("up"))
    )
    engine = create_engine(migrated_db)
    with Session(engine) as s:
        s.execute(text("TRUNCATE documents, eval_runs, agent_runs, ticket_drafts CASCADE"))
        s.commit()
        mod.seed(s)
        s.commit()
        admin = s.scalar(select(User).where(User.email == "admin@opspilot.local"))
        svc = IngestionService(s, settings, MockEmbedding())
        for kb_dir in sorted(p for p in CORPUS.iterdir() if p.is_dir() and p.name[0] != "_"):
            kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.slug == kb_dir.name))
            for f in sorted(kb_dir.iterdir()):
                if f.suffix.lower() in {".md", ".txt", ".pdf"}:
                    svc.upload(admin, kb.id, f.name, f.read_bytes())  # type: ignore[arg-type,union-attr]
        s.commit()
        users = {u.email.split("@")[0]: str(u.id) for u in s.scalars(select(User))}
    engine.dispose()
    return {"url": migrated_db, "settings": settings, "users": users}


@pytest.fixture
def agent_settings(agent_db: dict[str, Any]) -> Settings:
    return agent_db["settings"]


@pytest.fixture
def session_factory(agent_db: dict[str, Any]) -> Iterator[Callable[[], Session]]:
    engine = create_engine(agent_db["url"])
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def run_agent(agent_db: dict[str, Any], session_factory: Callable[[], Session]):  # noqa: ANN201
    """run_agent(question, user='alice', **opts) -> final state (in-memory checkpointer)."""

    def _run(
        question: str,
        user: str = "alice",
        *,
        llm: Any = None,
        faults: dict[str, str] | None = None,
        llm_faults: dict[str, str] | None = None,
        settings: Settings | None = None,
        thread_id: str | None = None,
        graph: Any = None,
        turn: int = 1,
    ) -> dict[str, Any]:
        st = settings or agent_db["settings"]
        g = graph or build_graph(
            AgentDeps(
                st, session_factory, MockEmbedding(), llm or MockAgentLLM(), faults, llm_faults
            ),
            checkpointer=InMemorySaver(),
        )
        tid = thread_id or uuid.uuid4().hex
        uid = agent_db["users"][user]
        role = "admin" if user == "admin" else "user"
        return g.invoke(
            {
                "question": question,
                "thread_id": tid,
                "user_id": uid,
                "user_role": role,
                "run_id": None,
                "turn": turn,
            },
            {"configurable": {"thread_id": tid}, "recursion_limit": st.agent_recursion_limit},
        )

    return _run
