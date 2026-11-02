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
