"""Find knowledge-base passages that contradict each other, once, at index time.

Two passages that give different values for the same thing (RTO 4 hours vs 1 hour) or disagree on
whether something is current (a certificate "valid" vs "expired") are the cases that embarrass a bid.
Detecting them up front means drafting only does a lookup, and the SME sees them on the KB dashboard
before any RFP depends on them.

Candidate pairs come from cheap similarity + a shared kind of fact; a model then judges each pair once.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
from pydantic import BaseModel
from sqlalchemy import text as sql
from sqlmodel import Session, select

from .. import enums
from ..agent.textutil import content_stems, number_units, standards
from ..llm.errors import LLMError
from ..llm.gateway import LLMGateway
from ..models import Conflict
from .embeddings import Embedder

_KEY_TERMS = ("rto", "rpo", "certif", "availability", "uptime", "recovery", "residency", "retention")
SIM_EMBED = 0.70
SIM_JACCARD = 0.18


class Verdict(BaseModel):
    contradiction: bool
    topic: str = ""
    summary: str = ""
    newer: Literal["A", "B", "same", "unclear"] = "unclear"


SYSTEM = (
    "You compare two passages from a vendor's knowledge base. Decide whether they make CONTRADICTORY "
    "factual claims about the same thing: different values for the same measure (for example an RTO of "
    "4 hours versus 1 hour), or one saying something is current or valid while the other says it has "
    "lapsed or ended. Passages that agree, add detail, or describe different things are NOT contradictions. "
    "A difference caused only by the passages being written at different times counts as a contradiction "
    "if both are stated as fact. Different customers, projects or engagements naturally have different "
    "figures (team sizes, durations, timelines): that is NOT a contradiction. Only call it a contradiction "
    "when both passages describe the SAME subject, such as the same product capability, policy, service "
    "level or certificate. A passage that is merely less specific (it omits a version, a number or a detail "
    "the other gives) is NOT a contradiction. Reply with JSON only."
)


@dataclass
class _Chunk:
    id: int
    doc_id: int
    title: str
    doc_date: date | None
    text: str
    emb: np.ndarray | None
    kinds: set[str]
    stems: set[str]


def fact_kinds(text: str) -> set[str]:
    low = text.lower()
    kinds = {unit for _, unit in number_units(text)} | standards(text)
    kinds |= {k for k in _KEY_TERMS if k in low}
    return kinds


def _load(session: Session) -> list[_Chunk]:
    rows = session.exec(  # type: ignore[call-overload]
        sql("SELECT c.id AS id, c.doc_id AS did, c.text AS text, c.embedding AS e, d.display_title AS title, "
            "d.doc_date AS dd FROM kbchunk c JOIN kbdocument d ON d.id = c.doc_id "
            "WHERE d.reuse_permitted = 1 AND d.answerable = 1 AND NOT EXISTS (SELECT 1 FROM conflict x "
            "WHERE x.status = 'resolved' AND x.canonical_doc_id IS NOT NULL AND x.canonical_doc_id != c.doc_id "
            "AND (x.chunk_a_id = c.id OR x.chunk_b_id = c.id))")
    ).all()
    out = []
    for r in rows:
        dd = date.fromisoformat(r.dd) if isinstance(r.dd, str) else r.dd
        out.append(_Chunk(
            r.id, r.did, r.title, dd, r.text,
            np.frombuffer(r.e, dtype=np.float32) if r.e else None,
            fact_kinds(r.title + "\n" + r.text), content_stems(r.text),
        ))
    return out


def candidate_pairs(chunks: list[_Chunk], max_pairs: int) -> list[tuple[_Chunk, _Chunk, float]]:
    scored = []
    for i, a in enumerate(chunks):
        for b in chunks[i + 1:]:
            if a.doc_id == b.doc_id or not (a.kinds & b.kinds):
                continue
            if a.emb is not None and b.emb is not None:
                sim = float(a.emb @ b.emb)
                ok = sim >= SIM_EMBED
            else:
                union = a.stems | b.stems
                sim = len(a.stems & b.stems) / len(union) if union else 0.0
                ok = sim >= SIM_JACCARD
            if ok:
                scored.append((a, b, sim))
    scored.sort(key=lambda t: -t[2])
    return scored[:max_pairs]


def _prompt(a: _Chunk, b: _Chunk) -> str:
    def block(tag: str, c: _Chunk) -> str:
        return f"PASSAGE {tag} ({c.title}, dated {c.doc_date}):\n<passage>\n{c.text[:900]}\n</passage>"
    return (block("A", a) + "\n\n" + block("B", b) +
            '\n\nJSON fields: contradiction (true/false), topic (short), summary (one sentence naming the two '
            'differing claims), newer ("A", "B", "same" or "unclear": which passage reflects the more recent position).')


async def detect_conflicts(
    session: Session, gateway: LLMGateway, embedder: Embedder | None = None, *,
    max_pairs: int = 40, concurrency: int = 3,
) -> dict:
    """Rebuild the open conflicts. Resolved ones are kept. Returns counts for the caller to show."""
    chunks = _load(session)
    pairs = candidate_pairs(chunks, max_pairs)
    sem = asyncio.Semaphore(concurrency)

    async def judge(a: _Chunk, b: _Chunk):
        async with sem:
            try:
                r = await gateway.complete("fast", _prompt(a, b), system=SYSTEM, schema=Verdict, max_tokens=700)
                return r.data
            except LLMError:
                return None

    verdicts = await asyncio.gather(*(judge(a, b) for a, b, _ in pairs))

    for old in session.exec(select(Conflict).where(Conflict.status == enums.CONFLICT_OPEN)).all():
        session.delete(old)
    found = failed = 0
    for (a, b, _), v in zip(pairs, verdicts):
        if v is None:
            failed += 1
            continue
        if not v.contradiction:
            continue
        # a = the older passage, b = the newer, so the dashboard reads "stale vs current"
        older, newer = (a, b)
        if (a.doc_date or date.min) > (b.doc_date or date.min) or (v.newer == "A" and a.doc_date == b.doc_date):
            older, newer = b, a
        session.add(Conflict(
            kind=enums.CONFLICT_CONTRADICTION, topic=v.topic or "unspecified", chunk_a_id=older.id,
            chunk_b_id=newer.id, doc_a_id=older.doc_id, doc_b_id=newer.doc_id, detail=v.summary,
        ))
        found += 1
    session.commit()
    return {"candidate_pairs": len(pairs), "contradictions": found, "judge_failures": failed}


def conflicts_by_chunk(session: Session) -> dict[int, list[dict]]:
    """chunk id -> the open conflicts it takes part in, for the validator's lookup."""
    out: dict[int, list[dict]] = {}
    for c in session.exec(select(Conflict).where(Conflict.status == enums.CONFLICT_OPEN)).all():
        info = {"id": c.id, "topic": c.topic, "status": c.status, "detail": c.detail, "kind": c.kind,
                "older_doc_id": c.doc_a_id, "newer_doc_id": c.doc_b_id}
        for cid in (c.chunk_a_id, c.chunk_b_id):
            if cid is not None:
                out.setdefault(cid, []).append(info)
    return out
