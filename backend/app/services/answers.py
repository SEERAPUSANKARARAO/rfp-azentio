from __future__ import annotations

from sqlmodel import Session, select

from .. import enums
from ..models import Answer, Citation, Requirement, Section
from ..schemas import AnswerOut, CitationOut


def answer_outs(session: Session, rfp_id: int, requirement_id: int | None = None) -> list[AnswerOut]:
    """One AnswerOut per requirement in the RFP's own order (or just the one asked for)."""
    q = select(Requirement).where(Requirement.rfp_id == rfp_id).order_by(Requirement.order)
    if requirement_id is not None:
        q = q.where(Requirement.id == requirement_id)
    reqs = session.exec(q).all()
    sections = {s.id: s for s in session.exec(select(Section).where(Section.rfp_id == rfp_id)).all()}
    ids = [r.id for r in reqs]
    answers = {a.requirement_id: a for a in session.exec(select(Answer).where(Answer.requirement_id.in_(ids))).all()}
    cites: dict[int, list[Citation]] = {}
    for c in session.exec(select(Citation).where(Citation.answer_id.in_([a.id for a in answers.values()]))).all():
        cites.setdefault(c.answer_id, []).append(c)

    out = []
    for r in reqs:
        sec = sections.get(r.section_id)
        base = dict(requirement_id=r.id, req_id=r.req_id, requirement=r.text, order=r.order, mandatory=r.mandatory,
                    section_code=sec.code if sec else "", section_title=sec.title if sec else "")
        a = answers.get(r.id)
        if a is None:
            out.append(AnswerOut(**base, review_status=enums.REVIEW_QUEUED))
            continue
        out.append(AnswerOut(
            **base, review_status=a.review_status, triage=a.triage, status=a.compliance_status,
            confidence=a.confidence, confidence_breakdown=a.confidence_breakdown or {}, flags=a.flags or [],
            flag_details=a.flag_details or {}, text=a.final_text if a.final_text is not None else a.draft_text,
            draft_text=a.draft_text, edited=a.edited, needs_review=a.needs_review, assigned_to=a.assigned_to,
            reasoning=a.reasoning, error=a.error,
            citations=[CitationOut(marker=c.marker, doc_id=c.doc_id, chunk_id=c.chunk_id, doc_title=c.doc_title,
                                   doc_date=c.doc_date.isoformat() if c.doc_date else None, location=c.location,
                                   quote=c.quote) for c in sorted(cites.get(a.id, []), key=lambda c: c.marker)],
        ))
    return out
