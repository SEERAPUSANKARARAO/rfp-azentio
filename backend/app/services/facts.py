"""A fact the Sales Engineer supplies themselves, to fill a gap the knowledge base could not.

It is stored as its own dated, authored source and cited as "SE-provided", so a reviewer can always tell a
sourced statement from one that rests on an engineer's say-so, and an SME can later promote or reject it.
"""

from __future__ import annotations

import hashlib
from datetime import date

from sqlalchemy import text as sql
from sqlmodel import Session

from .. import enums
from ..freshness import today
from ..models import KbChunk, KbDocument, Requirement
from ..rag.embeddings import Embedder
from . import audit


def add_se_fact(
    session: Session, requirement: Requirement, fact: str, reviewer: str, embedder: Embedder | None = None
) -> KbDocument:
    fact = " ".join(fact.split())
    if len(fact) < 12:
        raise ValueError("A fact needs at least a short sentence.")
    who = reviewer or "se"
    when: date = today()
    title = f"SE-provided fact for {requirement.req_id} ({who}, {when.isoformat()})"
    digest = hashlib.sha256(f"{requirement.rfp_id}:{requirement.req_id}:{fact}".encode()).hexdigest()
    doc = KbDocument(
        title=title, display_title=title, kind=enums.KB_SE_PROVIDED,
        filename=f"se_fact_{digest[:10]}.txt", doc_date=when, status=enums.DOC_APPROVED,
        owner=who, label="SE-PROVIDED", content_hash=digest,
    )
    session.add(doc)
    session.flush()
    body = f"SE-provided fact\n{fact}"
    vec = embedder.embed_passages([f"{title}. {body}"])[0].astype("float32").tobytes() if embedder else None
    chunk = KbChunk(doc_id=doc.id, ordinal=0, text=body, location=f"added by {who} on {when.isoformat()}", embedding=vec)
    session.add(chunk)
    session.flush()
    session.exec(  # type: ignore[call-overload]
        sql("INSERT INTO kb_chunk_fts(rowid, text) VALUES (:i, :t)").bindparams(i=chunk.id, t=f"{title}\n{body}"))
    audit.record(session, "kb_document", "se_fact_added", rfp_id=requirement.rfp_id, entity_id=doc.id, actor=who,
                 payload={"req_id": requirement.req_id, "fact": fact[:400]})
    session.commit()
    return doc
