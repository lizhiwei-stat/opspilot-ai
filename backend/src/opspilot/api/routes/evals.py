from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from opspilot.api.deps import CurrentUser, DbDep
from opspilot.core.errors import NotFoundError
from opspilot.db.models import EvalCaseResult, EvalRun

router = APIRouter(prefix="/api/v1/evals", tags=["evals"])


def _run(r: EvalRun) -> dict[str, object]:
    return {
        "id": str(r.id),
        "name": r.name,
        "dataset_name": r.dataset_name,
        "dataset_sha256": r.dataset_sha256,
        "retriever": r.retriever,
        "llm_provider": r.llm_provider,
        "git_commit": r.git_commit,
        "config": r.config,
        "metrics": r.metrics,
        "status": r.status,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


@router.get("/runs")
def list_runs(_: CurrentUser, db: DbDep, limit: int = 50) -> list[dict[str, object]]:
    rows = db.scalars(select(EvalRun).order_by(EvalRun.started_at.desc()).limit(min(limit, 200)))
    return [_run(r) for r in rows]


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, _: CurrentUser, db: DbDep) -> dict[str, object]:
    r = db.get(EvalRun, run_id)
    if r is None:
        raise NotFoundError("评测记录不存在")
    cases = db.scalars(
        select(EvalCaseResult)
        .where(EvalCaseResult.eval_run_id == run_id)
        .order_by(EvalCaseResult.case_id)
    )
    return {
        **_run(r),
        "cases": [
            {
                "case_id": c.case_id,
                "label_source": c.label_source,
                "recall_at_5": c.recall_at_5,
                "latency_ms": c.latency_ms,
                "details": c.details,
            }
            for c in cases
        ],
    }
