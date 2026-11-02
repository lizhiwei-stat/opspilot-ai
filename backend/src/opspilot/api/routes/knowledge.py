from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel

from opspilot.api.deps import CurrentUser, DbDep, SettingsDep
from opspilot.core.errors import AppError, NotFoundError
from opspilot.db.models import Document
from opspilot.repositories.knowledge import KnowledgeRepository
from opspilot.services.ingestion import IngestionService

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class KbOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    visibility: str
    document_count: int
    chunk_count: int
    can_write: bool


class VersionOut(BaseModel):
    version: int
    content_sha256: str
    size_bytes: int
    index_status: str
    error_message: str | None
    chunk_count: int
    embedding_model: str | None
    created_at: datetime


class DocumentOut(BaseModel):
    id: uuid.UUID
    kb_id: uuid.UUID
    source_name: str
    title: str
    mime_type: str
    status: str
    current_version: int
    latest_status: str | None
    latest_error: str | None
    chunk_count: int
    updated_at: datetime
    versions: list[VersionOut] | None = None


def _doc_out(repo: KnowledgeRepository, d: Document, with_versions: bool = False) -> DocumentOut:
    vers = repo.versions(d.id)
    cur = next((v for v in vers if v.version == d.current_version), None)
    latest = vers[0] if vers else None
    return DocumentOut(
        id=d.id,
        kb_id=d.kb_id,
        source_name=d.source_name,
        title=d.title,
        mime_type=d.mime_type,
        status=d.status,
        current_version=d.current_version,
        latest_status=latest.index_status if latest else None,
        latest_error=latest.error_message if latest else None,
        chunk_count=cur.chunk_count if cur else 0,
        updated_at=d.updated_at,
        versions=[VersionOut.model_validate(v, from_attributes=True) for v in vers]
        if with_versions
        else None,
    )


def _readable_doc(repo: KnowledgeRepository, user: Any, doc_id: uuid.UUID) -> Document:
    d = repo.get_document(doc_id)
    if d is None or d.status != "active" or not repo.can_read(user, d.kb_id):
        raise NotFoundError("文档不存在或无权访问")
    return d


@router.get("/knowledge-bases", response_model=list[KbOut])
def list_kbs(user: CurrentUser, db: DbDep) -> list[KbOut]:
    repo = KnowledgeRepository(db)
    return [
        KbOut(
            id=kb.id,
            slug=kb.slug,
            name=kb.name,
            description=kb.description,
            visibility=kb.visibility,
            document_count=dc,
            chunk_count=cc,
            can_write=repo.can_write(user, kb),
        )
        for kb, dc, cc in repo.list_kbs(user)
    ]


@router.get("/knowledge-bases/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(kb_id: uuid.UUID, user: CurrentUser, db: DbDep) -> list[DocumentOut]:
    repo = KnowledgeRepository(db)
    if repo.get_kb(kb_id) is None or not repo.can_read(user, kb_id):
        raise NotFoundError("知识库不存在或无权访问")
    return [_doc_out(repo, d) for d in repo.list_documents(kb_id)]


@router.post("/knowledge-bases/{kb_id}/documents")
async def upload_document(
    kb_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    file: UploadFile = File(...),  # noqa: B008
    title: str | None = Form(default=None),
) -> dict[str, object]:
    limit = int(settings.max_upload_mb * 2**20)
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge(f"文件超过 {settings.max_upload_mb} MB 上限")
    svc = IngestionService(db, settings, request.app.state.embedding)
    r = svc.upload(user, kb_id, file.filename or "", data, title=title)
    repo = KnowledgeRepository(db)
    return {
        "status": r.status,
        "message": r.message,
        "chunk_count": r.chunk_count,
        "document": _doc_out(repo, r.document, with_versions=True).model_dump(mode="json"),
    }


@router.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: uuid.UUID, user: CurrentUser, db: DbDep) -> DocumentOut:
    repo = KnowledgeRepository(db)
    return _doc_out(repo, _readable_doc(repo, user, doc_id), with_versions=True)


@router.get("/documents/{doc_id}/chunks")
def list_chunks(
    doc_id: uuid.UUID,
    user: CurrentUser,
    db: DbDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    repo = KnowledgeRepository(db)
    _readable_doc(repo, user, doc_id)
    rows, total = repo.chunks_of(doc_id, limit=limit, offset=offset)
    return {
        "total": total,
        "items": [
            {
                "id": str(c.id),
                "chunk_index": c.chunk_index,
                "content": c.content,
                "page_number": c.page_number,
                "heading_path": c.heading_path,
                "char_start": c.char_start,
                "char_end": c.char_end,
                "chars": len(c.content),
                "search_tokens": c.search_text.split()[:40],
            }
            for c in rows
        ],
    }


@router.post("/documents/{doc_id}/reindex")
def reindex_document(
    doc_id: uuid.UUID, request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> dict[str, object]:
    r = IngestionService(db, settings, request.app.state.embedding).reindex(user, doc_id)
    return {"status": r.status, "message": r.message, "chunk_count": r.chunk_count}


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: uuid.UUID, request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> dict[str, object]:
    removed = IngestionService(db, settings, request.app.state.embedding).delete(user, doc_id)
    return {"status": "deleted", "removed_chunks": removed}
