from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import enums
from ..agent import jobs
from ..db import get_engine, get_session
from ..llm import get_gateway
from ..llm.errors import NoKeysConfigured
from ..freshness import staleness_reasons
from ..models import ClarificationQuery, Conflict, KbChunk, KbDocument, Rfp
from ..rag.conflicts import detect_conflicts
from ..rag.embeddings import get_embedder
from ..agent import priority
from ..schemas import AnswerOut, ClarificationOut, JobOut
from ..services import conflict_resolution
from ..services.answers import answer_outs

router = APIRouter(tags=["agent"])


def _job_out(session: Session, rfp_id: int) -> JobOut:
    j = jobs.latest_job(session, rfp_id)
    if j is None:
        return JobOut(rfp_id=rfp_id, status="none", message="No drafting run yet")
    return JobOut(id=j.id, rfp_id=rfp_id, status=j.status, total=j.total, done=j.done, message=j.message,
                  resume_at=j.resume_at)


@router.post("/rfps/{rfp_id}/draft", response_model=JobOut, status_code=202)
async def start_draft(rfp_id: int, session: Session = Depends(get_session)):
    """Draft every requirement that has no answer yet. Also how a paused run is resumed."""
    if session.get(Rfp, rfp_id) is None:
        raise HTTPException(404, "RFP not found")
    gw = get_gateway()
    if not any(p.has_keys() for p in gw.pools.values()):
        raise HTTPException(503, str(NoKeysConfigured("No API keys configured. Add them to rfp-ai/.env.")))
    if jobs.start_job(get_engine(), gw, rfp_id, assess=True) is None:
        raise HTTPException(409, "A drafting run is already in progress for this RFP.")
    return _job_out(session, rfp_id)


@router.get("/rfps/{rfp_id}/job", response_model=JobOut)
def get_job(rfp_id: int, session: Session = Depends(get_session)):
    if session.get(Rfp, rfp_id) is None:
        raise HTTPException(404, "RFP not found")
    return _job_out(session, rfp_id)


@router.get("/rfps/{rfp_id}/answers", response_model=list[AnswerOut])
def list_answers(rfp_id: int, session: Session = Depends(get_session)):
    if session.get(Rfp, rfp_id) is None:
        raise HTTPException(404, "RFP not found")
    return answer_outs(session, rfp_id)


@router.get("/rfps/{rfp_id}/clarifications", response_model=list[ClarificationOut])
def list_clarifications(rfp_id: int, session: Session = Depends(get_session)):
    if session.get(Rfp, rfp_id) is None:
        raise HTTPException(404, "RFP not found")
    rows = session.exec(select(ClarificationQuery).where(ClarificationQuery.rfp_id == rfp_id)).all()
    rows.sort(key=lambda c: (priority.RANK.get(c.priority, 1), c.id))
    return [ClarificationOut(id=c.id, text=c.text, reason=c.reason, requirement_ids=c.requirement_ids,
                             status=c.status, priority=c.priority, impact=c.impact, source=c.source,
                             assigned_to=c.assigned_to, answer=c.answer) for c in rows]


@router.get("/rfps/{rfp_id}/events")
async def events(rfp_id: int):
    """Server-sent progress for the drafting run: one event whenever the numbers change."""
    engine = get_engine()

    async def stream():
        last = None
        while True:
            with Session(engine) as s:
                payload = _job_out(s, rfp_id).model_dump()
            text = json.dumps(payload)
            if text != last:
                yield f"data: {text}\n\n"
                last = text
            if payload["status"] in ("none", enums.JOB_DONE, enums.JOB_PAUSED, enums.JOB_FAILED) and not jobs.is_running(rfp_id):
                return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ knowledge-base conflicts
@router.post("/kb/detect-conflicts")
async def run_conflict_detection(session: Session = Depends(get_session)) -> dict:
    """Judge cross-document contradictions with the LLM. Uses quota, so it is an explicit action."""
    gw = get_gateway()
    if not any(p.has_keys() for p in gw.pools.values()):
        raise HTTPException(503, "No API keys configured. Add them to rfp-ai/.env.")
    return await detect_conflicts(session, gw, get_embedder())


def _conflict_out(session: Session, c: Conflict, docs: dict[int, KbDocument]) -> dict:
    def side(doc_id, chunk_id):
        d = docs.get(doc_id) if doc_id else None
        ch = session.get(KbChunk, chunk_id) if chunk_id else None
        return {"doc_id": doc_id, "title": d.display_title if d else "", "date": d.doc_date if d else None,
                "version": d.version if d else None, "location": ch.location if ch else "",
                "text": ch.text if ch else "", "stale_reasons": staleness_reasons(d.doc_date, d.version, d.status) if d else []}
    return {"id": c.id, "kind": c.kind, "topic": c.topic, "detail": c.detail, "status": c.status,
            "canonical_doc_id": c.canonical_doc_id, "older": side(c.doc_a_id, c.chunk_a_id),
            "newer": side(c.doc_b_id, c.chunk_b_id), "affected": conflict_resolution.affected_answers(session, c)}


@router.get("/kb/conflicts")
def list_conflicts(session: Session = Depends(get_session)) -> list[dict]:
    docs = {d.id: d for d in session.exec(select(KbDocument)).all()}
    return [_conflict_out(session, c, docs) for c in session.exec(select(Conflict).order_by(Conflict.id)).all()]


class ResolveBody(BaseModel):
    canonical_doc_id: int
    reviewer: str = "sme"
    recalculate: bool = True


@router.post("/kb/conflicts/{conflict_id}/resolve")
async def resolve_conflict(conflict_id: int, body: ResolveBody, session: Session = Depends(get_session)) -> dict:
    """The SME names the canonical source. The other passage is superseded, and the answers that relied on either
    side are redrafted (their approvals withdrawn) unless recalculate is false or no model keys are configured."""
    try:
        c, affected = conflict_resolution.resolve(session, conflict_id, body.canonical_doc_id, body.reviewer)
    except conflict_resolution.ConflictError as e:
        raise HTTPException(e.status_code, str(e)) from None

    recalculation: list[dict] = []
    if body.recalculate and affected:
        gw = get_gateway()
        has_keys = any(p.has_keys() for p in gw.pools.values())
        by_rfp: dict[int, list[int]] = {}
        for x in affected:
            by_rfp.setdefault(x["rfp_id"], []).append(x["requirement_id"])
        for rfp_id, rids in by_rfp.items():
            if not has_keys:
                recalculation.append({"rfp_id": rfp_id, "job_id": None, "skipped": "No API keys configured"})
                continue
            job_id, merged = jobs.start_recalc(get_engine(), gw, rfp_id, rids, f"conflict on '{c.topic}' was resolved", assess=True)
            recalculation.append({"rfp_id": rfp_id, "job_id": job_id, "requirements": len(rids), "merged": merged})
    docs = {d.id: d for d in session.exec(select(KbDocument)).all()}
    return {"conflict": _conflict_out(session, c, docs), "affected": affected, "recalculation": recalculation}


class ReopenBody(BaseModel):
    reviewer: str = "sme"


@router.post("/kb/conflicts/{conflict_id}/reopen")
def reopen_conflict(conflict_id: int, body: ReopenBody = ReopenBody(), session: Session = Depends(get_session)) -> dict:
    try:
        c = conflict_resolution.reopen(session, conflict_id, body.reviewer)
    except conflict_resolution.ConflictError as e:
        raise HTTPException(e.status_code, str(e)) from None
    docs = {d.id: d for d in session.exec(select(KbDocument)).all()}
    return _conflict_out(session, c, docs)
