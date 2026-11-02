"""Document ingestion: validate → parse → clean → chunk → embed → store, with versioning,
content-hash dedup, reindex and delete. DB state is changed only in one final transaction
per operation, so chunks and document/version rows can never disagree."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePath

from sqlalchemy.orm import Session

from opspilot.core.config import Settings
from opspilot.core.errors import AppError, ForbiddenError, NotFoundError
from opspilot.core.logging import get_logger
from opspilot.db.models import Chunk, Document, DocumentVersion, KnowledgeBase, User
from opspilot.llm.base import EmbeddingProvider, LLMError, embedder_metadata
from opspilot.rag.chunking import ChunkDraft, chunk_document
from opspilot.rag.parsers import ParsedDocument, ParseError, safe_filename, validate_and_parse
from opspilot.rag.tokenize import to_search_text
from opspilot.repositories.knowledge import KnowledgeRepository

log = get_logger("opspilot.ingestion")


class UnprocessableError(AppError):
    status_code = 422
    code = "unprocessable_document"


@dataclass(frozen=True)
class IngestResult:
    status: str  # indexed | duplicate | failed
    document: Document
    version: DocumentVersion | None
    chunk_count: int
    message: str


def embed_text(title: str, heading: str | None, content: str) -> str:
    """Text actually sent to the embedding model: a short contextual header + content."""
    head = f"{title} > {heading}" if heading else title
    return f"{head}\n{content}"


class IngestionService:
    def __init__(self, session: Session, settings: Settings, embedder: EmbeddingProvider) -> None:
        self.s = session
        self.settings = settings
        self.embedder = embedder
        self.repo = KnowledgeRepository(session)

    # ------------------------------------------------------------------ helpers
    def _kb_for_write(self, user: User, kb_id: uuid.UUID) -> KnowledgeBase:
        kb = self.repo.get_kb(kb_id)
        if kb is None or not self.repo.can_read(user, kb_id):
            raise NotFoundError("知识库不存在或无权访问")
        if not self.repo.can_write(user, kb):
            raise ForbiddenError("没有该知识库的写权限")
        return kb

    def _store_file(self, kb_id: uuid.UUID, sha: str, ext: str, data: bytes) -> str:
        root = Path(self.settings.upload_dir)
        path = root / str(kb_id) / f"{sha}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic on POSIX
        return str(path)

    def _model_row(self):  # type: ignore[no-untyped-def]
        meta = embedder_metadata(self.embedder)
        return self.repo.register_model(
            name=self.embedder.model,
            provider=self.embedder.name,
            dim=self.embedder.dim,
            revision=str(meta.get("revision")) if meta.get("revision") else None,
            config={k: v for k, v in meta.items() if k != "load_s"},
        )

    def _token_counts(self, texts: list[str]) -> list[int | None]:
        fn = getattr(self.embedder, "count_tokens", None)
        return list(fn(texts)) if fn else [None] * len(texts)

    def _build_chunks(
        self, parsed: ParsedDocument, title: str
    ) -> tuple[list[ChunkDraft], list[list[float]]]:
        drafts = chunk_document(
            parsed, chunk_size=self.settings.chunk_size, overlap=self.settings.chunk_overlap
        )
        if not drafts:
            raise ParseError("no_chunks", "解析后没有可索引的内容")
        texts = [embed_text(title, d.heading_path, d.content) for d in drafts]
        vectors: list[list[float]] = []
        bs = self.settings.embedding_batch_size
        for i in range(0, len(texts), bs):
            batch = self.embedder.embed(texts[i : i + bs])
            if len(batch) != len(texts[i : i + bs]) or any(
                len(v) != self.embedder.dim for v in batch
            ):
                raise LLMError("embedding provider returned wrong count or dimension")
            vectors.extend(batch)
        return drafts, vectors

    def _chunk_rows(
        self,
        doc: Document,
        version: DocumentVersion,
        drafts: list[ChunkDraft],
        vectors: list[list[float]],
    ) -> list[Chunk]:
        """Chunk rows + this model's vectors (chunk_embeddings). Real token counts come from the
        embedding model's own tokenizer when it has one; chunks.char_count is characters."""
        model = self._model_row()
        texts = [embed_text(doc.title, d.heading_path, d.content) for d in drafts]
        ntoks = self._token_counts(texts)
        rows = [
            Chunk(
                id=uuid.uuid4(),
                kb_id=doc.kb_id,
                document_id=doc.id,
                document_version_id=version.id,
                chunk_index=d.index,
                content=d.content,
                search_text=to_search_text(f"{d.heading_path or ''}\n{d.content}"),
                page_number=d.page_number,
                heading_path=d.heading_path,
                char_start=d.char_start,
                char_end=d.char_end,
                char_count=len(d.content),
            )
            for d in drafts
        ]
        self.s.add_all(rows)
        self.s.flush()
        self.repo.add_embeddings(
            model,
            [(c.id, v, n) for c, v, n in zip(rows, vectors, ntoks, strict=True)],
        )
        return rows

    # ------------------------------------------------------------------ upload
    def upload(
        self, user: User, kb_id: uuid.UUID, filename: str, data: bytes, title: str | None = None
    ) -> IngestResult:
        kb = self._kb_for_write(user, kb_id)
        name = safe_filename(filename)
        sha = hashlib.sha256(data).hexdigest()
        dup = self.repo.find_active_by_hash(kb.id, sha)
        if dup is not None:
            doc, ver = dup
            return IngestResult(
                "duplicate",
                doc,
                ver,
                ver.chunk_count,
                f"内容与已索引文档《{doc.source_name}》v{ver.version} 完全相同，未重复索引",
            )
        doc = self.repo.find_document(kb.id, name, lock=True)
        if doc is None:
            doc = Document(
                kb_id=kb.id,
                source_name=name,
                title=title or PurePath(name).stem,
                mime_type="application/octet-stream",
                uploaded_by=user.id,
                current_version=0,
            )
            self.s.add(doc)
            self.s.flush()
        elif doc.status == "deleted":
            doc.status = "active"  # re-upload of a deleted name revives the document
        version = DocumentVersion(
            document_id=doc.id,
            version=self.repo.next_version_number(doc.id),
            content_sha256=sha,
            size_bytes=len(data),
            storage_path="",
            index_status="indexing",
            created_by=user.id,
        )
        self.s.add(version)
        self.s.flush()
        try:
            parsed = validate_and_parse(
                name, data, max_bytes=int(self.settings.max_upload_mb * 2**20)
            )
            doc.mime_type = parsed.mime_type
            version.storage_path = self._store_file(kb.id, sha, PurePath(name).suffix.lower(), data)
            drafts, vectors = self._build_chunks(parsed, doc.title)
        except (ParseError, LLMError) as exc:
            code = exc.code if isinstance(exc, ParseError) else "embedding_failed"
            version.index_status, version.error_message = "failed", f"[{code}] {exc}"
            log.warning("ingest_failed", doc=name, code=code)
            return IngestResult("failed", doc, version, 0, str(exc))
        # ---- single transaction (committed by the request unit of work) ----
        self._chunk_rows(doc, version, drafts, vectors)
        removed = self.repo.replace_document_chunks(doc.id, version.id)
        self.repo.supersede_other_versions(doc.id, version.id)
        version.index_status, version.chunk_count = "indexed", len(drafts)
        version.embedding_model = self.embedder.model
        doc.current_version = version.version
        self.s.flush()
        log.info(
            "ingest_indexed",
            doc=name,
            version=version.version,
            chunks=len(drafts),
            removed_old_chunks=removed,
        )
        return IngestResult(
            "indexed",
            doc,
            version,
            len(drafts),
            f"已索引 {len(drafts)} 个分块" + (f"，替换旧版本分块 {removed} 个" if removed else ""),
        )

    # ------------------------------------------------------------------ reindex / delete
    def reindex(self, user: User, doc_id: uuid.UUID) -> IngestResult:
        doc = self.repo.get_document(doc_id, lock=True)
        if doc is None or doc.status != "active":
            raise NotFoundError("文档不存在")
        self._kb_for_write(user, doc.kb_id)
        version = self.repo.current_version(doc)
        if version is None:
            raise UnprocessableError("文档没有成功索引的版本，请重新上传")
        data = Path(version.storage_path).read_bytes()
        parsed = validate_and_parse(
            doc.source_name, data, max_bytes=int(self.settings.max_upload_mb * 2**20)
        )
        try:
            drafts, vectors = self._build_chunks(parsed, doc.title)
        except LLMError as exc:  # old chunks untouched: still consistent
            raise UnprocessableError(f"重新索引失败（原索引保留）：{exc}") from exc
        self.repo.delete_version_chunks(version.id)
        self.s.flush()
        self._chunk_rows(doc, version, drafts, vectors)
        version.chunk_count, version.embedding_model = len(drafts), self.embedder.model
        version.index_status, version.error_message = "indexed", None
        self.s.flush()
        return IngestResult(
            "indexed", doc, version, len(drafts), f"已重新索引 {len(drafts)} 个分块"
        )

    def delete(self, user: User, doc_id: uuid.UUID) -> int:
        doc = self.repo.get_document(doc_id, lock=True)
        if doc is None or doc.status != "active":
            raise NotFoundError("文档不存在")
        self._kb_for_write(user, doc.kb_id)
        removed = self.repo.delete_document_chunks(doc.id)
        doc.status = "deleted"
        self.s.flush()
        return removed
