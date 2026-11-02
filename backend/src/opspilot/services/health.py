"""Readiness checks. Each check is isolated: one failing dependency never masks the others."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from sqlalchemy import Engine, text

from opspilot.llm.base import EmbeddingProvider, LLMProvider


@dataclass
class CheckResult:
    name: str
    ok: bool
    latency_ms: float
    detail: str


def _timed(name: str, fn: Callable[[], str]) -> CheckResult:
    start = time.perf_counter()
    try:
        detail = fn()
        ok = True
    except Exception as exc:
        detail, ok = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}", False
    return CheckResult(name, ok, round((time.perf_counter() - start) * 1000, 2), detail)


class HealthService:
    def __init__(self, engine: Engine, llm: LLMProvider, embedding: EmbeddingProvider) -> None:
        self.engine = engine
        self.llm = llm
        self.embedding = embedding

    def _database(self) -> str:
        with self.engine.connect() as conn:
            version = conn.execute(text("SHOW server_version")).scalar_one()
        return f"postgres {version}"

    def _pgvector(self) -> str:
        with self.engine.connect() as conn:
            ver = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
            if ver is None:
                raise RuntimeError("extension 'vector' not installed (run alembic upgrade head)")
            dist = conn.execute(text("SELECT '[1,0,0]'::vector <=> '[0,1,0]'::vector")).scalar()
        return f"pgvector {ver}, cosine_distance_probe={float(dist):.1f}"

    def _migrations(self) -> str:
        with self.engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        return f"alembic revision {rev}"

    def _llm(self) -> str:
        h = self.llm.health()
        if not h.ok:
            raise RuntimeError(h.detail)
        return f"{h.provider}/{h.model}: {h.detail}"

    def _embedding(self) -> str:
        vec = self.embedding.embed(["health probe"])[0] if self.embedding.name == "mock" else None
        if vec is not None and len(vec) != self.embedding.dim:
            raise RuntimeError("embedding dim mismatch")
        return f"{self.embedding.name}/{self.embedding.model} dim={self.embedding.dim}"

    def readiness(self) -> dict[str, object]:
        db = _timed("database", self._database)
        if db.ok:
            db_checks = [_timed("pgvector", self._pgvector), _timed("migrations", self._migrations)]
        else:
            # Don't wait for the same connection timeout two more times.
            db_checks = [
                CheckResult(name, False, 0.0, "skipped: database unavailable")
                for name in ("pgvector", "migrations")
            ]
        checks = [db, *db_checks, _timed("llm", self._llm), _timed("embedding", self._embedding)]
        return {
            "status": "ready" if all(c.ok for c in checks) else "not_ready",
            "checks": [asdict(c) for c in checks],
        }
