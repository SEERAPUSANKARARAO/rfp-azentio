"""Evidence anchors: certifications, standards and regulations a requirement names.

If a requirement asks about "ISO 22301" and no retrieved passage mentions 22301, the answer cannot be
"Complied" however fluent the draft is. This check does not rely on the model noticing the gap."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Anchor:
    label: str
    present: re.Pattern  # what to look for in the evidence text


_FINDERS: list[tuple[re.Pattern, callable]] = [
    (re.compile(r"\bISO(?:/IEC)?\s*(\d{4,5})(?::\d{4})?", re.I),
     lambda m: Anchor(f"ISO {m.group(1)}", re.compile(rf"\b{m.group(1)}\b"))),
    (re.compile(r"\bPCI[\s-]?DSS\b", re.I), lambda m: Anchor("PCI DSS", re.compile(r"\bpci\b", re.I))),
    (re.compile(r"\bSOC\s?([123])\b", re.I),
     lambda m: Anchor(f"SOC {m.group(1)}", re.compile(rf"\bsoc\s?{m.group(1)}\b", re.I))),
    (re.compile(r"\bGDPR\b", re.I), lambda m: Anchor("GDPR", re.compile(r"\bgdpr\b", re.I))),
    (re.compile(r"\bDPDP\b|Digital Personal Data Protection", re.I),
     lambda m: Anchor("DPDP", re.compile(r"\bdpdp\b|digital personal data protection", re.I))),
    (re.compile(r"\bHIPAA\b", re.I), lambda m: Anchor("HIPAA", re.compile(r"\bhipaa\b", re.I))),
    (re.compile(r"\bNIST\b"), lambda m: Anchor("NIST", re.compile(r"\bnist\b", re.I))),
    (re.compile(r"\bFIPS\s?\d*", re.I), lambda m: Anchor("FIPS", re.compile(r"\bfips\b", re.I))),
    (re.compile(r"\bCSA STAR\b", re.I), lambda m: Anchor("CSA STAR", re.compile(r"csa star", re.I))),
    (re.compile(r"Decree[- ]Law No\.?\s*(\d+)\s*of\s*(\d{4})", re.I),
     lambda m: Anchor(f"Decree-Law {m.group(1)} of {m.group(2)}",
                      re.compile(rf"\b{m.group(1)}\s+of\s+{m.group(2)}\b", re.I))),
]


def anchors(text: str) -> list[Anchor]:
    found: dict[str, Anchor] = {}
    for pattern, build in _FINDERS:
        for m in pattern.finditer(text):
            a = build(m)
            found.setdefault(a.label, a)
    return list(found.values())


def missing_anchors(requirement_text: str, evidence_texts: list[str]) -> list[str]:
    """Labels of named certifications/standards/regulations that no evidence text mentions."""
    blob = "\n".join(evidence_texts)
    return [a.label for a in anchors(requirement_text) if not a.present.search(blob)]
