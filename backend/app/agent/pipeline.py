"""Draft one requirement: triage, retrieve, model draft, validate, score, persist.

The model only drafts. Everything that decides what the reviewer sees (what survives, which status
is allowed, which flags are raised, how confident to be) is deterministic code in validate.py/score.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlmodel import Session, select

from .. import enums
from ..config import get_settings
from ..llm.gateway import LLMGateway
from ..models import Answer, Citation, ClarificationQuery, Requirement, Rfp, Section
from ..rag.retrieve import retrieve
from ..services import audit
from .prompts import SYSTEM, build_prompt
from .schemas import DraftOut
from .score import confidence
from .triage import triage_owner
from .validate import sanitize_requirement, validate_draft


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _upsert_answer(session: Session, requirement_id: int) -> Answer:
    ans = session.exec(select(Answer).where(Answer.requirement_id == requirement_id)).first()
    if ans is None:
        ans = Answer(requirement_id=requirement_id)
        session.add(ans)
        session.flush()
    for c in session.exec(select(Citation).where(Citation.answer_id == ans.id)).all():
        session.delete(c)
    return ans


def _save_owner(session: Session, req: Requirement, owner) -> int:
    ans = _upsert_answer(session, req.id)
    ans.draft_text = ""
    ans.compliance_status = enums.PENDING
    ans.confidence = None
    ans.confidence_breakdown = {}
    ans.triage = enums.TRIAGE_OWNER
    ans.flags = [owner.flag]
    ans.flag_details = {"owner": owner.owner, "reason": owner.reason}
    ans.owner_hint = owner.owner
    ans.assigned_to = owner.owner
    ans.reasoning = (f"This asks about {owner.reason}. Pricing, legal and contractual commitments are never "
                     f"drafted automatically; route to {owner.owner}.")
    ans.review_status = enums.REVIEW_DRAFTED
    ans.needs_review = True
    ans.error = None
    ans.updated_at = _now()
    audit.record(session, "answer", "routed_to_owner", rfp_id=req.rfp_id, entity_id=ans.id,
                 payload={"req_id": req.req_id, "owner": owner.owner})
    session.commit()
    return ans.id


async def draft_requirement(
    engine: Engine,
    gateway: LLMGateway,
    requirement_id: int,
    *,
    conflicts: dict[int, list[dict]] | None = None,
    instruction: str | None = None,
    use_cache: bool = True,
    k: int | None = None,
) -> int:
    """Draft and save one answer. Returns the Answer id. May raise AllProvidersExhausted."""
    s = get_settings()
    with Session(engine) as session:
        req = session.get(Requirement, requirement_id)
        rfp = session.get(Rfp, req.rfp_id)
        section = session.get(Section, req.section_id) if req.section_id else None
        section_title = section.title if section else ""
        word_limit = int((rfp.format_rules or {}).get("word_limit") or s.default_word_limit)

        clean_text, injected = sanitize_requirement(req.text)
        owner = triage_owner(clean_text)
        if owner:
            return _save_owner(session, req, owner)
        # What the customer replied to a question about this requirement is part of the requirement from now on.
        # It is the customer's words, so it is cleaned like any other text from outside before the model sees it.
        replies = [c.answer for c in session.exec(select(ClarificationQuery).where(
            ClarificationQuery.rfp_id == req.rfp_id, ClarificationQuery.status == "answered")).all()
            if req.req_id in (c.requirement_ids or []) and (c.answer or "").strip()]
        if replies:
            clean_text = clean_text + "\n\nCustomer clarification: " + " ".join(sanitize_requirement(r)[0] for r in replies)
        passages = retrieve(session, clean_text, k=k or s.retrieval_k)
        req_id, mandatory, req_pk, rfp_id, rfp_name = req.req_id, req.mandatory, req.id, req.rfp_id, rfp.name

    result = await gateway.complete(
        "draft",
        build_prompt(req_id=req_id, requirement=clean_text, mandatory=mandatory, section_title=section_title,
                     passages=passages, instruction=instruction, rfp_name=rfp_name),
        system=SYSTEM.format(word_limit=word_limit),
        schema=DraftOut,
        max_tokens=s.draft_max_tokens,
        use_cache=use_cache,
    )
    out: DraftOut = result.data

    v = validate_draft(out, passages, clean_text, conflicts_by_chunk=conflicts or {}, injection_removed=injected)
    top_sim = max((p.vec_sim for p in passages if p.vec_sim is not None), default=None)
    score, breakdown = confidence(v, top_sim)
    breakdown["model"] = f"{result.provider}/{result.model}"

    if v.status == enums.CANNOT_CONFIRM:
        triage = enums.TRIAGE_CLARIFY
    elif score >= s.review_confidence:
        triage = enums.TRIAGE_AUTO
    else:
        triage = enums.TRIAGE_LOW
    needs_review = bool(v.flags) or score < s.review_confidence or v.status != enums.COMPLIED

    with Session(engine) as session:
        ans = _upsert_answer(session, req_pk)
        ans.draft_text = v.answer
        ans.final_text = None
        ans.edited = False
        ans.compliance_status = v.status
        ans.confidence = score
        ans.confidence_breakdown = breakdown
        ans.triage = triage
        ans.flags = v.flags
        ans.flag_details = {**v.flag_details, "missing_info": v.missing_info,
                            "words": len(v.answer.split()), "word_limit": word_limit}
        ans.reasoning = v.reasoning
        ans.owner_hint = None
        ans.assigned_to = None
        ans.review_status = enums.REVIEW_DRAFTED
        ans.needs_review = needs_review
        ans.error = None
        ans.updated_at = _now()
        session.flush()
        for c in v.citations:
            session.add(Citation(
                answer_id=ans.id, chunk_id=c["chunk_id"], doc_id=c["doc_id"], marker=c["marker"],
                quote=c["quote"], doc_title=c["doc_title"], doc_date=c["doc_date"], location=c["location"],
            ))
        audit.record(session, "answer", "drafted", rfp_id=rfp_id, entity_id=ans.id, actor="agent",
                     payload={"req_id": req_id, "status": v.status, "confidence": score, "flags": v.flags,
                              "model": breakdown["model"], "removed_sentences": len(v.removed)})
        session.commit()
        return ans.id
