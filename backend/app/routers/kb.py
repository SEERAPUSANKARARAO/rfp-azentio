from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import func
from sqlmodel import Session, col, select

from ..config import get_settings
from ..db import get_session
from ..freshness import staleness_reasons
from ..models import KbChunk, KbDocument
from ..rag.embeddings import get_embedder
from ..rag.indexer import index_kb_dir
from ..rag.retrieve import retrieval_mode, retrieve
from ..services import kb_admin

router = APIRouter(prefix="/kb", tags=["knowledge base"])


@router.post("/index-samples")
def index_samples(session: Session = Depends(get_session)) -> dict:
    """Load the labelled sample knowledge base. Unchanged files are skipped."""
    res = index_kb_dir(session, get_settings().sample_dir / "kb", get_embedder())
    return asdict(res)


@router.get("/status")
def status(session: Session = Depends(get_session)) -> dict:
    docs = session.exec(select(func.count(KbDocument.id))).one()
    chunks = session.exec(select(func.count(KbChunk.id))).one()
    return {"documents": docs, "chunks": chunks, "retrieval_mode": retrieval_mode(session)}


@router.get("/documents")
def documents(session: Session = Depends(get_session)) -> list[dict]:
    counts = dict(session.exec(select(KbChunk.doc_id, func.count(KbChunk.id)).group_by(KbChunk.doc_id)).all())
    out = []
    for d in session.exec(select(KbDocument).order_by(col(KbDocument.doc_date).desc())).all():
        out.append({
            "id": d.id, "title": d.display_title, "kind": d.kind, "version": d.version,
            "doc_date": d.doc_date, "status": d.status, "canonical": d.canonical,
            "reuse_permitted": d.reuse_permitted, "answerable": d.answerable, "label": d.label,
            "owner": d.owner, "customer_redacted": bool(d.customer),
            "chunks": counts.get(d.id, 0),
            "stale_reasons": staleness_reasons(d.doc_date, d.version, d.status),
        })
    return out


@router.get("/search")
def search(
    q: str = Query(..., min_length=2), k: int = Query(6, ge=1, le=20),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Retrieval as the agent sees it: source, location, date, freshness on every passage."""
    return [asdict(p) for p in retrieve(session, q, k=k)]


def _guard(fn):
    try:
        return fn()
    except kb_admin.KbError as e:
        raise HTTPException(e.status_code, str(e)) from None


@router.post("/documents", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    kind: str = Form("product_doc"),
    version: str = Form(""),
    doc_date: str = Form(""),
    status: str = Form("approved"),
    customer: str = Form(""),
    owner: str = Form(""),
    reuse_permitted: bool = Form(True),
    answerable: bool = Form(True),
    reviewer: str = Form("se"),
    session: Session = Depends(get_session),
) -> dict:
    """Add a source to the knowledge base. Customer names are redacted at index time; undated sources are
    accepted but warned about, since their freshness cannot be checked."""
    data = await file.read(kb_admin.MAX_BYTES + 1)
    meta = dict(title=title, kind=kind, version=version, doc_date=doc_date, status=status, customer=customer,
                owner=owner, reuse_permitted=reuse_permitted, answerable=answerable)
    return _guard(lambda: kb_admin.add_document(session, file.filename or "document", data, meta, get_embedder(), reviewer))


@router.patch("/documents/{doc_id}")
def patch_document(doc_id: int, changes: dict, session: Session = Depends(get_session)) -> dict:
    """Deprecate, mark canonical, switch reuse or answerability, set date/version/owner. Takes effect at once."""
    actor = str(changes.pop("reviewer", "se"))
    d = _guard(lambda: kb_admin.update_document(session, doc_id, changes, actor))
    return {"id": d.id, "status": d.status, "canonical": d.canonical, "reuse_permitted": d.reuse_permitted,
            "answerable": d.answerable}


@router.delete("/documents/{doc_id}", status_code=204, response_class=Response)
def remove_document(doc_id: int, reviewer: str = "se", session: Session = Depends(get_session)):
    _guard(lambda: kb_admin.delete_document(session, doc_id, reviewer))
    return Response(status_code=204)
