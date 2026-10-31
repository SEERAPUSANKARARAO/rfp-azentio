from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..agent.pipeline import draft_requirement
from ..db import get_engine, get_session
from ..llm import get_gateway
from ..llm.errors import AllProvidersExhausted, LLMError
from ..models import AuditEvent, ClarificationQuery, Requirement, Rfp
from ..rag.conflicts import conflicts_by_chunk
from ..rag.embeddings import get_embedder
from ..schemas import AnswerOut
from ..services import audit, facts, review
from ..services.answers import answer_outs

router = APIRouter(tags=["review"])


class Who(BaseModel):
    reviewer: str = "se"


class EditBody(Who):
    text: str


class StatusBody(Who):
    status: str


class FlagBody(Who):
    assignee: str | None = None
    note: str = ""


class RegenBody(Who):
    instruction: str | None = None


class FactBody(Who):
    text: str
    regenerate: bool = True


def _one(session: Session, rfp_id: int, requirement_id: int) -> AnswerOut:
    return answer_outs(session, rfp_id, requirement_id)[0]


def _guard(fn):
    try:
        return fn()
    except review.ReviewError as e:
        raise HTTPException(e.status_code, str(e)) from None


@router.post("/rfps/{rfp_id}/answers/{rid}/accept", response_model=AnswerOut)
def accept(rfp_id: int, rid: int, body: Who = Who(), session: Session = Depends(get_session)):
    _guard(lambda: review.accept(session, rfp_id, rid, body.reviewer))
    return _one(session, rfp_id, rid)


@router.post("/rfps/{rfp_id}/answers/{rid}/edit", response_model=AnswerOut)
def edit(rfp_id: int, rid: int, body: EditBody, session: Session = Depends(get_session)):
    _guard(lambda: review.edit(session, rfp_id, rid, body.text, body.reviewer))
    return _one(session, rfp_id, rid)


@router.post("/rfps/{rfp_id}/answers/{rid}/revert", response_model=AnswerOut)
def revert(rfp_id: int, rid: int, body: Who = Who(), session: Session = Depends(get_session)):
    _guard(lambda: review.revert_edit(session, rfp_id, rid, body.reviewer))
    return _one(session, rfp_id, rid)


@router.post("/rfps/{rfp_id}/answers/{rid}/status", response_model=AnswerOut)
def set_status(rfp_id: int, rid: int, body: StatusBody, session: Session = Depends(get_session)):
    _guard(lambda: review.set_status(session, rfp_id, rid, body.status, body.reviewer))
    return _one(session, rfp_id, rid)


@router.post("/rfps/{rfp_id}/answers/{rid}/flag", response_model=AnswerOut)
def flag(rfp_id: int, rid: int, body: FlagBody, session: Session = Depends(get_session)):
    _guard(lambda: review.flag(session, rfp_id, rid, body.assignee, body.note, body.reviewer))
    return _one(session, rfp_id, rid)


async def _redraft(session: Session, rfp_id: int, rid: int, instruction: str | None, use_cache: bool, who: str):
    gw = get_gateway()
    if not any(p.has_keys() for p in gw.pools.values()):
        raise HTTPException(503, "No API keys configured. Add them to rfp-ai/.env.")
    _guard(lambda: review.load(session, rfp_id, rid))
    try:
        await draft_requirement(get_engine(), gw, rid, conflicts=conflicts_by_chunk(session),
                                instruction=instruction, use_cache=use_cache)
    except AllProvidersExhausted as e:
        raise HTTPException(503, f"{e} Try again shortly.") from None
    except LLMError as e:
        raise HTTPException(502, f"The model could not produce a valid draft: {type(e).__name__}") from None
    session.expire_all()
    req = session.get(Requirement, rid)
    audit.record(session, "answer", "regenerated", rfp_id=rfp_id, actor=who,
                 payload={"req_id": req.req_id, "instruction": instruction})
    session.commit()


@router.post("/rfps/{rfp_id}/answers/{rid}/regenerate", response_model=AnswerOut)
async def regenerate(rfp_id: int, rid: int, body: RegenBody = RegenBody(), session: Session = Depends(get_session)):
    """Draft again, optionally with a wording instruction ("shorter", "more formal"). Clears any approval."""
    await _redraft(session, rfp_id, rid, (body.instruction or "").strip() or None, use_cache=False, who=body.reviewer)
    return _one(session, rfp_id, rid)


@router.post("/rfps/{rfp_id}/answers/{rid}/facts", response_model=AnswerOut)
async def add_fact(rfp_id: int, rid: int, body: FactBody, session: Session = Depends(get_session)):
    """The SE supplies a fact the knowledge base lacks. It is stored as a dated, authored SE-provided source and
    the answer is redrafted so it can cite it as such."""
    req, _ = _guard(lambda: review.load(session, rfp_id, rid))
    try:
        facts.add_se_fact(session, req, body.text, body.reviewer, get_embedder())
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    if body.regenerate:
        await _redraft(session, rfp_id, rid, None, use_cache=False, who=body.reviewer)
    return _one(session, rfp_id, rid)


@router.get("/rfps/{rfp_id}/answers/{rid}", response_model=AnswerOut)
def get_answer(rfp_id: int, rid: int, session: Session = Depends(get_session)):
    outs = answer_outs(session, rfp_id, rid)
    if not outs:
        raise HTTPException(404, "Requirement not found")
    return outs[0]


@router.get("/rfps/{rfp_id}/summary")
def summary(rfp_id: int, session: Session = Depends(get_session)) -> dict:
    if session.get(Rfp, rfp_id) is None:
        raise HTTPException(404, "RFP not found")
    return review.summary(session, rfp_id)


@router.get("/rfps/{rfp_id}/assignees")
def assignees(rfp_id: int) -> list[str]:
    return list(review.ASSIGNEES)


def _audit_rows(session: Session, rfp_id: int | None, limit: int) -> list[dict]:
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if rfp_id is not None:
        q = q.where(AuditEvent.rfp_id == rfp_id)
    return [{"id": e.id, "ts": e.ts.isoformat(), "rfp_id": e.rfp_id, "entity": e.entity, "entity_id": e.entity_id,
             "action": e.action, "actor": e.actor, "payload": e.payload} for e in session.exec(q).all()]


@router.get("/rfps/{rfp_id}/audit")
def rfp_audit(rfp_id: int, limit: int = 200, session: Session = Depends(get_session)) -> list[dict]:
    return _audit_rows(session, rfp_id, min(limit, 1000))


@router.get("/audit")
def all_audit(limit: int = 200, session: Session = Depends(get_session)) -> list[dict]:
    return _audit_rows(session, None, min(limit, 1000))


# ------------------------------------------------------------------ clarification questions
class ClarificationBody(Who):
    text: str | None = None
    reason: str | None = None
    status: str | None = None  # open | approved | dismissed
    requirement_ids: list[str] | None = None


CLAR_STATUSES = ("open", "approved", "dismissed")


def _clar(session: Session, rfp_id: int, cid: int) -> ClarificationQuery:
    c = session.get(ClarificationQuery, cid)
    if c is None or c.rfp_id != rfp_id:
        raise HTTPException(404, "Clarification question not found")
    return c


def _clar_out(c: ClarificationQuery) -> dict:
    return {"id": c.id, "text": c.text, "reason": c.reason, "requirement_ids": c.requirement_ids, "status": c.status}


@router.post("/rfps/{rfp_id}/clarifications", status_code=201)
def add_clarification(rfp_id: int, body: ClarificationBody, session: Session = Depends(get_session)) -> dict:
    """The SE adds a question of their own to the list."""
    if session.get(Rfp, rfp_id) is None:
        raise HTTPException(404, "RFP not found")
    if not (body.text or "").strip():
        raise HTTPException(422, "A question needs some text.")
    c = ClarificationQuery(rfp_id=rfp_id, text=body.text.strip(), reason=(body.reason or "").strip(),
                           requirement_ids=body.requirement_ids or [], status="approved")
    session.add(c)
    session.flush()
    audit.record(session, "clarification", "clarification_added", rfp_id=rfp_id, entity_id=c.id, actor=body.reviewer)
    session.commit()
    return _clar_out(c)


@router.patch("/rfps/{rfp_id}/clarifications/{cid}")
def edit_clarification(rfp_id: int, cid: int, body: ClarificationBody, session: Session = Depends(get_session)) -> dict:
    """Reword, approve (it will go to the customer) or dismiss (it will not) a clarification question."""
    c = _clar(session, rfp_id, cid)
    if body.status is not None and body.status not in CLAR_STATUSES:
        raise HTTPException(422, f"Status must be one of: {', '.join(CLAR_STATUSES)}")
    if body.text is not None and not body.text.strip():
        raise HTTPException(422, "A question needs some text.")
    before = _clar_out(c)
    for field in ("text", "reason", "status", "requirement_ids"):
        v = getattr(body, field)
        if v is not None:
            setattr(c, field, v.strip() if isinstance(v, str) else v)
    audit.record(session, "clarification", "clarification_edited", rfp_id=rfp_id, entity_id=c.id, actor=body.reviewer,
                 payload={"before": before, "after": _clar_out(c)})
    session.commit()
    return _clar_out(c)


@router.delete("/rfps/{rfp_id}/clarifications/{cid}", status_code=204, response_class=Response)
def delete_clarification(rfp_id: int, cid: int, reviewer: str = "se", session: Session = Depends(get_session)):
    c = _clar(session, rfp_id, cid)
    audit.record(session, "clarification", "clarification_deleted", rfp_id=rfp_id, entity_id=c.id, actor=reviewer,
                 payload={"text": c.text})
    session.delete(c)
    session.commit()
    return Response(status_code=204)
