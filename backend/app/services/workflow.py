"""Who has what, and what is waiting on whom: the review board, plus redrafting what has changed without touching what
a person has already approved."""

from __future__ import annotations

from sqlmodel import Session, select

from .. import enums
from ..agent import jobs
from ..models import Answer, ClarificationQuery, Requirement, Rfp
from . import audit
from .matrix import matrix_rows

STATES = ("not_drafted", "pending_clarification", "with_owner", "needs_review", "ready_for_review", "approved",
          "approved_recheck")
LABELS = {"not_drafted": "Not drafted", "pending_clarification": "Waiting for the customer", "with_owner": "With an owner",
          "needs_review": "Needs review", "ready_for_review": "Ready for review", "approved": "Approved",
          "approved_recheck": "Approved, evidence changed"}


def _open_clarifications(session: Session, rfp_id: int) -> tuple[dict[str, list[int]], dict[str, str]]:
    """(req_id -> ids of questions about it still waiting for the customer, req_id -> who owns the first of them)."""
    out: dict[str, list[int]] = {}
    owner: dict[str, str] = {}
    for c in session.exec(select(ClarificationQuery).where(ClarificationQuery.rfp_id == rfp_id)).all():
        if c.status in ("open", "approved"):
            for rid in c.requirement_ids or []:
                out.setdefault(rid, []).append(c.id)
                if c.assigned_to:
                    owner.setdefault(rid, c.assigned_to)
    return out, owner


def state_of(row: dict, waiting: list[int], recheck: bool) -> str:
    if row["review_status"] == enums.REVIEW_QUEUED or row["status"] == "Not drafted":
        return "not_drafted"
    if row["review_status"] == enums.REVIEW_ACCEPTED:
        return "approved_recheck" if recheck else "approved"
    if waiting:
        return "pending_clarification"
    if row["assigned_to"]:
        return "with_owner"
    if row["flags"] or row["needs_validation"] or (row["confidence"] or 0) < 0.7 or row["status"] != enums.COMPLIED:
        return "needs_review"
    return "ready_for_review"


def board(session: Session, rfp_id: int) -> dict:
    rows = matrix_rows(session, rfp_id)
    waiting, clar_owner = _open_clarifications(session, rfp_id)
    items, counts, by_role = [], {s: 0 for s in STATES}, {}
    for r in rows:
        st = state_of(r, waiting.get(r["req_id"], []), bool(r["recheck"]))
        counts[st] += 1
        items.append({"req_id": r["req_id"], "requirement_id": r["requirement_id"], "state": st, "label": LABELS[st],
                      "assigned_to": r["assigned_to"], "suggested_owner": r["gap_owner"],
                      "clarifications": waiting.get(r["req_id"], []), "mandatory": r["mandatory"]})
        who = r["assigned_to"] or (clar_owner.get(r["req_id"]) if st == "pending_clarification" else None) \
            or ("Unassigned" if st in ("needs_review", "pending_clarification") else None)
        if who and st not in ("approved", "ready_for_review"):
            by_role.setdefault(who, []).append(r["req_id"])
    return {"counts": counts, "labels": LABELS, "by_role": {k: v for k, v in sorted(by_role.items())}, "items": items}


def assign_suggested(session: Session, rfp_id: int, actor: str) -> dict:
    """Give each requirement that has a gap, and nobody looking at it, the role the gap analysis suggests."""
    assigned = []
    for r in matrix_rows(session, rfp_id):
        if not r["gap_owner"] or r["assigned_to"] or r["review_status"] == enums.REVIEW_ACCEPTED or not r["answer_text"]:
            continue
        ans = session.exec(select(Answer).where(Answer.requirement_id == r["requirement_id"])).first()
        ans.assigned_to, ans.needs_review = r["gap_owner"], True
        assigned.append({"req_id": r["req_id"], "assigned_to": r["gap_owner"]})
    audit.record(session, "rfp", "suggested_owners_assigned", rfp_id=rfp_id, entity_id=rfp_id, actor=actor,
                 payload={"assigned": assigned})
    session.commit()
    return {"assigned": assigned}


def affected_by_replies(session: Session, rfp_id: int) -> list[int]:
    """Requirements with an answered clarification, and the requirements that depend on them."""
    reqs = {r.req_id: r for r in session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id)).all()}
    hit: set[str] = set()
    for c in session.exec(select(ClarificationQuery).where(ClarificationQuery.rfp_id == rfp_id,
                                                           ClarificationQuery.status == "answered")).all():
        hit.update(rid for rid in c.requirement_ids or [] if rid in reqs)
    for rid in list(hit):
        hit.update(d for d in (reqs[rid].interpretation or {}).get("depended_on_by", []) if d in reqs)
    return sorted(reqs[r].id for r in hit)


def regenerate_affected(session: Session, engine, gateway, rfp_id: int, *, requirement_ids: list[int] | None,
                        reason: str, actor: str) -> dict:
    """Redraft what changed, and leave every approved answer exactly as it is. An approved answer that is affected is
    not rewritten: it is marked so a person re-checks it, and approving it again clears the mark."""
    ids = requirement_ids if requirement_ids is not None else affected_by_replies(session, rfp_id)
    redraft, kept = [], []
    for rid in ids:
        ans = session.exec(select(Answer).where(Answer.requirement_id == rid)).first()
        req = session.get(Requirement, rid)
        if req is None or req.rfp_id != rfp_id:
            continue
        if ans is not None and ans.review_status == enums.REVIEW_ACCEPTED:
            ans.flag_details = {**(ans.flag_details or {}), "recheck": reason or "Evidence changed since this was approved."}
            ans.needs_review = True
            kept.append(req.req_id)
        else:
            redraft.append(rid)
    audit.record(session, "rfp", "affected_regenerated", rfp_id=rfp_id, entity_id=rfp_id, actor=actor,
                 payload={"reason": reason, "redrafted": len(redraft), "kept_approved": kept})
    session.commit()
    job_id = merged = None
    if redraft and gateway is not None:
        job_id, merged = jobs.start_recalc(engine, gateway, rfp_id, redraft, reason or "affected answers", assess=True)
    reqs = {r.id: r.req_id for r in session.exec(select(Requirement).where(Requirement.id.in_(redraft))).all()} if redraft else {}
    return {"redrafting": sorted(reqs.values()), "kept_approved": kept, "job_id": job_id, "joined_running_job": merged}
