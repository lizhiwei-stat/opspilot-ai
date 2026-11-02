"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import sessionmaker

from opspilot import __version__
from opspilot.api.routes import agent, auth, evals, health, knowledge, search, system
from opspilot.core.config import Settings, get_settings
from opspilot.core.errors import register_error_handlers
from opspilot.core.logging import configure_logging, get_logger
from opspilot.core.middleware import RequestContextMiddleware
from opspilot.core.redact import register_secret
from opspilot.db.session import build_engine
from opspilot.llm.factory import build_embedding_or_degraded, build_llm
from opspilot.services.health import HealthService


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = app.state.settings
    if s.agent_checkpoint_auto_setup:
        # before serving: PostgresSaver.setup() uses CREATE INDEX CONCURRENTLY, which would
        # wait on request transactions if it ran lazily inside a request
        import anyio

        from opspilot.agent.checkpoint import setup

        try:
            await anyio.to_thread.run_sync(setup, s)
        except Exception as exc:  # DB down at boot: agent endpoints fail later, app still boots
            get_logger().warning("checkpointer_setup_failed", error=type(exc).__name__)
    yield
    pool = getattr(app.state, "agent_pool", None)  # LangGraph checkpointer pool (lazy)
    if pool is not None:
        pool.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(
        title="OpsPilot AI API",
        version=__version__,
        description="Production-oriented AI Support & Ticket Resolution Platform",
        lifespan=_lifespan,
    )
    app.state.settings = settings
    register_secret(settings.llm_api_key.get_secret_value())
    app.state.llm = build_llm(settings)
    app.state.embedding = build_embedding_or_degraded(settings)
    engine = build_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.health_service = HealthService(
        engine, app.state.llm, app.state.embedding, timeout_s=settings.ready_timeout_s
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    for r in (
        health.router,
        system.router,
        auth.router,
        knowledge.router,
        search.router,
        evals.router,
        agent.router,
    ):
        app.include_router(r)
    get_logger().info(
        "app_started", env=settings.env, llm=settings.llm_provider, emb=settings.embedding_provider
    )
    return app
