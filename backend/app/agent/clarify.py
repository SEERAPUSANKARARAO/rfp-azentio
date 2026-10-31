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
from .priority import impact_of, priority_of
from .textutil import content_stems


# Who normally owns a question, by what it could change. A starting point the SE can change.
ROLE_BY_IMPACT = {enums.IMPACT_COMMERCIAL: "Commercial Desk", enums.IMPACT_DESIGN: "Solution Architecture",
                  enums.IMPACT_DELIVERY: "Account Management", enums.IMPACT_COMPLIANCE: "SME"}


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


def merge_linked(groups: list[list[dict]]) -> list[list[dict]]:
    """Two requirements that contradict each other raise ONE question, not one each."""
    merged: list[list[dict]] = []
    for g in groups:
        ids = {x["req_id"] for x in g}
        links = set().union(*(x["links"] for x in g))
        for m in merged:
            if (links & {x["req_id"] for x in m}) or (set().union(*(x["links"] for x in m)) & ids):
                m.extend(g)
                break
        else:
            merged.append(list(g))
    return merged


def _fallback(group: list[dict]) -> Wording:
    first = group[0]
    unclear = "; ".join(dict.fromkeys(m for g in group for m in g["unclear"]))[:300]
    if unclear and not any(g["missing"] for g in group):
        return Wording(question=f"Could you clarify requirement {first['req_id']}? {unclear}",
                       reason="The requirement can be read in more than one way, and the answer, scope or price depends on which.")
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
            unclear = [f for f in (req.interpretation or {}).get("findings", []) if f["kind"] in
                       ("ambiguous", "incomplete", "contradictory")]
            gap = ans.triage == enums.TRIAGE_CLARIFY or (
                enums.FLAG_MISSING in (ans.flags or []) and ans.compliance_status == enums.PARTIAL)
            if not gap and not unclear:
                continue
            kinds = {f["kind"] for f in unclear}
            items.append({
                "req_id": req.req_id, "req_pk": req.id, "text": req.text, "mandatory": req.mandatory is not False,
                "missing": (ans.flag_details or {}).get("missing_info", []) if gap else [],
                "unclear": [f["detail"] for f in unclear],
                "source": ("contradiction" if "contradictory" in kinds else "ambiguity" if kinds else "missing_evidence"),
                "links": {w for f in unclear for w in f.get("with", [])},  # the requirements this one clashes with
                "anchors": tuple((ans.flag_details or {}).get("missing_anchors", [])) if gap else (),
            })

    try:
        embedder = get_embedder()
    except Exception:  # noqa: BLE001  clustering falls back to word overlap
        embedder = None
    groups = merge_linked(cluster(items, embedder))

    async def word(group: list[dict]) -> Wording:
        if gateway is None:
            return _fallback(group)
        lines = "\n".join(f"- {g['req_id']}: {g['text']}" for g in group)
        miss = "; ".join(dict.fromkeys(m for g in group for m in g["missing"]))
        unclear = "; ".join(dict.fromkeys(m for g in group for m in g["unclear"]))
        asked = ((f"What our documentation could not confirm: {miss}\n" if miss else "")
                 + (f"What is unclear in the requirement itself: {unclear}\n" if unclear else "")) \
            or "What our documentation could not confirm: not stated in the documentation\n"
        try:
            r = await gateway.complete(
                "fast", f"RFP requirements:\n{lines}\n\n{asked}\nJSON fields: question, reason.", system=SYSTEM,
                schema=Wording, max_tokens=500)
            return r.data
        except LLMError:
            return _fallback(group)  # quota or provider trouble must not lose the gap itself

    wordings = [await word(g) for g in groups]

    with Session(engine) as session:
        for old in session.exec(select(ClarificationQuery).where(
                ClarificationQuery.rfp_id == rfp_id, ClarificationQuery.status == "open")).all():
            session.delete(old)
        for g, w in zip(groups, wordings):
            impact = impact_of(*(x["text"] for x in g), *(m for x in g for m in x["missing"]),
                               *(m for x in g for m in x["unclear"]))
            source = ("contradiction" if any(x["source"] == "contradiction" for x in g)
                      else "ambiguity" if any(x["source"] == "ambiguity" for x in g) else "missing_evidence")
            priority, _ = priority_of(mandatory=any(x["mandatory"] for x in g), n_requirements=len(g), impact=impact,
                                      source=source)
            session.add(ClarificationQuery(
                rfp_id=rfp_id, text=re.sub(r"\s+", " ", w.question).strip(),
                reason=re.sub(r"\s+", " ", w.reason).strip(), requirement_ids=[x["req_id"] for x in g],
                priority=priority, impact=impact, source=source, assigned_to=ROLE_BY_IMPACT.get(impact)))
        session.commit()
    return len(groups)
