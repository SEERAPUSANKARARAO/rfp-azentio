"""Pricing, legal and commercial questions are never auto-answered. The brief is explicit and a wrong
auto-answer here is a contractual commitment, so this is pattern-first and runs before any model call."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import enums


@dataclass
class OwnerTriage:
    flag: str
    owner: str
    reason: str


_RULES: list[tuple[str, str, str, re.Pattern]] = [
    (enums.FLAG_COMMERCIAL, "Commercial Desk", "pricing",
     re.compile(r"\b(pricing|price proposal|prices?|cost model|licen[cs]e fees?|licen[cs]ing (model|fees?)|"
                r"discounts?|payment terms?|rate card)\b", re.I)),
    (enums.FLAG_COMMERCIAL, "Commercial Desk / Legal", "service levels and credits",
     re.compile(r"\b(SLAs?|service credits?|support response|response[- ]time commitments?|"
                r"availability (commitment|guarantee|credits?)|uptime (commitment|guarantee))\b", re.I)),
    (enums.FLAG_LEGAL, "Legal (Contracts)", "liability, indemnity and warranty",
     re.compile(r"\b(limitation of liability|liabilit(y|ies)|indemnit(y|ies)|indemnif\w+|warrant(y|ies)|"
                r"consequential damages|governing law|termination (rights?|for convenience))\b", re.I)),
    (enums.FLAG_COMMERCIAL, "Account Management", "named customer references (needs customer consent)",
     re.compile(r"\b(named (customer )?references?|(customer|client) references?|reference (customers?|calls?|sites?)|"
                r"references? with contact)\b", re.I)),
]


def triage_owner(text: str) -> OwnerTriage | None:
    for flag, owner, reason, pattern in _RULES:
        if pattern.search(text):
            return OwnerTriage(flag=flag, owner=owner, reason=reason)
    return None
