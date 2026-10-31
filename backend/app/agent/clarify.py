"""The clarification list: questions the sources cannot answer, merged and worded for the customer.

The agent never fills a gap with an assumption. It lists the gap instead, once per distinct question,
with the reason, and links it to every requirement that raised it.
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from .. import enums
from ..llm.errors import LLMError
from ..llm.gateway import LLMGateway
from ..models import Answer, ClarificationQuery, Requirement
from ..rag.embeddings import get_embedder
from .textutil import content_stems


class Wording(BaseModel):
    question: str
    reason: str


SYSTEM = (
    "You write clarification questions that a vendor's Sales Engineer sends to a customer about their RFP. "
    "Given one or more related RFP requirements and what the vendor's own documentation could not confirm, "
    "write ONE polite, specific question to the customer and a one-sentence reason it is needed. "
    "Do not invent facts about the vendor's products. Do not promise capabilities. Reply with JSON only."
)


def _jaccard(a: set[str], b: set[str]) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def cluster(items: list[dict], embedder=None, threshold: float = 0.88) -> list[list[dict]]:
    """Group requirements that are really the same question (same missing standard, or near-identical text)."""
    vecs = embedder.embed_passages([i["text"] for i in items]) if embedder and items else None
    groups: list[list[int]] = []
    for idx, it in enumerate(items):
        placed = False
        for g in groups:
            ref = items[g[0]]
            same_anchor = bool(it["anchors"]) and it["anchors"] == ref["anchors"]
            if vecs is not None:
                close = float(vecs[idx] @ vecs[g[0]]) >= threshold
            else:
                close = _jaccard(content_stems(it["text"]), content_stems(ref["text"])) >= 0.7
            if same_anchor or close:
                g.append(idx)
                placed = True
                break
        if not placed:
            groups.append([idx])
    return [[items[i] for i in g] for g in groups]


def _fallback(group: list[dict]) -> Wording:
    first = group[0]
    missing = "; ".join(dict.fromkeys(m for g in group for m in g["missing"]))[:300]
    return Wording(
        question=f"Could you clarify the requirement in {first['req_id']}: {first['text'][:220]}",
        reason=("Our documentation does not confirm this" + (f" ({missing})" if missing else "")
                + ", so we would rather ask than assume."),
    )


async def build_clarifications(engine: Engine, gateway: LLMGateway | None, rfp_id: int) -> int:
    """Rebuild the open clarification queries for an RFP. Returns how many were created."""
    with Session(engine) as session:
        rows = session.exec(
            select(Answer, Requirement).where(Answer.requirement_id == Requirement.id, Requirement.rfp_id == rfp_id)
            .order_by(Requirement.order)).all()
        items = []
        for ans, req in rows:
            gap = ans.triage == enums.TRIAGE_CLARIFY or (
                enums.FLAG_MISSING in (ans.flags or []) and ans.compliance_status == enums.PARTIAL)
            if not gap:
                continue
            items.append({
                "req_id": req.req_id, "req_pk": req.id, "text": req.text,
                "missing": (ans.flag_details or {}).get("missing_info", []),
                "anchors": tuple((ans.flag_details or {}).get("missing_anchors", [])),
            })

    try:
        embedder = get_embedder()
    except Exception:  # noqa: BLE001  clustering falls back to word overlap
        embedder = None
    groups = cluster(items, embedder)

    async def word(group: list[dict]) -> Wording:
        if gateway is None:
            return _fallback(group)
        lines = "\n".join(f"- {g['req_id']}: {g['text']}" for g in group)
        miss = "; ".join(dict.fromkeys(m for g in group for m in g["missing"])) or "not stated in the documentation"
        try:
            r = await gateway.complete(
                "fast", f"RFP requirements:\n{lines}\n\nWhat our documentation could not confirm: {miss}\n\n"
                        "JSON fields: question, reason.", system=SYSTEM, schema=Wording, max_tokens=500)
            return r.data
        except LLMError:
            return _fallback(group)  # quota or provider trouble must not lose the gap itself

    wordings = [await word(g) for g in groups]

    with Session(engine) as session:
        for old in session.exec(select(ClarificationQuery).where(
                ClarificationQuery.rfp_id == rfp_id, ClarificationQuery.status == "open")).all():
            session.delete(old)
        for g, w in zip(groups, wordings):
            session.add(ClarificationQuery(
                rfp_id=rfp_id, text=re.sub(r"\s+", " ", w.question).strip(),
                reason=re.sub(r"\s+", " ", w.reason).strip(), requirement_ids=[x["req_id"] for x in g]))
        session.commit()
    return len(groups)
