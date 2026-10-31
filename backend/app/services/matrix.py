"""One row per requirement joining the requirement, its answer and its assessment: the compliance matrix, the gap
analysis and the review gate are all views over these rows."""

from __future__ import annotations

from sqlmodel import Session, select

from .. import enums
from ..agent.assess import answer_fingerprint
from ..models import Answer, Assessment, Requirement, Section


def answer_text(a: Answer | None) -> str:
    return ((a.final_text if a.final_text is not None else a.draft_text) or "").strip() if a else ""


def matrix_rows(session: Session, rfp_id: int) -> list[dict]:
    sections = {s.id: s for s in session.exec(select(Section).where(Section.rfp_id == rfp_id)).all()}
    reqs = session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id).order_by(Requirement.order)).all()
    ids = [r.id for r in reqs]
    answers = {a.requirement_id: a for a in session.exec(select(Answer).where(Answer.requirement_id.in_(ids))).all()} if ids else {}
    assessments = {a.requirement_id: a for a in session.exec(
        select(Assessment).where(Assessment.requirement_id.in_(ids))).all()} if ids else {}
    out = []
    for r in reqs:
        ans, asm = answers.get(r.id), assessments.get(r.id)
        sec = sections.get(r.section_id)
        text = answer_text(ans)
        current = bool(asm and ans and asm.answer_hash == answer_fingerprint(text, ans.compliance_status, list(ans.flags or [])))
        out.append({
            "requirement_id": r.id, "req_id": r.req_id, "section_code": sec.code if sec else "",
            "section_title": sec.title if sec else "", "text": r.text, "mandatory": r.mandatory,
            "status": ans.compliance_status if ans else "Not drafted", "confidence": ans.confidence if ans else None,
            "flags": list(ans.flags or []) if ans else [], "review_status": ans.review_status if ans else "queued",
            "triage": ans.triage if ans else None, "assigned_to": ans.assigned_to if ans else None,
            "answer_text": text, "recheck": (ans.flag_details or {}).get("recheck") if ans else None,
            "assessed": asm is not None, "assessment_current": current,
            "capability": asm.capability if asm else "", "code": asm.code if asm else "",
            "needs": list(asm.needs or []) if asm else [], "needs_validation": asm.needs_validation if asm else False,
            "validation_reasons": list(asm.validation_reasons or []) if asm else [],
            "confirmed": asm.confirmed if asm else False, "confirmed_by": asm.confirmed_by if asm else None,
            "estimate": asm.estimate if asm else None, "attachments": list(asm.attachments or []) if asm else [],
            "gaps": list(asm.gaps or []) if asm else [], "gap_owner": asm.gap_owner if asm else "",
            "issues": list(asm.issues or []) if asm else [], "basis": asm.basis if asm else "",
            "parts": list(asm.parts or []) if asm else [], "assessment_error": asm.error if asm else None,
            "interpretation": dict(r.interpretation or {}),
        })
    return out


def unmet_needs(row: dict) -> list[str]:
    """What the chosen code asks for that is not there yet: an explanation, an estimate, an attachment."""
    unmet = []
    for need in row["needs"]:
        if need == enums.NEED_EXPLANATION and not row["answer_text"]:
            unmet.append(need)
        elif need == enums.NEED_ESTIMATE and not (row["estimate"] or "").strip():
            unmet.append(need)
        elif need == enums.NEED_ATTACHMENT and not row["attachments"]:
            unmet.append(need)
    return unmet


def gaps_summary(rows: list[dict]) -> dict:
    """The gap analysis: confirmed product gaps kept apart from knowledge gaps, grouped by who should look."""
    items, by_type, by_owner = [], {}, {}
    for r in rows:
        for g in r["gaps"]:
            items.append({"req_id": r["req_id"], "requirement_id": r["requirement_id"], "text": r["text"],
                          "mandatory": r["mandatory"], **g})
            by_type[g["type"]] = by_type.get(g["type"], 0) + 1
            by_owner.setdefault(g["owner"], []).append(r["req_id"])
    order = {t: i for i, t in enumerate(enums.GAP_TYPES)}
    items.sort(key=lambda g: (not g["critical"], order.get(g["type"], 9), g["req_id"]))
    return {
        "counts": {t: by_type.get(t, 0) for t in enums.GAP_TYPES},
        "by_owner": {o: sorted(set(ids)) for o, ids in sorted(by_owner.items())},
        "items": items,
        "requirements_with_gaps": len({i["req_id"] for i in items}),
    }
