"""Build / refresh ONE embedding model's isolated index (chunk_embeddings rows for that model).

    uv run python scripts/reindex_embeddings.py --provider local      # bge-small-zh (CPU)
    uv run python scripts/reindex_embeddings.py --provider mock
    uv run python scripts/reindex_embeddings.py --provider local --recreate   # drop+rebuild

Only the selected model's vectors are written; chunks, other models' vectors (e.g. the
Stage 2 mock index) and documents are never touched. Real token counts come from the
model's own tokenizer when it has one (local models); mock stores NULL.
A paid provider is refused here unless the gateway guards pass (see llm/gateway.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from opspilot.core.config import Settings, get_settings
from opspilot.db.session import build_engine
from opspilot.llm.base import EmbeddingProvider, embedder_metadata
from opspilot.llm.factory import build_embedding
from opspilot.repositories.knowledge import KnowledgeRepository
from opspilot.services.ingestion import embed_text


def ensure_index(
    session: Session, settings: Settings, embedder: EmbeddingProvider, *, recreate: bool = False
) -> dict[str, object]:
    repo = KnowledgeRepository(session)
    meta = embedder_metadata(embedder)
    existing = repo.get_model(embedder.model)
    if existing is not None and recreate:
        repo.drop_model_vectors(existing)
        session.delete(existing)
        session.flush()
    model = repo.register_model(
        name=embedder.model,
        provider=embedder.name,
        dim=embedder.dim,
        revision=str(meta.get("revision")) if meta.get("revision") else None,
        config={k: v for k, v in meta.items() if k != "load_s"},
    )
    todo = repo.chunks_missing_model(model)
    bs = getattr(embedder, "batch_size", settings.embedding_batch_size)
    count_fn = getattr(embedder, "count_tokens", None)
    t0 = time.perf_counter()
    n_tok = 0
    for i in range(0, len(todo), bs):
        batch = todo[i : i + bs]
        texts = [embed_text(d.title, c.heading_path, c.content) for c, d in batch]
        vecs = embedder.embed(texts)
        toks = list(count_fn(texts)) if count_fn else [None] * len(texts)
        n_tok += sum(t for t in toks if t)
        repo.add_embeddings(
            model, [(c.id, v, t) for (c, _), v, t in zip(batch, vecs, toks, strict=True)]
        )
    session.flush()
    secs = time.perf_counter() - t0
    have, total = repo.index_coverage(embedder.model)
    return {
        "model": embedder.model,
        "model_id": model.id,
        "dim": model.dim,
        "embedded_now": len(todo),
        "embed_seconds": round(secs, 3),
        "chunks_per_s": round(len(todo) / secs, 2) if todo and secs else None,
        "real_tokens_embedded": n_tok if count_fn else None,
        "coverage": f"{have}/{total}",
        "metadata": meta,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["mock", "local", "openai_compatible"])
    ap.add_argument("--recreate", action="store_true")
    args = ap.parse_args()
    settings = get_settings()
    if args.provider:
        settings = Settings(**{**settings.model_dump(), "embedding_provider": args.provider})
    embedder = build_embedding(settings)
    with Session(build_engine(settings)) as s, s.begin():
        out = ensure_index(s, settings, embedder, recreate=args.recreate)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
