"""The chat workspace: a conversation about one RFP.

Two kinds of message, handled very differently on purpose:

  Commands  ("summarise", "why was 3.2 flagged", "rewrite 3.2 shorter", "accept 1.1", "fact for 4.4: ...")
            are matched by explicit patterns and need a requirement the SE named (or has selected). Nothing is
            ever changed because a model guessed an intent: human control, as the brief requires.
  Questions (anything else) are answered from the approved sources only, through the same prompt and the same
            validator as RFP answers, so a chat answer is cited, checked and never invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlmodel import Session, select

from .. import enums
from ..config import get_settings
from ..llm.errors import AllProvidersExhausted, LLMError
from ..llm.gateway import LLMGateway
from ..models import Answer, ChatMessage, ClarificationQuery, Requirement, Rfp
from ..rag.conflicts import conflicts_by_chunk
from ..rag.embeddings import get_embedder
from ..rag.retrieve import retrieve
from ..services import facts, review
from ..services.answers import answer_outs
from .pipeline import draft_requirement
from .prompts import SYSTEM, build_prompt
from .schemas import DraftOut
from .validate import sanitize_requirement, validate_draft

HELP = (
    "I can work through this RFP with you. Try:\n"
    "- \"summarise this RFP\", \"what is the progress?\", \"when is it due?\"\n"
    "- \"what needs review?\" or \"show the clarification questions\"\n"
    "- \"why was 3.2 flagged?\" (or just \"why?\" with a question selected)\n"
    "- \"rewrite 3.2 shorter\", \"make 1.4 more formal\"\n"
    "- \"fact for 4.4: our scorecard module is rules based\"\n"
    "- \"accept 1.1\"\n"
    "- or ask anything about what the approved sources say, for example \"do we support Wakala deposits?\"\n"
    "Anything that changes an answer needs a requirement number, or one selected in the queue."
)


@dataclass
class Reply:
    text: str
    intent: str
    refs: list[str] = field(default_factory=list)  # requirement IDs the UI can turn into links
    sources: list[dict] = field(default_factory=list)
    select: int | None = None  # requirement_id for the UI to open


_ID = re.compile(r"(?<![\w.])([A-Za-z]{1,5}-?\d+(?:\.\d+)*|\d+(?:\.\d+)+)(?![\w])")


def _find_req(message: str, reqs: dict[str, Requirement]) -> Requirement | None:
    upper = {k.upper(): v for k, v in reqs.items()}
    for m in sorted(_ID.finditer(message), key=lambda m: -len(m.group(1))):
        if m.group(1).upper() in upper:
            return upper[m.group(1).upper()]
    return None


def _has_keys(gateway: LLMGateway) -> bool:
    return any(p.has_keys() for p in gateway.pools.values())


def _overview(session: Session, rfp: Rfp, reqs: list[Requirement]) -> str:
    rep = rfp.parse_report or {}
    rules = rfp.format_rules or {}
    sm = review.summary(session, rfp.id)
    lines = [f"{rfp.name} ({rfp.filename})",
             f"{rep.get('requirements', len(reqs))} requirements: {rep.get('mandatory', 0)} mandatory, "
             f"{rep.get('optional', 0)} optional" + (f", {rep['unknown_priority']} unclear" if rep.get("unknown_priority") else "") + ".",
             "Sections: " + "; ".join(f"{s['code'] + '. ' if s['code'] else ''}{s['title']} ({s['count']})" for s in rep.get("sections", [])) + "."]
    bits = []
    if rules.get("word_limit"):
        bits.append(f"{rules['word_limit']} words per answer")
    if rules.get("compliance_codes"):
        bits.append("compliance wording " + " / ".join(rules["compliance_codes"]))
    if rules.get("file_name"):
        bits.append(f"file name {rules['file_name']}")
    if bits:
        lines.append("Format rules: " + "; ".join(bits) + ".")
    if rules.get("clarification_deadline") or rules.get("deadline"):
        lines.append(f"Questions due {rules.get('clarification_deadline') or 'not stated'}; submission due {rules.get('deadline') or 'not stated'}.")
    lines.append(f"Progress: {sm['drafted']}/{sm['total']} drafted, {sm['accepted']} approved, {sm['needs_review']} need review, "
                 f"{sm['clarify']} to clarify, {sm['owner']} with owners.")
    return "\n".join(lines)


def _list(answers, pred, title: str, limit: int = 12) -> tuple[str, list[str]]:
    hits = [a for a in answers if pred(a)]
    if not hits:
        return f"{title}: none.", []
    ids = [a.req_id for a in hits]
    shown = ", ".join(ids[:limit]) + (f" and {len(ids) - limit} more" if len(ids) > limit else "")
    return f"{title} ({len(ids)}): {shown}.", ids[:limit]


def _explain(a) -> str:
    if a.review_status == enums.REVIEW_QUEUED:
        return f"{a.req_id} has not been drafted yet."
    out = [f"{a.req_id}: {a.status or 'no status'}" + (f", {round((a.confidence or 0) * 100)}% confidence" if a.confidence is not None else "") + "."]
    if a.triage == enums.TRIAGE_OWNER:
        out.append(f"This is a {a.flag_details.get('reason', 'commercial or legal')} question, so it was never drafted. It is routed to {a.assigned_to}.")
    if a.reasoning and a.triage != enums.TRIAGE_OWNER:
        out.append(f"Why: {a.reasoning}")
    d = a.flag_details or {}
    for f in a.flags:
        if f == "conflicting":
            for c in (d.get("conflicts") or {}).get("indexed", []):
                out.append(f"Conflict on {c.get('topic')}: {c.get('detail')}")
        elif f == "stale":
            out.append("Stale source: " + "; ".join(f"{s['doc']} ({'; '.join(s['reasons'])})" for s in d.get("stale", [])))
        elif f == "missing":
            anchors = d.get("missing_anchors") or []
            miss = anchors + [m for m in (d.get("missing_info") or []) if not (anchors and m.startswith("No source mentions "))]
            out.append("Evidence missing: " + ("; ".join(miss) if miss else "the sources do not address this."))
        elif f == "unsupported":
            out.append(f"{len(d.get('removed_sentences', []))} statement(s) were removed because their cited sources did not support them.")
        elif f == "injection":
            out.append("The requirement contained an instruction aimed at an AI. It was removed before drafting and ignored.")
        elif f == "se_provided":
            out.append("Part of this rests on a fact an engineer supplied, not on approved documentation.")
    if a.citations:
        out.append("Sources: " + "; ".join(f"[{c.marker}] {c.doc_title}, {c.location}" for c in a.citations))
    return "\n".join(out)


def _clean_instruction(message: str, req_id: str) -> str:
    t = re.sub(re.escape(req_id), "", message, flags=re.I)
    t = re.sub(r"\b(please|can you|could you|rewrite|redraft|regenerate|re-draft|answer|question|for|the|it|make)\b", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ,.:;-")
    return t or "reword"


async def handle(
    session: Session, engine: Engine, gateway: LLMGateway, rfp_id: int, message: str,
    focus_requirement_id: int | None = None, reviewer: str = "se",
) -> Reply:
    rfp = session.get(Rfp, rfp_id)
    reqs = session.exec(select(Requirement).where(Requirement.rfp_id == rfp_id).order_by(Requirement.order)).all()
    by_id = {r.req_id: r for r in reqs}
    msg = message.strip()
    low = msg.lower()
    named = _find_req(msg, by_id)
    focus = named or (session.get(Requirement, focus_requirement_id) if focus_requirement_id else None)
    if focus is not None and focus.rfp_id != rfp_id:
        focus = None
    answers = answer_outs(session, rfp_id)
    by_ans = {a.req_id: a for a in answers}

    if re.search(r"\b(help|what can you do|commands)\b", low):
        return Reply(HELP, "help")

    # ---- explicit commands that change something: need a named (or selected) requirement
    m = re.match(r"^\s*(?:please\s+)?(?:add\s+(?:a\s+)?)?fact(?:\s+for\s+([\w.\-]+))?\s*[:\-]\s*(.+)$", msg, re.I | re.S)
    if m:
        target = by_id.get(m.group(1)) if m.group(1) else focus
        if target is None:
            return Reply("Which requirement is this fact for? Say \"fact for 3.4: ...\" or select the question first.", "need_requirement")
        try:
            facts.add_se_fact(session, target, m.group(2), reviewer, get_embedder())
        except ValueError as e:
            return Reply(str(e), "fact_rejected")
        if not _has_keys(gateway):
            return Reply(f"Saved your fact for {target.req_id} as an SE-provided source. I cannot redraft right now (no model keys).", "fact", [target.req_id], select=target.id)
        try:
            await draft_requirement(engine, gateway, target.id, conflicts=conflicts_by_chunk(session), use_cache=False)
        except (AllProvidersExhausted, LLMError) as e:
            return Reply(f"Saved your fact for {target.req_id}, but the redraft failed ({type(e).__name__}). Try Regenerate in a moment.", "fact", [target.req_id], select=target.id)
        session.expire_all()
        a = answer_outs(session, rfp_id, target.id)[0]
        return Reply(f"Saved as an SE-provided fact (in {reviewer}'s name) and redrafted {target.req_id}:\n{a.text or '(no answer)'}\nIt is marked as resting on an engineer's fact, so check it before approving.",
                     "fact", [target.req_id], select=target.id)

    if re.match(r"^\s*(please\s+)?accept\b", low):
        if focus is None:
            return Reply("Which one should I accept? Say \"accept 1.1\" or select a question first.", "need_requirement")
        try:
            review.accept(session, rfp_id, focus.id, reviewer)
        except review.ReviewError as e:
            return Reply(f"I could not accept {focus.req_id}: {e}", "accept_failed", [focus.req_id], select=focus.id)
        return Reply(f"Accepted {focus.req_id} in {reviewer}'s name. Editing it later will withdraw the approval.", "accept", [focus.req_id], select=focus.id)

    if re.search(r"\b(rewrite|redraft|regenerate|re-draft|shorten|shorter|more formal|simplify|plain language|reword)\b", low):
        if focus is None:
            return Reply("Which answer should I rewrite? Say \"rewrite 3.2 shorter\" or select a question first.", "need_requirement")
        if not _has_keys(gateway):
            return Reply("I cannot redraft: no model keys are configured.", "rewrite_unavailable", [focus.req_id])
        instruction = _clean_instruction(msg, focus.req_id)
        try:
            await draft_requirement(engine, gateway, focus.id, conflicts=conflicts_by_chunk(session), instruction=instruction, use_cache=False)
        except AllProvidersExhausted:
            return Reply("The models are rate-limited right now. Try again in a minute.", "rewrite_unavailable", [focus.req_id])
        except LLMError as e:
            return Reply(f"The model could not produce a valid draft ({type(e).__name__}).", "rewrite_failed", [focus.req_id])
        session.expire_all()
        a = answer_outs(session, rfp_id, focus.id)[0]
        return Reply(f"Redrafted {focus.req_id} (\"{instruction}\"). Any earlier approval was withdrawn.\n{a.text or '(no answer)'}", "rewrite", [focus.req_id], select=focus.id)

    # ---- read-only commands
    if re.search(r"\b(summar\w*|overview|analy[sz]\w*|what'?s in this|tell me about this)\b", low):
        return Reply(_overview(session, rfp, reqs), "summary")
    if re.search(r"\b(deadline|due date|when is it due|when do we submit|submission date)\b", low):
        r = rfp.format_rules or {}
        return Reply(f"Clarification questions by {r.get('clarification_deadline') or 'not stated'}. Submission due {r.get('deadline') or 'not stated'}.", "deadline")
    if re.search(r"\b(progress|status|how many|how far|where are we)\b", low) and not named:
        s = review.summary(session, rfp_id)
        return Reply(f"{s['drafted']} of {s['total']} drafted, {s['accepted']} approved, {s['needs_review']} need review, "
                     f"{s['clarify']} to clarify, {s['owner']} with owners, {s['unanswered']} not drafted yet.", "progress")
    if re.search(r"\bclarif", low):
        qs = session.exec(select(ClarificationQuery).where(ClarificationQuery.rfp_id == rfp_id, ClarificationQuery.status != "dismissed")).all()
        if not qs:
            return Reply("No clarification questions yet.", "clarifications")
        return Reply("Clarification questions:\n" + "\n".join(f"- ({', '.join(q.requirement_ids)}) {q.text}" for q in qs), "clarifications",
                     [i for q in qs for i in q.requirement_ids])
    if re.search(r"\b(needs?|need) (review|attention)|\bflagged\b|\bstale\b|\bconflict|\bmissing\b|\bcommercial\b|\blegal\b|\bowners?\b", low) and not named:
        if "stale" in low:
            text, ids = _list(answers, lambda a: "stale" in a.flags, "Answers relying on stale sources")
        elif "conflict" in low:
            text, ids = _list(answers, lambda a: "conflicting" in a.flags, "Answers touched by conflicting sources")
        elif "missing" in low:
            text, ids = _list(answers, lambda a: "missing" in a.flags, "Answers with missing evidence")
        elif re.search(r"commercial|legal|owner", low):
            text, ids = _list(answers, lambda a: a.triage == "owner", "Questions routed to an owner")
        elif "flagged" in low:
            text, ids = _list(answers, lambda a: bool(a.assigned_to), "Questions waiting on someone else")
        else:
            text, ids = _list(answers, lambda a: a.review_status == "drafted" and a.needs_review, "Answers that need your review")
        return Reply(text, "list", ids)
    if re.match(r"^\s*(why|explain|how come|what happened)\b", low) or re.search(r"\bwhy (was|is|did)\b", low):
        if focus is None or focus.req_id not in by_ans:
            return Reply("Which question? Say \"why was 3.2 flagged?\" or select it first.", "need_requirement")
        return Reply(_explain(by_ans[focus.req_id]), "explain", [focus.req_id], select=focus.id)
    if named and re.match(r"^\s*(show|open|go to|select|find)\b", low):
        return Reply(f"Opening {named.req_id}.", "open", [named.req_id], select=named.id)

    # ---- everything else is a question: answer it from the approved sources, checked like any other answer
    if not _has_keys(gateway):
        return Reply("I answer questions from the approved sources, but no model keys are configured. I can still run commands: type \"help\".", "ask_unavailable")
    clean, injected = sanitize_requirement(msg)
    passages = retrieve(session, clean, k=get_settings().retrieval_k)
    try:
        res = await gateway.complete(
            "draft",
            build_prompt(req_id="Q", requirement=clean, mandatory=None, section_title="A question from the Sales Engineer, not an RFP requirement",
                         passages=passages, rfp_name=rfp.name),
            system=SYSTEM.format(word_limit=120), schema=DraftOut, max_tokens=get_settings().draft_max_tokens)
    except AllProvidersExhausted:
        return Reply("The models are rate-limited right now. Try again in a minute.", "ask_unavailable")
    except LLMError as e:
        return Reply(f"I could not get a usable answer ({type(e).__name__}).", "ask_failed")
    v = validate_draft(res.data, passages, clean, conflicts_by_chunk=conflicts_by_chunk(session), injection_removed=injected)
    if v.status == enums.CANNOT_CONFIRM and not v.citations:
        return Reply("I could not find that in the approved sources, so I will not guess. "
                     + ("Not evidenced: " + "; ".join(v.missing_info[:3]) + "." if v.missing_info else ""), "ask", sources=[])
    notes = []
    if enums.FLAG_STALE in v.flags:
        notes.append("Note: this rests on an older source.")
    if enums.FLAG_CONFLICTING in v.flags:
        notes.append("Note: sources disagree on this; an SME should settle it.")
    return Reply("\n".join([v.answer, *notes]), "ask",
                 sources=[{"marker": c["marker"], "doc_title": c["doc_title"], "location": c["location"],
                           "doc_date": c["doc_date"].isoformat() if c["doc_date"] else None, "quote": c["quote"]} for c in v.citations])


def save(session: Session, rfp_id: int, role: str, content: str, meta: dict | None = None) -> ChatMessage:
    m = ChatMessage(rfp_id=rfp_id, role=role, content=content, meta=meta or {})
    session.add(m)
    session.commit()
    session.refresh(m)
    return m
