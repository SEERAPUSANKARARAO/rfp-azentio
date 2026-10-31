"""Everything an export needs, decided once: which answers go in, in what form, with which sources.

The rules (human control first):
  * Default is approved answers only. Anything the SE has not accepted is left blank.
  * A review copy can include drafts, but then every unapproved answer is marked and the file name says DRAFT.
  * The compliance code is the RFP's own vocabulary. "Cannot Confirm" never gets a code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import Session, select

from .. import enums
from ..agent.formatrules import code_for, over_limit, word_count
from ..config import get_settings
from ..models import Answer, ClarificationQuery, Citation, KbDocument, Requirement, Rfp, Section

DRAFT_MARK = "[DRAFT - not approved] "


@dataclass
class ExportRow:
    requirement: Requirement
    section_code: str
    section_title: str
    answer: Answer | None
    text: str = ""  # what goes in the response cell (already marked if it is an unapproved draft)
    included: bool = False
    approved: bool = False
    code: str = ""
    citations: list[Citation] = field(default_factory=list)


@dataclass
class ExportPlan:
    rfp: Rfp
    rules: dict
    rows: list[ExportRow]
    clarifications: list[ClarificationQuery]
    draft_copy: bool  # true if any unapproved answer is included
    vendor: str
    sources: list[dict]


def sources_text(citations: list[Citation]) -> str:
    """'[1] Disaster Recovery Policy v3 (2026-03-10), p.1, 1. Objectives; [2] ...' : what a reader needs to find it."""
    out = []
    for c in sorted(citations, key=lambda c: c.marker):
        when = f" ({c.doc_date.isoformat()})" if c.doc_date else ""
        out.append(f"[{c.marker}] {c.doc_title}{when}, {c.location}")
    return "; ".join(out)


def build_plan(session: Session, rfp_id: int, *, approved_only: bool = True) -> ExportPlan:
    rfp = session.get(Rfp, rfp_id)
    rules = rfp.format_rules or {}
    sections = {s.id: s for s in session.exec(select(Section).where(Section.rfp_id == rfp_id)).all()}
    reqs = session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id).order_by(Requirement.order)).all()
    answers = {a.requirement_id: a for a in session.exec(select(Answer)).all()}
    cites: dict[int, list[Citation]] = {}
    for c in session.exec(select(Citation)).all():
        cites.setdefault(c.answer_id, []).append(c)

    rows, draft_copy = [], False
    for r in reqs:
        sec = sections.get(r.section_id)
        a = answers.get(r.id)
        row = ExportRow(r, sec.code if sec else "", sec.title if sec else "", a)
        if a is not None:
            body = ((a.final_text if a.final_text is not None else a.draft_text) or "").strip()
            approved = a.review_status == enums.REVIEW_ACCEPTED
            if body and (approved or not approved_only):
                row.included, row.approved = True, approved
                row.text = body if approved else DRAFT_MARK + body
                row.code = code_for(rules, a.compliance_status)
                row.citations = sorted(cites.get(a.id, []), key=lambda c: c.marker)
                draft_copy = draft_copy or not approved
        rows.append(row)

    seen: dict[int, dict] = {}
    for row in rows:
        for c in row.citations:
            key = c.doc_id or id(c)
            e = seen.setdefault(key, {"doc_id": c.doc_id, "title": c.doc_title, "date": c.doc_date,
                                      "locations": set(), "requirements": []})
            e["locations"].add(c.location)
            if row.requirement.req_id not in e["requirements"]:
                e["requirements"].append(row.requirement.req_id)
    kb = {d.id: d for d in session.exec(select(KbDocument)).all()}
    sources = []
    for e in seen.values():
        d = kb.get(e["doc_id"])
        sources.append({"title": e["title"], "date": e["date"], "version": d.version if d else None,
                        "kind": d.kind if d else "", "locations": sorted(e["locations"]),
                        "requirements": e["requirements"]})
    sources.sort(key=lambda s: s["title"])

    clar = session.exec(select(ClarificationQuery).where(
        ClarificationQuery.rfp_id == rfp_id, ClarificationQuery.status != "dismissed")).all()
    return ExportPlan(rfp, rules, rows, clar, draft_copy, get_settings().vendor_name, sources)


def check_plan(plan: ExportPlan) -> dict:
    """What would a careful reviewer want to know before this leaves the building?

    blocking: a submission copy that breaks a rule the RFP stated (a word limit). A review copy only warns.
    Everything else is a warning the SE can accept.
    """
    issues: list[dict] = []
    rules = plan.rules

    def add(severity: str, code: str, message: str, ids: list[str] | None = None):
        issues.append({"severity": severity, "code": code, "message": message, "requirements": ids or []})

    over = [(r.requirement.req_id, over_limit(rules, r.text)) for r in plan.rows if r.included and over_limit(rules, r.text)]
    if over:
        who = ", ".join(f"{n} word(s) in {i}" for i, n in over[:5]) + ("..." if len(over) > 5 else "")
        # A submission copy must obey the RFP's own limit. A review copy is for reviewing, so it only warns.
        add("warning" if plan.draft_copy else "blocking", "over_word_limit",
            f"{len(over)} answer(s) exceed the RFP's {rules['word_limit']}-word limit (by {who}). "
            + ("Shorten them before this goes out." if plan.draft_copy else "Shorten them, or export a draft copy."),
            [i for i, _ in over])

    no_answer = [r.requirement.req_id for r in plan.rows if r.requirement.mandatory is not False and not r.included]
    if no_answer:
        add("warning", "mandatory_unanswered",
            f"{len(no_answer)} mandatory requirement(s) have no approved answer: "
            f"{', '.join(no_answer[:8])}{'...' if len(no_answer) > 8 else ''}.", no_answer)

    handed = [r.requirement.req_id for r in plan.rows if r.included and r.answer and r.answer.assigned_to]
    if handed:
        add("warning", "handed_off", f"{len(handed)} answer(s) are still assigned to someone else.", handed)

    risky = [r.requirement.req_id for r in plan.rows if r.included and r.approved and r.answer and
             set(r.answer.flags or []) & {enums.FLAG_CONFLICTING, enums.FLAG_STALE, enums.FLAG_INJECTION,
                                          enums.FLAG_UNSUPPORTED}]
    if risky:
        add("warning", "approved_with_flags",
            f"{len(risky)} approved answer(s) still carry evidence flags (stale, conflicting, unsupported or "
            "injection). Confirm they were reviewed.", risky)

    if not rules.get("confirmed"):
        add("warning", "rules_unconfirmed", "The detected formatting rules have not been confirmed by the SE.")
    open_clar = [c for c in plan.clarifications if c.status == "open"]
    if open_clar:
        add("info", "open_clarifications", f"{len(open_clar)} clarification question(s) are still open.")
    if plan.draft_copy:
        add("info", "draft_copy", "This copy includes answers that are not approved and is marked DRAFT.")

    counts = {
        "requirements": len(plan.rows), "included": sum(r.included for r in plan.rows),
        "approved": sum(r.approved for r in plan.rows),
        "words_total": sum(word_count(r.text) for r in plan.rows if r.included),
    }
    return {"ready": not any(i["severity"] == "blocking" for i in issues), "issues": issues, "counts": counts}
