from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from ..agent.formatrules import FormatRules
from ..db import get_session
from ..models import Rfp
from ..services import audit, export_service

router = APIRouter(tags=["export"])


def _rfp(session: Session, rfp_id: int) -> Rfp:
    rfp = session.get(Rfp, rfp_id)
    if rfp is None:
        raise HTTPException(404, "RFP not found")
    return rfp


@router.get("/rfps/{rfp_id}/format-rules")
def get_rules(rfp_id: int, session: Session = Depends(get_session)) -> dict:
    return _rfp(session, rfp_id).format_rules or {}


@router.put("/rfps/{rfp_id}/format-rules")
def put_rules(rfp_id: int, rules: FormatRules, session: Session = Depends(get_session)) -> dict:
    """The SE confirms or corrects what was detected. Saving marks the rules confirmed."""
    rfp = _rfp(session, rfp_id)
    body = rules.model_copy(update={"confirmed": True, "keep_ids": True}).model_dump(exclude_none=True)
    rfp.format_rules = body
    rfp.deadline = body.get("deadline")
    audit.record(session, "rfp", "format_rules_confirmed", rfp_id=rfp_id, entity_id=rfp_id, payload=body)
    session.commit()
    return body


@router.get("/rfps/{rfp_id}/export/check")
def check(rfp_id: int, approved_only: bool = True, session: Session = Depends(get_session)) -> dict:
    _rfp(session, rfp_id)
    return export_service.check(session, rfp_id, approved_only)


@router.post("/rfps/{rfp_id}/export")
def export(rfp_id: int, approved_only: bool = True, allow_blocking: bool = False,
           session: Session = Depends(get_session)):
    """Download the response in the RFP's own format. Approved answers only unless asked otherwise."""
    _rfp(session, rfp_id)
    try:
        res = export_service.export_response(session, rfp_id, approved_only=approved_only,
                                             allow_blocking=allow_blocking)
    except export_service.ExportBlocked as e:
        raise HTTPException(409, {"message": str(e), **e.check}) from None
    return FileResponse(res.path, media_type=res.media_type, filename=res.filename,
                        headers={"X-Export-Draft": str(res.plan.draft_copy).lower()})


@router.get("/rfps/{rfp_id}/export/matrix")
def matrix(rfp_id: int, session: Session = Depends(get_session)):
    _rfp(session, rfp_id)
    res = export_service.export_compliance_matrix(session, rfp_id)
    return FileResponse(res.path, media_type=res.media_type, filename=res.filename)
