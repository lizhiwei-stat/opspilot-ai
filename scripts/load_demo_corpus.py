"""Ingest the synthetic eval corpus (evals/corpus/<kb-slug>/*) through the real IngestionService.

Idempotent: unchanged files are reported as "duplicate" (content-hash dedup).
Runs seed_demo first so users/KBs exist. Uses the configured embedding provider
(mock by default — real providers need OPSPILOT_ALLOW_PAID_API=true).

Usage: uv run python scripts/load_demo_corpus.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from opspilot.core.config import get_settings
from opspilot.db.models import KnowledgeBase, User
from opspilot.db.session import build_engine
from opspilot.llm.factory import build_embedding
from opspilot.services.ingestion import IngestionService
from seed_demo import seed

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"
EXTS = {".md", ".txt", ".pdf"}


def load(session: Session, corpus: Path = CORPUS) -> list[tuple[str, str, str, int]]:
    settings = get_settings()
    seed(session)
    admin = session.scalar(select(User).where(User.email == "admin@opspilot.local"))
    assert admin is not None
    svc = IngestionService(session, settings, build_embedding(settings))
    out = []
    for kb_dir in sorted(p for p in corpus.iterdir() if p.is_dir() and not p.name.startswith("_")):
        kb = session.scalar(select(KnowledgeBase).where(KnowledgeBase.slug == kb_dir.name))
        if kb is None:
            raise SystemExit(f"knowledge base {kb_dir.name!r} missing (seed_demo.py out of sync)")
        for f in sorted(kb_dir.iterdir()):
            if f.suffix.lower() not in EXTS:
                continue
            r = svc.upload(admin, kb.id, f.name, f.read_bytes())
            out.append((kb.slug, f.name, r.status, r.chunk_count))
    return out


def main() -> int:
    engine = build_engine(get_settings())
    with Session(engine) as session, session.begin():
        rows = load(session)
    for slug, name, status, n in rows:
        print(f"{slug:16} {name:42} {status:9} chunks={n}")
    failed = [r for r in rows if r[2] == "failed"]
    print(f"total={len(rows)} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
