"""The human side of the loop. Nothing is final until the SE accepts it, and any later change withdraws that.

Every action is written to the audit log with who did it, so "who approved this wording, and when" always
has an answer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from .. import enums
from ..agent.textutil import MARK, unsupported_figures
from ..models import Answer, Citation, KbChunk, Requirement
from . import audit

# who an answer can be handed to for a decision the SE cannot make alone
ASSIGNEES = ("SME", "Security Officer", "Commercial Desk", "Legal (Contracts)", "Account Management",
             "Shariah Board Liaison", "Product Owner", "Product Management", "Engineering", "Solution Architecture")


class ReviewError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load(session: Session, rfp_id: int, requirement_id: int) -> tuple[Requirement, Answer]:
    req = session.get(Requirement, requirement_id)
    if req is None or req.rfp_id != rfp_id:
        raise ReviewError("Requirement not found in this RFP", 404)
    ans = session.exec(select(Answer).where(Answer.requirement_id == requirement_id)).first()
    if ans is None:
        raise ReviewError("This requirement has not been drafted yet", 409)
    return req, ans


def current_text(ans: Answer) -> str:
    return (ans.final_text if ans.final_text is not None else ans.draft_text) or ""


def accept(session: Session, rfp_id: int, requirement_id: int, reviewer: str) -> Answer:
    req, ans = load(session, rfp_id, requirement_id)
    if ans.error:
        raise ReviewError("This answer failed to draft. Regenerate it first.", 409)
    if not current_text(ans).strip():
        raise ReviewError(
            "There is no answer text to accept. Write one (or hand this to its owner) first.", 422)
    ans.review_status = enums.REVIEW_ACCEPTED
    ans.reviewer = reviewer or "se"
    ans.reviewed_at = _now()
    ans.updated_at = _now()
    if (ans.flag_details or {}).get("recheck"):  # approving again is the re-check
        ans.flag_details = {k: v for k, v in ans.flag_details.items() if k != "recheck"}
    audit.record(session, "answer", "accepted", rfp_id=rfp_id, entity_id=ans.id, actor=ans.reviewer,
                 payload={"req_id": req.req_id, "status": ans.compliance_status, "edited": ans.edited,
                          "flags": ans.flags})
    session.commit()
    return ans


def _evidence_for(session: Session, ans: Answer) -> str:
    chunk_ids = [c.chunk_id for c in session.exec(select(Citation).where(Citation.answer_id == ans.id)).all()
                 if c.chunk_id]
    return "\n".join(session.get(KbChunk, i).text for i in chunk_ids)


def edit(session: Session, rfp_id: int, requirement_id: int, text: str, reviewer: str) -> Answer:
    """Replace the answer wording. Withdraws any approval and says if the SE's text states figures that
    the cited sources do not (those are the SE's own facts, and are marked as such, not as sourced)."""
    req, ans = load(session, rfp_id, requirement_id)
    text = text.strip()
    if not text:
        raise ReviewError("The edited answer cannot be empty.", 422)
    before = current_text(ans)
    if text == before:
        return ans
    was_accepted = ans.review_status == enums.REVIEW_ACCEPTED

    ans.final_text = text
    ans.edited = True
    ans.reviewer = reviewer or "se"
    ans.updated_at = _now()
    if was_accepted:
        ans.review_status = enums.REVIEW_DRAFTED
        ans.reviewed_at = None

    own = unsupported_figures(text, _evidence_for(session, ans))
    flags = [f for f in (ans.flags or []) if f != enums.FLAG_SE_PROVIDED]
    details = dict(ans.flag_details or {})
    details.pop("se_edit_figures", None)
    if own:
        flags.append(enums.FLAG_SE_PROVIDED)
        details["se_edit_figures"] = own
    details["words"] = len(MARK.sub("", text).split())
    ans.flags, ans.flag_details = flags, details
    ans.needs_review = True

    audit.record(session, "answer", "edited", rfp_id=rfp_id, entity_id=ans.id, actor=ans.reviewer,
                 payload={"req_id": req.req_id, "before": before[:600], "after": text[:600],
                          "approval_withdrawn": was_accepted, "se_provided_figures": own})
    session.commit()
    return ans


def set_status(session: Session, rfp_id: int, requirement_id: int, status: str, reviewer: str) -> Answer:
    """The SE decides the compliance status. Needed for owner-routed items and whenever the SE disagrees."""
    if status not in (enums.COMPLIED, enums.PARTIAL, enums.NOT_COMPLIED, enums.CANNOT_CONFIRM):
        raise ReviewError("Status must be one of: Complied, Partially Complied, Not Complied, Cannot Confirm.", 422)
    req, ans = load(session, rfp_id, requirement_id)
    before = ans.compliance_status
    if before == status:
        return ans
    ans.compliance_status = status
    ans.reviewer = reviewer or "se"
    if ans.review_status == enums.REVIEW_ACCEPTED:
        ans.review_status, ans.reviewed_at = enums.REVIEW_DRAFTED, None  # a changed status needs a fresh approval
    ans.updated_at = _now()
    audit.record(session, "answer", "status_changed", rfp_id=rfp_id, entity_id=ans.id, actor=ans.reviewer,
                 payload={"req_id": req.req_id, "before": before, "after": status})
    session.commit()
    return ans


def revert_edit(session: Session, rfp_id: int, requirement_id: int, reviewer: str) -> Answer:
    """Throw the SE's wording away and go back to the agent's draft."""
    req, ans = load(session, rfp_id, requirement_id)
    if ans.final_text is None:
        return ans
    ans.final_text, ans.edited = None, False
    ans.flags = [f for f in (ans.flags or []) if f != enums.FLAG_SE_PROVIDED]
    details = dict(ans.flag_details or {})
    details.pop("se_edit_figures", None)
    ans.flag_details = details
    if ans.review_status == enums.REVIEW_ACCEPTED:
        ans.review_status, ans.reviewed_at = enums.REVIEW_DRAFTED, None
    ans.updated_at = _now()
    audit.record(session, "answer", "edit_reverted", rfp_id=rfp_id, entity_id=ans.id, actor=reviewer or "se",
                 payload={"req_id": req.req_id})
    session.commit()
    return ans


def flag(session: Session, rfp_id: int, requirement_id: int, assignee: str | None, note: str, reviewer: str) -> Answer:
    """Hand an answer to someone else (SME, Security Officer, ...), or take it back with assignee=None."""
    req, ans = load(session, rfp_id, requirement_id)
    if assignee and assignee not in ASSIGNEES:
        raise ReviewError(f"Unknown assignee. Choose one of: {', '.join(ASSIGNEES)}", 422)
    ans.assigned_to = assignee or None
    ans.needs_review = True if assignee else ans.needs_review
    if assignee and ans.review_status == enums.REVIEW_ACCEPTED:
        ans.review_status, ans.reviewed_at = enums.REVIEW_DRAFTED, None  # a handed-off answer is not final
    details = dict(ans.flag_details or {})
    details["handoff_note"] = note.strip() if note else details.get("handoff_note", "")
    ans.flag_details = details
    ans.updated_at = _now()
    audit.record(session, "answer", "flagged" if assignee else "unflagged", rfp_id=rfp_id, entity_id=ans.id,
                 actor=reviewer or "se", payload={"req_id": req.req_id, "assignee": assignee, "note": note})
    session.commit()
    return ans


def summary(session: Session, rfp_id: int) -> dict:
    """Numbers for the header and the queue filters (All / Needs review / Approved / Clarify / Flagged)."""
    reqs = session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id)).all()
    ids = {r.id for r in reqs}
    answers = [a for a in session.exec(select(Answer)).all() if a.requirement_id in ids]
    drafted = [a for a in answers if a.review_status != enums.REVIEW_QUEUED]
    return {
        "total": len(reqs),
        "drafted": len(drafted),
        "accepted": sum(a.review_status == enums.REVIEW_ACCEPTED for a in answers),
        "needs_review": sum(a.needs_review and a.review_status != enums.REVIEW_ACCEPTED for a in drafted),
        "clarify": sum(a.triage == enums.TRIAGE_CLARIFY for a in drafted),
        "owner": sum(a.triage == enums.TRIAGE_OWNER for a in drafted),
        "flagged": sum(bool(a.assigned_to) for a in drafted),
        "conflicting": sum(enums.FLAG_CONFLICTING in (a.flags or []) for a in drafted),
        "stale": sum(enums.FLAG_STALE in (a.flags or []) for a in drafted),
        "unanswered": len(reqs) - len(drafted),
    }
