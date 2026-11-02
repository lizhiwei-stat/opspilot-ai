from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from opspilot import __version__

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    """Liveness: process is up. Never touches dependencies."""
    return {"status": "alive", "version": __version__}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness: DB, pgvector, migrations, LLM + embedding provider. 503 if any fails."""
    report = request.app.state.health_service.readiness()
    return JSONResponse(report, status_code=200 if report["status"] == "ready" else 503)
