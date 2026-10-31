"""A person validates, corrects or completes the agent's capability assessment. Always audited."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from .. import enums
from ..agent import gaps as gap_rules
from ..agent.scheme import code_for_class, needs_for_code, scheme_of
from ..models import Answer, Assessment, Requirement, Rfp
from . import audit


class AssessmentError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def update_assessment(
    session: Session, rfp_id: int, requirement_id: int, *, capability: str | None = None, code: str | None = None,
    estimate: str | None = None, attachments: list[str] | None = None, confirm: bool = False, actor: str = "se",
) -> Assessment:
    req = session.get(Requirement, requirement_id)
    if req is None or req.rfp_id != rfp_id:
        raise AssessmentError("Requirement not found", 404)
    a = session.exec(select(Assessment).where(Assessment.requirement_id == requirement_id)).first()
    if a is None:
        raise AssessmentError("This requirement has not been assessed yet", 404)
    rfp = session.get(Rfp, rfp_id)
    scheme = scheme_of(rfp.format_rules or {})
    before = {"capability": a.capability, "code": a.code, "estimate": a.estimate, "confirmed": a.confirmed}

    if capability is not None:
        if capability not in enums.CAPABILITIES:
            raise AssessmentError(f"Capability must be one of: {', '.join(enums.CAPABILITIES)}")
        a.capability = capability
        a.parts = [{"what": "Set by the reviewer", "capability": capability, "passages": [], "quote": "",
                    "reason": f"Chosen by {actor}"}]
        if code is None and scheme is not None and scheme.kind == enums.SCHEME_CAPABILITY:
            a.code = code_for_class(scheme, capability)
            a.needs = needs_for_code(scheme, a.code)
        ans = session.exec(select(Answer).where(Answer.requirement_id == requirement_id)).first()
        a.gaps, a.gap_owner = gap_rules.analyse(req.text, req.mandatory, ans.compliance_status if ans else "",
                                                list(ans.flags or []) if ans else [], capability, [])
    if code is not None:
        if code and (scheme is None or code not in {c.code for c in scheme.codes}):
            raise AssessmentError("That is not one of this RFP's response codes.")
        a.code = code
        a.needs = needs_for_code(scheme, code)
    if estimate is not None:
        a.estimate = estimate.strip() or None
    if attachments is not None:
        a.attachments = [x.strip() for x in attachments if x.strip()]
    if confirm:
        a.confirmed, a.confirmed_by = True, actor
    a.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    audit.record(session, "answer", "assessment_confirmed" if confirm else "assessment_edited", rfp_id=rfp_id,
                 entity_id=requirement_id, actor=actor,
                 payload={"req_id": req.req_id, "before": before,
                          "after": {"capability": a.capability, "code": a.code, "estimate": a.estimate,
                                    "confirmed": a.confirmed}})
    session.commit()
    return a
