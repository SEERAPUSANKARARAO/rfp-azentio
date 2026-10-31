"""Level 2: capability assessment, the compliance matrix, gap analysis, the review gate and the submission checklist."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..agent.assess import assess_rfp
from ..agent.interpret import interpret_rfp
from ..agent.scheme import scheme_of
from ..db import get_engine, get_session
from ..llm import get_gateway
from ..models import Rfp
from ..services import assessments, readiness, workflow
from ..services.matrix import gaps_summary, matrix_rows

router = APIRouter(tags=["assessment"])
_running: dict[int, asyncio.Task] = {}
_last: dict[int, dict] = {}


def _rfp(session: Session, rfp_id: int) -> Rfp:
    rfp = session.get(Rfp, rfp_id)
    if rfp is None:
        raise HTTPException(404, "RFP not found")
    return rfp


def _is_running(rfp_id: int) -> bool:
    t = _running.get(rfp_id)
    return t is not None and not t.done()


class AssessBody(BaseModel):
    requirement_ids: list[int] | None = None
    force: bool = False  # reassess even where the assessment is current


@router.post("/rfps/{rfp_id}/assess", status_code=202)
async def start_assess(rfp_id: int, body: AssessBody | None = None, session: Session = Depends(get_session)) -> dict:
    """Classify each drafted answer's capability and find the gaps. Runs in the background; poll /assessments."""
    _rfp(session, rfp_id)
    gw = get_gateway()
    if not any(p.has_keys() for p in gw.pools.values()):
        raise HTTPException(503, "No API keys configured. Add them to rfp-ai/.env.")
    if _is_running(rfp_id):
        raise HTTPException(409, "An assessment is already running for this RFP.")
    body = body or AssessBody()

    async def go() -> None:
        _last[rfp_id] = await assess_rfp(get_engine(), gw, rfp_id, requirement_ids=body.requirement_ids,
                                         only_stale=not body.force)

    _running[rfp_id] = asyncio.create_task(go())
    return {"started": True}


@router.get("/rfps/{rfp_id}/assessments")
def list_assessments(rfp_id: int, session: Session = Depends(get_session)) -> dict:
    """The compliance matrix: every requirement with its status, suggested code, needs, gaps and review state."""
    rfp = _rfp(session, rfp_id)
    scheme = scheme_of(rfp.format_rules or {})
    return {"scheme": scheme.model_dump() if scheme else None, "assessing": _is_running(rfp_id),
            "last_run": _last.get(rfp_id), "rows": matrix_rows(session, rfp_id)}


class AssessmentPatch(BaseModel):
    capability: str | None = None
    code: str | None = None
    estimate: str | None = None
    attachments: list[str] | None = None
    confirm: bool = False
    reviewer: str = "se"


@router.patch("/rfps/{rfp_id}/assessments/{requirement_id}")
def patch_assessment(rfp_id: int, requirement_id: int, body: AssessmentPatch,
                     session: Session = Depends(get_session)) -> dict:
    """A person validates, corrects or completes a suggested code. Everything is audited."""
    _rfp(session, rfp_id)
    try:
        assessments.update_assessment(session, rfp_id, requirement_id, capability=body.capability, code=body.code,
                                      estimate=body.estimate, attachments=body.attachments, confirm=body.confirm,
                                      actor=body.reviewer)
    except assessments.AssessmentError as e:
        raise HTTPException(e.status_code, str(e)) from None
    return next(r for r in matrix_rows(session, rfp_id) if r["requirement_id"] == requirement_id)


@router.get("/rfps/{rfp_id}/gaps")
def gaps(rfp_id: int, session: Session = Depends(get_session)) -> dict:
    _rfp(session, rfp_id)
    return gaps_summary(matrix_rows(session, rfp_id))


@router.get("/rfps/{rfp_id}/readiness")
def get_readiness(rfp_id: int, session: Session = Depends(get_session)) -> dict:
    _rfp(session, rfp_id)
    return readiness.readiness(session, rfp_id)


class ProceedBody(BaseModel):
    acknowledge: bool = False
    reason: str = ""
    reviewer: str = "se"


@router.post("/rfps/{rfp_id}/proceed-to-review")
def proceed(rfp_id: int, body: ProceedBody, session: Session = Depends(get_session)) -> dict:
    _rfp(session, rfp_id)
    try:
        return readiness.proceed_to_review(session, rfp_id, acknowledge=body.acknowledge, reason=body.reason,
                                           actor=body.reviewer)
    except readiness.ReadinessError as e:
        raise HTTPException(e.status_code, {"message": str(e), **e.payload}) from None


@router.get("/rfps/{rfp_id}/checklist")
def get_checklist(rfp_id: int, session: Session = Depends(get_session)) -> dict:
    _rfp(session, rfp_id)
    return readiness.checklist(session, rfp_id)


class TickBody(BaseModel):
    done: bool = True
    reviewer: str = "se"


@router.put("/rfps/{rfp_id}/checklist/{key}")
def tick(rfp_id: int, key: str, body: TickBody, session: Session = Depends(get_session)) -> dict:
    _rfp(session, rfp_id)
    try:
        return readiness.set_manual(session, rfp_id, key, body.done, body.reviewer)
    except readiness.ReadinessError as e:
        raise HTTPException(e.status_code, str(e)) from None


@router.post("/rfps/{rfp_id}/interpret")
async def interpret(rfp_id: int, session: Session = Depends(get_session)) -> dict:
    """Read the requirements for dependencies, the components they touch, and ambiguous or incomplete wording.
    The rule-based part needs no model; with keys it also finds implied dependencies and contradictions."""
    _rfp(session, rfp_id)
    gw = get_gateway()
    has_keys = any(p.has_keys() for p in gw.pools.values())
    return await interpret_rfp(get_engine(), gw if has_keys else None, rfp_id)


# ------------------------------------------------------------------ collaborative workflow
@router.get("/rfps/{rfp_id}/workflow")
def get_workflow(rfp_id: int, session: Session = Depends(get_session)) -> dict:
    """The review board: how many requirements are in each state, and who has what."""
    _rfp(session, rfp_id)
    return workflow.board(session, rfp_id)


class WhoBody(BaseModel):
    reviewer: str = "se"


@router.post("/rfps/{rfp_id}/assign-suggested")
def assign_suggested(rfp_id: int, body: WhoBody | None = None, session: Session = Depends(get_session)) -> dict:
    """Hand each requirement that has a gap and nobody looking at it to the role the gap analysis suggests."""
    _rfp(session, rfp_id)
    return workflow.assign_suggested(session, rfp_id, (body or WhoBody()).reviewer)


class RegenerateBody(BaseModel):
    requirement_ids: list[int] | None = None  # default: what customer replies have changed, and what depends on it
    reason: str = ""
    reviewer: str = "se"


@router.post("/rfps/{rfp_id}/regenerate-affected", status_code=202)
def regenerate_affected(rfp_id: int, body: RegenerateBody | None = None, session: Session = Depends(get_session)) -> dict:
    """Redraft what has changed. Approved answers are never rewritten: they are marked for a re-check instead."""
    _rfp(session, rfp_id)
    body = body or RegenerateBody()
    gw = get_gateway()
    if not any(p.has_keys() for p in gw.pools.values()):
        raise HTTPException(503, "No API keys configured. Add them to rfp-ai/.env.")
    return workflow.regenerate_affected(session, get_engine(), gw, rfp_id, requirement_ids=body.requirement_ids,
                                        reason=body.reason, actor=body.reviewer)
