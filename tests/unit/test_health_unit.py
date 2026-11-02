from sqlalchemy import create_engine

from opspilot.llm.mock import MockEmbedding, MockLLM
from opspilot.services.health import HealthService


def test_readiness_reports_db_down_without_raising() -> None:
    # Port 9 is closed: every DB check must fail fast, provider checks still pass.
    engine = create_engine(
        "postgresql+psycopg://x:y@127.0.0.1:9/none", connect_args={"connect_timeout": 1}
    )
    report = HealthService(engine, MockLLM(), MockEmbedding()).readiness()
    by = {c["name"]: c for c in report["checks"]}  # type: ignore[index]
    assert report["status"] == "not_ready"
    assert not by["database"]["ok"] and not by["pgvector"]["ok"]
    assert by["pgvector"]["detail"] == "skipped: database unavailable"
    assert by["llm"]["ok"] and by["embedding"]["ok"]


def test_readiness_overall_deadline() -> None:
    import time

    from opspilot.services.health import HealthService as HS

    class SlowHealth(HS):
        def _run_checks(self) -> dict[str, object]:
            time.sleep(2)
            return {"status": "ready", "checks": []}

    engine = create_engine("postgresql+psycopg://x:y@127.0.0.1:9/none")
    svc = SlowHealth(engine, MockLLM(), MockEmbedding(), timeout_s=0.3)
    t0 = time.perf_counter()
    report = svc.readiness()
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0
    assert report["status"] == "not_ready" and report["timed_out"] is True
    assert all("timeout" in c["detail"] for c in report["checks"])  # type: ignore[union-attr]
