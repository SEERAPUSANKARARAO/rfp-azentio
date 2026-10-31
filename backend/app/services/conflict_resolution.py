"""An SME settles a contradiction between two sources by naming the canonical one.

The other side's passage is superseded (never retrieved again), the conflict stops flagging, and every answer
that leaned on either side is queued to be redrafted. Redrafting also withdraws any approval on those answers,
because the evidence behind them has changed and the SE should look again."""

from __future__ import annotations

from sqlmodel import Session, select

from .. import enums
from ..models import Answer, Citation, Conflict, Requirement
from . import audit


class ConflictError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def affected_answers(session: Session, conflict: Conflict) -> list[dict]:
    """Answers that cite either side of the conflict, or were flagged because of it."""
    chunk_ids = [c for c in (conflict.chunk_a_id, conflict.chunk_b_id) if c is not None]
    answer_ids: set[int] = set()
    if chunk_ids:
        answer_ids |= {c.answer_id for c in session.exec(select(Citation).where(Citation.chunk_id.in_(chunk_ids))).all()}
    for a in session.exec(select(Answer)).all():
        if enums.FLAG_CONFLICTING in (a.flags or []):
            if any(x.get("id") == conflict.id for x in ((a.flag_details or {}).get("conflicts") or {}).get("indexed", [])):
                answer_ids.add(a.id)
    out = []
    for aid in sorted(answer_ids):
        a = session.get(Answer, aid)
        req = session.get(Requirement, a.requirement_id)
        out.append({"rfp_id": req.rfp_id, "requirement_id": req.id, "req_id": req.req_id,
                    "review_status": a.review_status})
    return out


def resolve(session: Session, conflict_id: int, canonical_doc_id: int, reviewer: str) -> tuple[Conflict, list[dict]]:
    c = session.get(Conflict, conflict_id)
    if c is None:
        raise ConflictError("Conflict not found", 404)
    if canonical_doc_id not in (c.doc_a_id, c.doc_b_id):
        raise ConflictError("The canonical document must be one of the two documents in this conflict.")
    if c.status == enums.CONFLICT_RESOLVED:
        raise ConflictError("This conflict is already resolved. Reopen it first to change the decision.", 409)
    affected = affected_answers(session, c)  # computed before the flag disappears
    c.status, c.canonical_doc_id = enums.CONFLICT_RESOLVED, canonical_doc_id
    from ..models import KbDocument
    doc = session.get(KbDocument, canonical_doc_id)
    doc.canonical = True
    audit.record(session, "conflict", "conflict_resolved", entity_id=c.id, actor=reviewer or "sme",
                 payload={"topic": c.topic, "canonical": doc.display_title, "affected": [a["req_id"] for a in affected]})
    session.commit()
    return c, affected


def reopen(session: Session, conflict_id: int, reviewer: str) -> Conflict:
    c = session.get(Conflict, conflict_id)
    if c is None:
        raise ConflictError("Conflict not found", 404)
    if c.status != enums.CONFLICT_RESOLVED:
        return c
    c.status, c.canonical_doc_id = enums.CONFLICT_OPEN, None
    audit.record(session, "conflict", "conflict_reopened", entity_id=c.id, actor=reviewer or "sme",
                 payload={"topic": c.topic})
    session.commit()
    return c
