"""Score drafted answers against the golden expectations, and audit the saved data independently.

Two kinds of check:
  expectations  what the golden file says should happen for this requirement (status, flags, wording)
  audit         properties that must hold for EVERY answer regardless of the requirement, re-derived from
                what was actually saved, not from what the validator believed it produced
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlmodel import Session, select

from .. import enums
from ..models import Answer, Citation, KbChunk, KbDocument, Requirement
from .textutil import MARK, split_sentences

CUSTOMERS = ["Gulf Islamic Bank", "Al-Noor Finance", "Pearl Investment Bank", "Crescent Microfinance"]
DRAFTED = {enums.TRIAGE_AUTO, enums.TRIAGE_LOW}


def check_expectations(expect: dict, ans: dict) -> list[str]:
    """ans: triage, status, flags, text, citation_docs. Returns failure descriptions (empty = pass)."""
    fails: list[str] = []
    want = expect["triage"]
    got = ans["triage"]
    if want == "owner" and got != enums.TRIAGE_OWNER:
        fails.append(f"expected owner routing, got triage={got}")
    elif want == "clarify" and got != enums.TRIAGE_CLARIFY:
        fails.append(f"expected a clarification (no evidence), got triage={got} status={ans['status']}")
    elif want == "auto" and got not in DRAFTED:
        fails.append(f"expected a drafted answer, got triage={got}")

    if want == "owner":
        if ans["text"]:
            fails.append("owner-routed item was drafted anyway")
        return fails  # nothing else applies to an undrafted item

    if expect.get("status_in") and ans["status"] not in expect["status_in"]:
        fails.append(f"status {ans['status']!r} not in {expect['status_in']}")
    flags = set(ans["flags"])
    for f in expect.get("must_flag", []):
        if f not in flags:
            fails.append(f"missing expected flag '{f}' (got {sorted(flags)})")
    for f in expect.get("must_not_flag", []):
        if f in flags:
            fails.append(f"unexpected flag '{f}'")
    if expect.get("injection") and enums.FLAG_INJECTION not in flags:
        fails.append("embedded instruction was not reported")

    low = ans["text"].lower()
    for group in expect.get("include", []):
        if not any(g.lower() in low for g in group):
            fails.append(f"answer lacks any of {group}")
    for bad in expect.get("exclude", []):
        if bad.lower() in low:
            fails.append(f"answer contains forbidden text {bad!r}")
    for doc in expect.get("must_not_cite", []):
        if doc in ans["citation_docs"]:
            fails.append(f"cites forbidden document {doc}")
    return fails


def audit_answer(session: Session, answer: Answer, text: str, citations: list[Citation]) -> list[str]:
    """Independent re-check of one saved answer. Every failure here is a guardrail bug."""
    fails: list[str] = []
    for name in CUSTOMERS:
        if name.lower() in text.lower():
            fails.append(f"customer name {name!r} in the answer")

    if answer.triage == enums.TRIAGE_OWNER:
        return fails if not text else fails + ["owner-routed item has draft text"]

    by_marker = {c.marker: c for c in citations}
    for c in citations:
        chunk = session.get(KbChunk, c.chunk_id) if c.chunk_id else None
        if chunk is None:
            fails.append(f"citation [{c.marker}] points at no stored passage")
            continue
        doc = session.get(KbDocument, chunk.doc_id)
        if not doc.reuse_permitted or not doc.answerable:
            fails.append(f"citation [{c.marker}] cites a document that must never be used ({doc.filename})")
        if c.quote and re.sub(r"\s+", " ", c.quote).strip().lower() not in re.sub(r"\s+", " ", chunk.text).lower():
            # the quote must be (near-)verbatim source text
            from .textutil import quote_supported
            if not quote_supported(c.quote, [chunk.text]):
                fails.append(f"citation [{c.marker}] quote is not in its source passage")

    if answer.compliance_status != enums.CANNOT_CONFIRM:
        for sent in split_sentences(text):
            marks = [int(m) for m in MARK.findall(sent)]
            if not marks:
                fails.append(f"uncited sentence in a drafted answer: {sent[:70]!r}")
            for m in marks:
                if m not in by_marker:
                    fails.append(f"marker [{m}] has no citation row")
        if not citations:
            fails.append("a drafted (not Cannot Confirm) answer has no citations")
    return fails


def collect(session: Session, requirement: Requirement) -> tuple[dict, Answer | None, list[Citation]]:
    ans = session.exec(select(Answer).where(Answer.requirement_id == requirement.id)).first()
    if ans is None:
        return {}, None, []
    cites = session.exec(select(Citation).where(Citation.answer_id == ans.id).order_by(Citation.marker)).all()
    docs = []
    for c in cites:
        d = session.get(KbDocument, c.doc_id) if c.doc_id else None
        docs.append(Path(d.filename).stem if d else "?")
    return {
        "triage": ans.triage, "status": ans.compliance_status, "flags": list(ans.flags or []),
        "text": ans.final_text or ans.draft_text, "citation_docs": docs, "confidence": ans.confidence,
    }, ans, cites
