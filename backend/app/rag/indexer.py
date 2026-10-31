from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import text as sql
from sqlmodel import Session, select

from ..ingest.kb_loader import load_chunks
from ..ingest.redact import redact
from ..models import KbChunk, KbDocument
from .embeddings import Embedder


@dataclass
class IndexResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    chunks: int = 0
    embedded: bool = False


def _fts_text(display_title: str, chunk_text: str) -> str:
    # Title words make a chunk findable by product/topic, e.g. "Murabaha" in a guide's title.
    return f"{display_title}\n{chunk_text}"


def _drop_chunks(session: Session, doc_id: int) -> None:
    ids = [c for c in session.exec(select(KbChunk.id).where(KbChunk.doc_id == doc_id)).all()]
    for i in ids:
        session.exec(sql("DELETE FROM kb_chunk_fts WHERE rowid = :i").bindparams(i=i))  # type: ignore[call-overload]
    for c in session.exec(select(KbChunk).where(KbChunk.doc_id == doc_id)).all():
        session.delete(c)


def index_document(
    session: Session, path: Path, meta: dict, embedder: Embedder | None
) -> tuple[str, int]:
    """Index one file. Returns ("added" | "updated" | "unchanged", chunk_count). Caller commits."""
    digest = hashlib.sha256(path.read_bytes() + json.dumps(meta, sort_keys=True).encode()).hexdigest()
    existing = session.exec(select(KbDocument).where(KbDocument.filename == path.name)).first()
    if existing and existing.content_hash == digest:
        n = len(session.exec(select(KbChunk.id).where(KbChunk.doc_id == existing.id)).all())
        return "unchanged", n

    customer = meta.get("customer")
    display_title = redact(meta["title"], customer)
    fields = dict(
        title=meta["title"], display_title=display_title, kind=meta["kind"], filename=path.name,
        version=meta.get("version"),
        doc_date=date.fromisoformat(meta["doc_date"]) if meta.get("doc_date") else None,
        status=meta.get("status", "approved"), canonical=meta.get("canonical", False),
        reuse_permitted=meta.get("reuse_permitted", True), answerable=meta.get("answerable", True),
        customer=customer, owner=meta.get("owner"), label=meta.get("label", "SAMPLE"),
        content_hash=digest,
    )
    if existing:
        _drop_chunks(session, existing.id)
        for k, v in fields.items():
            setattr(existing, k, v)
        doc, action = existing, "updated"
    else:
        doc = KbDocument(**fields)
        session.add(doc)
        action = "added"
    session.flush()

    drafts = load_chunks(path, customer=customer, redact_fn=redact, fallback_heading=display_title)
    vectors = None
    if embedder and drafts:
        vectors = embedder.embed_passages([f"{display_title}. {d.text}" for d in drafts])
    for i, d in enumerate(drafts):
        chunk = KbChunk(
            doc_id=doc.id, ordinal=d.ordinal, text=d.text, location=d.location, page=d.page,
            embedding=vectors[i].astype("float32").tobytes() if vectors is not None else None,
        )
        session.add(chunk)
        session.flush()
        session.exec(  # type: ignore[call-overload]
            sql("INSERT INTO kb_chunk_fts(rowid, text) VALUES (:i, :t)")
            .bindparams(i=chunk.id, t=_fts_text(display_title, d.text))
        )
    return action, len(drafts)


def index_kb_dir(session: Session, kb_dir: Path, embedder: Embedder | None) -> IndexResult:
    """Index every document listed in kb_dir/manifest.json. Unchanged files are skipped."""
    manifest = json.loads((kb_dir / "manifest.json").read_text(encoding="utf-8"))
    result = IndexResult(embedded=embedder is not None)
    for meta in manifest:
        action, n = index_document(session, kb_dir / meta["file"], meta, embedder)
        getattr(result, action).append(meta["file"])
        result.chunks += n
    session.commit()
    return result
