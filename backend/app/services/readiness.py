"""Is this response ready to go to review, and what is left before it can be submitted?

Two different questions, kept apart:

  * the REVIEW GATE: has the agent left anything that should stop a reviewer's time being wasted? Unsupported claims,
    explanations that contradict their code, mandatory requirements nobody is dealing with. A person can still
    proceed over a blocker, but only by saying so, and that is recorded.
  * the SUBMISSION CHECKLIST: everything that must be true before the file goes to the customer, plus the annexures the
    instructions asked for, which a person ticks off.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone

from sqlmodel import Session, select

from .. import enums
from ..agent.crosscheck import inconsistent_answers, unsupported_claims
from ..agent.formatrules import over_limit
from ..agent.scheme import scheme_of, unmapped_codes
from ..config import get_settings
from ..models import Answer, Citation, ClarificationQuery, KbChunk, Requirement, Rfp
from . import audit
from .matrix import matrix_rows, unmet_needs


class ReadinessError(Exception):
    def __init__(self, message: str, status_code: int = 409, payload: dict | None = None):
        super().__init__(message)
        self.status_code, self.payload = status_code, payload or {}


def _today() -> date:
    return get_settings().as_of_date or date.today()


def _evidence(session: Session, rfp_id: int) -> dict[int, list[str]]:
    """requirement_id -> the text of every passage its answer cites."""
    rows = session.exec(select(Answer, Citation, KbChunk).where(
        Answer.requirement_id == Requirement.id, Requirement.rfp_id == rfp_id,
        Citation.answer_id == Answer.id, KbChunk.id == Citation.chunk_id)).all()
    out: dict[int, list[str]] = {}
    for ans, _cit, chunk in rows:
        out.setdefault(ans.requirement_id, []).append(chunk.text)
    return out


def readiness(session: Session, rfp_id: int) -> dict:
    rfp = session.get(Rfp, rfp_id)
    rules = rfp.format_rules or {}
    scheme = scheme_of(rules)
    rows = matrix_rows(session, rfp_id)
    evidence = _evidence(session, rfp_id)
    clar = session.exec(select(ClarificationQuery).where(ClarificationQuery.rfp_id == rfp_id)).all()
    issues: list[dict] = []

    def add(severity: str, code: str, message: str, ids: list[str] | None = None) -> None:
        if ids is None or ids:
            issues.append({"severity": severity, "code": code, "message": message, "requirements": ids or []})

    def many(ids: list[str]) -> str:
        return ", ".join(ids[:8]) + ("..." if len(ids) > 8 else "")

    # ---- blockers: things the agent should not hand to a reviewer as they are
    unsupported = [r["req_id"] for r in rows
                   if r["answer_text"] and unsupported_claims(r["answer_text"], evidence.get(r["requirement_id"], []))]
    add("blocking", "unsupported_claims",
        f"{len(unsupported)} answer(s) contain a sentence the cited sources do not support: {many(unsupported)}. "
        "Remove or source the claim.", unsupported)
    misaligned = [r["req_id"] for r in rows if r["issues"] and not r["confirmed"]]
    add("blocking", "code_explanation_mismatch",
        f"{len(misaligned)} response code(s) do not match their explanation: {many(misaligned)}.", misaligned)
    clashes = inconsistent_answers(rows)
    for c in clashes:
        add("blocking", "inconsistent_answers", f"Answers disagree: {c['detail']}. A bid must not contradict itself.",
            c["requirements"])
    nobody = [r["req_id"] for r in rows if r["mandatory"] is not False and not r["answer_text"] and not r["assigned_to"]]
    add("blocking", "mandatory_unanswered",
        f"{len(nobody)} mandatory requirement(s) have no answer and nobody assigned: {many(nobody)}.", nobody)

    # ---- warnings
    if scheme is None:
        add("info", "no_response_scheme", "The RFP does not appear to ask for response codes, or none were detected.")
    else:
        add("warning", "unmapped_codes", "These response codes are not mapped to a capability, so none will be suggested "
            f"for them: {', '.join(unmapped_codes(scheme))}.", [] if not unmapped_codes(scheme) else None)
        if not rules.get("confirmed"):
            add("warning", "scheme_unconfirmed", "The response codes and formatting rules have not been confirmed.")
        if scheme.kind == enums.SCHEME_CAPABILITY:
            todo = [r["req_id"] for r in rows if r["answer_text"] and (not r["assessed"] or not r["assessment_current"])]
            add("warning", "not_assessed", f"{len(todo)} answer(s) have no current capability assessment: {many(todo)}. "
                "Run the assessment.", todo)
    unvalidated = [r["req_id"] for r in rows if r["needs_validation"] and not r["confirmed"]]
    add("warning", "codes_need_validation",
        f"{len(unvalidated)} suggested code(s) rest on incomplete evidence and need a person to validate them: "
        f"{many(unvalidated)}.", unvalidated)
    unmet = [r["req_id"] for r in rows if r["code"] and unmet_needs(r)]
    add("warning", "code_needs_missing",
        f"{len(unmet)} requirement(s) have a code that needs an explanation, estimate or attachment that is not there "
        f"yet: {many(unmet)}.", unmet)
    risky = [r["req_id"] for r in rows if set(r["flags"]) & {enums.FLAG_STALE, enums.FLAG_CONFLICTING}]
    add("warning", "stale_or_conflicting", f"{len(risky)} answer(s) rely on stale or conflicting sources: {many(risky)}.",
        risky)
    recheck = [r["req_id"] for r in rows if r["recheck"] and r["review_status"] == enums.REVIEW_ACCEPTED]
    add("warning", "approved_may_be_outdated",
        f"{len(recheck)} approved answer(s) were left as they are, but their evidence changed since: {many(recheck)}. "
        "Re-check and approve them again.", recheck)
    owners = [r["req_id"] for r in rows if r["triage"] == enums.TRIAGE_OWNER and not r["answer_text"]]
    add("info", "awaiting_owner", f"{len(owners)} requirement(s) are with an owner (pricing, legal and similar): "
        f"{many(owners)}.", owners)
    high = [c for c in clar if c.status == "open" and c.priority == enums.PRIORITY_HIGH]
    add("warning", "high_priority_clarifications",
        f"{len(high)} high-priority clarification question(s) are still open.", [] if not high else None)

    counts = {
        "requirements": len(rows), "mandatory": sum(r["mandatory"] is not False for r in rows),
        "answered": sum(bool(r["answer_text"]) for r in rows),
        "approved": sum(r["review_status"] == enums.REVIEW_ACCEPTED for r in rows),
        "assessed": sum(r["assessed"] and r["assessment_current"] for r in rows),
        "codes_assigned": sum(bool(r["code"]) for r in rows),
        "codes_validated": sum(bool(r["code"]) and (r["confirmed"] or not r["needs_validation"]) for r in rows),
        "open_clarifications": sum(c.status == "open" for c in clar),
    }
    blockers = [i for i in issues if i["severity"] == "blocking"]
    return {"can_proceed": not blockers, "in_review": rfp.status == "in_review", "blockers": blockers,
            "warnings": [i for i in issues if i["severity"] == "warning"],
            "info": [i for i in issues if i["severity"] == "info"], "counts": counts}


def proceed_to_review(session: Session, rfp_id: int, *, acknowledge: bool, reason: str, actor: str) -> dict:
    state = readiness(session, rfp_id)
    if state["blockers"] and not (acknowledge and reason.strip()):
        raise ReadinessError("This response has issues a reviewer should not be asked to find. Fix them, or proceed "
                             "anyway with a reason.", 409, state)
    rfp = session.get(Rfp, rfp_id)
    rfp.status = "in_review"
    audit.record(session, "rfp", "proceeded_to_review", rfp_id=rfp_id, entity_id=rfp_id, actor=actor,
                 payload={"acknowledged_blockers": [b["code"] for b in state["blockers"]], "reason": reason.strip(),
                          "warnings": len(state["warnings"]), "counts": state["counts"]})
    session.commit()
    return readiness(session, rfp_id)


# ---------------------------------------------------------------- submission checklist
_ANNEX = re.compile(r"annex|appendix|appendices|attach|enclose|include a copy|copy of|certificate of|company profile|"
                    r"financial statements?|reference letters?|power of attorney|brochure", re.I)


def _key(label: str) -> str:
    return hashlib.sha1(label.lower().encode()).hexdigest()[:10]


def manual_items(rfp: Rfp) -> list[dict]:
    """Annexures and enclosures the instructions ask for. A person ticks these off: we cannot know they were prepared."""
    seen, out = set(), []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", rfp.submission_instructions or ""):
        s = re.sub(r"\s+", " ", sentence).strip()
        if len(s) < 12 or not _ANNEX.search(s):
            continue
        label = s[:160]
        if _key(label) in seen:
            continue
        seen.add(_key(label))
        state = ((rfp.checklist or {}).get("manual") or {}).get(_key(label), {})
        out.append({"key": _key(label), "label": label, "kind": "manual", "status": "done" if state.get("done") else "outstanding",
                    "detail": (f"Ticked by {state.get('by')}" if state.get("done") else "Prepare and attach it.")})
    return out


def checklist(session: Session, rfp_id: int) -> dict:
    rfp = session.get(Rfp, rfp_id)
    rules = rfp.format_rules or {}
    rows = matrix_rows(session, rfp_id)
    evidence = _evidence(session, rfp_id)
    scheme = scheme_of(rules)
    items: list[dict] = []

    def add(key: str, label: str, done: bool, detail: str = "", na: bool = False) -> None:
        items.append({"key": key, "label": label, "kind": "auto", "status": "na" if na else "done" if done else "outstanding",
                      "detail": detail})

    mand = [r for r in rows if r["mandatory"] is not False]
    approved = [r for r in mand if r["review_status"] == enums.REVIEW_ACCEPTED]
    add("mandatory_approved", "Every mandatory requirement answered and approved", len(approved) == len(mand),
        f"{len(approved)} of {len(mand)} approved")
    unsupported = [r["req_id"] for r in rows if r["answer_text"] and unsupported_claims(r["answer_text"], evidence.get(r["requirement_id"], []))]
    add("no_unsupported", "No unsupported claims in any answer", not unsupported,
        f"Check {', '.join(unsupported[:6])}" if unsupported else "")
    clashes = inconsistent_answers(rows)
    add("consistent", "No two answers contradict each other", not clashes,
        "; ".join(c["detail"] for c in clashes[:3]) if clashes else "")
    if scheme is not None and scheme.kind == enums.SCHEME_CAPABILITY:
        coded = [r for r in rows if r["answer_text"] and r["triage"] != enums.TRIAGE_OWNER]
        missing = [r["req_id"] for r in coded if not r["code"] or (r["needs_validation"] and not r["confirmed"])]
        add("codes_validated", "A validated response code for every requirement", not missing,
            f"{len(missing)} still need a code or a person's validation" if missing else "")
    else:
        add("codes_validated", "A validated response code for every requirement", True, na=True)
    unmet = [r["req_id"] for r in rows if r["code"] and unmet_needs(r)]
    add("code_needs", "Explanations, estimates and attachments the codes require", not unmet,
        f"Missing for {', '.join(unmet[:6])}" if unmet else "")
    over = [r["req_id"] for r in rows if r["answer_text"] and over_limit(rules, r["answer_text"])]
    add("word_limit", f"Every answer within the {rules.get('word_limit')}-word limit", not over,
        f"Too long: {', '.join(over[:6])}" if over else "", na=not rules.get("word_limit"))
    add("rules_confirmed", "Formatting rules and response codes confirmed", bool(rules.get("confirmed")))
    open_clar = session.exec(select(ClarificationQuery).where(ClarificationQuery.rfp_id == rfp_id,
                                                              ClarificationQuery.status == "open")).all()
    due = rules.get("clarification_deadline")
    left = (date.fromisoformat(due) - _today()).days if due else None
    add("clarifications", "Clarification questions sent to the customer" + (f" by {due}" if due else ""),
        not open_clar, (f"{len(open_clar)} still open" + (f", {left} day(s) left" if left is not None else ""))
        if open_clar else "")
    sub = rules.get("deadline")
    left = (date.fromisoformat(sub) - _today()).days if sub else None
    add("submission", "Submitted as " + (rules.get("file_name") or "the required file") + (f" by {sub}" if sub else ""),
        False, f"{left} day(s) left" if left is not None else "", na=not (sub or rules.get("file_name")))
    items += manual_items(rfp)
    done = sum(i["status"] == "done" for i in items)
    open_ = sum(i["status"] == "outstanding" for i in items)
    return {"items": items, "done": done, "outstanding": open_, "complete": open_ == 0}


def set_manual(session: Session, rfp_id: int, key: str, done: bool, actor: str) -> dict:
    rfp = session.get(Rfp, rfp_id)
    if key not in {i["key"] for i in manual_items(rfp)}:
        raise ReadinessError("No such checklist item.", 404)
    state = dict(rfp.checklist or {})
    manual = dict(state.get("manual") or {})
    manual[key] = {"done": done, "by": actor, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    state["manual"] = manual
    rfp.checklist = state
    audit.record(session, "rfp", "checklist_item_ticked" if done else "checklist_item_unticked", rfp_id=rfp_id,
                 entity_id=rfp_id, actor=actor, payload={"key": key})
    session.commit()
    return checklist(session, rfp_id)
