"""How much could the customer's answer change our solution, delivery or price? Decided by rule, so a reviewer can
see why a question ranks where it does. The model only words the question; it never sets the priority."""

from __future__ import annotations

import re

from .. import enums

# Checked in this order: a question that touches price is the most consequential, then design, delivery, compliance.
_IMPACT = [
    (enums.IMPACT_COMMERCIAL, re.compile(
        r"pric|\bcost|licen[cs]e|volume|\busers?\b|\bseats?\b|transactions?|per (?:month|year|day)|sizing|quantit|"
        r"penalt|warrant|contract (?:term|length)|term of|how many", re.I)),
    (enums.IMPACT_DESIGN, re.compile(
        r"integrat|interface|\bAPI\b|architect|data model|migrat|on[\s-]?prem|\bcloud\b|hosting|performance|"
        r"throughput|latency|availability|disaster|security|encrypt|authentic|single sign|resilien|scalab|"
        r"third[\s-]?party|core banking", re.I)),
    (enums.IMPACT_DELIVERY, re.compile(
        r"timeline|go[\s-]?live|phase|training|support hours|resources?|handover|implementation|roll[\s-]?out|"
        r"deadline|schedule|milestone|parallel run", re.I)),
    (enums.IMPACT_COMPLIANCE, re.compile(
        r"regulat|shariah|aaoifi|ifsb|\baudit|certificat|central bank|compliance|data residency|privacy|\bISO\b", re.I)),
]


def impact_of(*texts: str) -> str:
    blob = " ".join(t for t in texts if t)
    for impact, pattern in _IMPACT:
        if pattern.search(blob):
            return impact
    return ""


def priority_of(*, mandatory: bool, n_requirements: int, impact: str, source: str) -> tuple[str, int]:
    """(priority, score). Mandatory matters most; a question that settles several requirements at once matters more;
    price and design impact matter more than delivery or compliance wording; a requirement that is ambiguous or
    contradicts another is worth settling early."""
    score = (3 if mandatory else 1) + min(max(n_requirements - 1, 0), 2)
    score += 1 if impact in (enums.IMPACT_COMMERCIAL, enums.IMPACT_DESIGN) else 0
    score += 1 if source in ("ambiguity", "contradiction") else 0
    level = enums.PRIORITY_HIGH if score >= 5 else enums.PRIORITY_MEDIUM if score >= 3 else enums.PRIORITY_LOW
    return level, score


RANK = {enums.PRIORITY_HIGH: 0, enums.PRIORITY_MEDIUM: 1, enums.PRIORITY_LOW: 2}
