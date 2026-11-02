from __future__ import annotations

from fastapi import APIRouter, Depends

from opspilot import STAGE, __version__
from opspilot.api.deps import SettingsDep, require_role
from opspilot.api.schemas import VersionOut

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/version", response_model=VersionOut)
def version(settings: SettingsDep) -> VersionOut:
    return VersionOut(
        name="OpsPilot AI",
        version=__version__,
        stage=STAGE,
        env=settings.env,
        llm_provider=settings.llm_provider,
        llm_model="mock-llm-v1" if settings.llm_provider == "mock" else settings.llm_model,
        embedding_provider=settings.embedding_provider,
        embedding_model=(
            "mock-hash-embedding-v1"
            if settings.embedding_provider == "mock"
            else settings.embedding_model
        ),
        embedding_dim=settings.embedding_dim,
        rag={
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "rrf_k": settings.rrf_k,
            "reranker": "lexical-overlap-v1",
            "qa_min_evidence": settings.qa_min_evidence,
        },
    )


@router.get("/admin-ping", dependencies=[Depends(require_role("admin"))])
def admin_ping() -> dict[str, str]:
    """Admin-only probe used to verify role enforcement end-to-end."""
    return {"status": "ok", "scope": "admin"}
