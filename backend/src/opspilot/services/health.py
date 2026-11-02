"""Readiness checks. Each check is isolated: one failing dependency never masks the others."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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
    def __init__(
        self,
        engine: Engine,
        llm: LLMProvider,
        embedding: EmbeddingProvider,
        *,
        timeout_s: float = 3.0,
    ) -> None:
        self.engine = engine
        self.llm = llm
        self.embedding = embedding
        self.timeout_s = timeout_s
        # Bounded pool: a hung DB probe can occupy at most 2 threads, never the event loop.
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ready-probe")

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

    def _run_checks(self) -> dict[str, object]:
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

    def readiness(self) -> dict[str, object]:
        """Run all checks under one overall deadline (OPSPILOT_READY_TIMEOUT_S)."""
        start = time.perf_counter()
        future = self._pool.submit(self._run_checks)
        try:
            return future.result(timeout=self.timeout_s)
        except FutureTimeout:
            elapsed = round((time.perf_counter() - start) * 1000, 2)
            detail = f"timeout: readiness checks exceeded {self.timeout_s}s"
            return {
                "status": "not_ready",
                "checks": [
                    asdict(CheckResult(n, False, elapsed, detail))
                    for n in ("database", "pgvector", "migrations", "llm", "embedding")
                ],
                "timed_out": True,
            }
