"""Knowledge base administration: add a document, change its metadata, remove it.

What an SME decides here (a document is deprecated, canonical, or must not be reused) takes effect on the
very next retrieval, and is written to the audit log with who decided it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from sqlalchemy import text as sql
from sqlmodel import Session, select

from .. import enums
from ..config import get_settings
from ..models import Citation, Conflict, KbChunk, KbDocument
from ..rag.embeddings import Embedder
from ..rag.indexer import index_document
from . import audit

ALLOWED_SUFFIX = {".md", ".txt", ".docx", ".pdf"}
UPLOADABLE_KINDS = tuple(k for k in enums.KB_KINDS if k != enums.KB_SE_PROVIDED)
MAX_BYTES = 15 * 1024 * 1024


class KbError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name) or "document"


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise KbError("Document date must look like 2026-03-15.") from None


def add_document(session: Session, filename: str, data: bytes, meta: dict, embedder: Embedder | None, actor: str) -> dict:
    name = _safe(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise KbError("Unsupported file type. Upload a Word (.docx), PDF, Markdown or text file.")
    if not data:
        raise KbError("The uploaded file is empty.")
    if len(data) > MAX_BYTES:
        raise KbError("File is larger than 15 MB.", 413)
    kind = meta.get("kind") or enums.KB_PRODUCT_DOC
    if kind not in UPLOADABLE_KINDS:
        raise KbError(f"Kind must be one of: {', '.join(UPLOADABLE_KINDS)}.")
    status = meta.get("status") or enums.DOC_APPROVED
    if status not in (enums.DOC_APPROVED, enums.DOC_DEPRECATED):
        raise KbError("Status must be approved or deprecated.")

    clean = {
        "title": (meta.get("title") or Path(name).stem.replace("_", " ")).strip(),
        "kind": kind, "version": (meta.get("version") or None), "doc_date": _iso(meta.get("doc_date")),
        "status": status, "canonical": bool(meta.get("canonical", False)),
        "reuse_permitted": bool(meta.get("reuse_permitted", True)), "answerable": bool(meta.get("answerable", True)),
        "customer": (meta.get("customer") or None), "owner": (meta.get("owner") or actor), "label": "UPLOADED",
    }
    dest = get_settings().kb_upload_dir / name
    dest.write_bytes(data)
    action, chunks = index_document(session, dest, clean, embedder)

    warnings = []
    if clean["doc_date"] is None:
        warnings.append("No document date: staleness cannot be checked for this source.")
    if clean["kind"] in (enums.KB_PAST_RFP, enums.KB_SOW) and not clean["customer"]:
        warnings.append("This looks like customer work but no customer name was given, so nothing is redacted.")
    if chunks == 0:
        warnings.append("No text could be extracted (is it a scanned PDF?). Nothing was indexed.")
    doc = session.exec(select(KbDocument).where(KbDocument.filename == name)).one()
    audit.record(session, "kb_document", f"document_{action}", entity_id=doc.id, actor=actor,
                 payload={"title": doc.title, "kind": kind, "chunks": chunks, "warnings": warnings})
    session.commit()
    return {"action": action, "id": doc.id, "title": doc.display_title, "chunks": chunks, "warnings": warnings,
            "needs_conflict_check": action != "unchanged" and chunks > 0}


EDITABLE = {"status", "canonical", "reuse_permitted", "answerable", "owner", "doc_date", "version"}


def update_document(session: Session, doc_id: int, changes: dict, actor: str) -> KbDocument:
    doc = session.get(KbDocument, doc_id)
    if doc is None:
        raise KbError("Document not found", 404)
    bad = set(changes) - EDITABLE
    if bad:
        raise KbError(f"Cannot change: {', '.join(sorted(bad))}.")
    if "status" in changes and changes["status"] not in (enums.DOC_APPROVED, enums.DOC_DEPRECATED):
        raise KbError("Status must be approved or deprecated.")
    if "doc_date" in changes:
        iso = _iso(changes["doc_date"])
        changes = {**changes, "doc_date": date.fromisoformat(iso) if iso else None}
    before = {k: getattr(doc, k) for k in changes}
    for k, v in changes.items():
        setattr(doc, k, v)
    audit.record(session, "kb_document", "document_updated", entity_id=doc.id, actor=actor,
                 payload={"title": doc.display_title, "before": {k: str(v) for k, v in before.items()},
                          "after": {k: str(v) for k, v in changes.items()}})
    session.commit()
    return doc


def delete_document(session: Session, doc_id: int, actor: str) -> None:
    doc = session.get(KbDocument, doc_id)
    if doc is None:
        raise KbError("Document not found", 404)
    cited = session.exec(select(Citation).where(Citation.doc_id == doc_id)).all()
    if cited:
        raise KbError(f"{len(cited)} answer citation(s) rely on this document. Mark it deprecated instead of "
                      "deleting it, so those answers keep their evidence.", 409)
    chunk_ids = [c for c in session.exec(select(KbChunk.id).where(KbChunk.doc_id == doc_id)).all()]
    for cid in chunk_ids:
        session.exec(sql("DELETE FROM kb_chunk_fts WHERE rowid = :i").bindparams(i=cid))  # type: ignore[call-overload]
    for c in session.exec(select(Conflict).where((Conflict.doc_a_id == doc_id) | (Conflict.doc_b_id == doc_id))).all():
        session.delete(c)
    for ch in session.exec(select(KbChunk).where(KbChunk.doc_id == doc_id)).all():
        session.delete(ch)
    title = doc.display_title
    session.delete(doc)
    audit.record(session, "kb_document", "document_deleted", entity_id=doc_id, actor=actor, payload={"title": title})
    session.commit()
