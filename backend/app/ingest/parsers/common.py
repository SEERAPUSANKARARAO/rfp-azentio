"""Shared rules for reading requirements out of any RFP format.

Requirement IDs, section membership and order come from the document, never from a model:
a model may help classify a requirement later, but it never invents, renumbers or drops one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class ParseError(Exception):
    """The file cannot be read as an RFP (corrupt, scanned, or no requirements found)."""


# ---------------------------------------------------------------- data
@dataclass
class ParsedRequirement:
    req_id: str
    text: str
    order: int
    section_code: str
    section_title: str
    mandatory: bool | None
    kind: str = "requirement"
    source_ref: dict = field(default_factory=dict)
    parse_confidence: float = 1.0
    notes: list[str] = field(default_factory=list)


@dataclass
class ParsedRfp:
    file_type: str
    title: str = ""
    requirements: list[ParsedRequirement] = field(default_factory=list)
    instruction_text: str = ""
    warnings: list[str] = field(default_factory=list)

    def sections(self) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        for r in self.requirements:
            key = (r.section_code, r.section_title)
            if key not in seen:
                seen.append(key)
        return seen

    def report(self) -> dict:
        per: dict[tuple[str, str], int] = {}
        for r in self.requirements:
            per[(r.section_code, r.section_title)] = per.get((r.section_code, r.section_title), 0) + 1
        return {
            "requirements": len(self.requirements),
            "sections": [{"code": c, "title": t, "count": n} for (c, t), n in per.items()],
            "mandatory": sum(1 for r in self.requirements if r.mandatory is True),
            "optional": sum(1 for r in self.requirements if r.mandatory is False),
            "unknown_priority": sum(1 for r in self.requirements if r.mandatory is None),
            "low_confidence": [r.req_id for r in self.requirements if r.parse_confidence < 0.9],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------- text
def clean(text: object) -> str:
    """Collapse whitespace, including the newlines PDF and DOCX cells wrap with."""
    return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


_FOOTER = re.compile(r"^(page\s+\d+(\s+of\s+\d+)?|\d+\s*/\s*\d+)$", re.I)


def is_footer(line: str) -> bool:
    return bool(_FOOTER.match(line.strip()))


# ---------------------------------------------------------------- IDs
# dotted numeric (2.1.1), or letters then digits (TR-01, REQ-045, Q12), or "A.1.2"
_ID = r"(?:\d+(?:\.\d+)+|[A-Za-z]{1,5}[-_]?\d+(?:\.\d+)*|[A-Za-z]\.\d+(?:\.\d+)*)"
ID_LINE = re.compile(rf"^\s*(?P<id>{_ID})\s*[.):\-–]?\s+(?P<text>\S.*)$")
ID_ONLY = re.compile(rf"^\s*{_ID}\s*$")


def looks_like_id(value: object) -> bool:
    return bool(ID_ONLY.match(clean(value)))


# ---------------------------------------------------------------- sections
_SECTION_WORD = re.compile(
    r"^\s*(?:section|part|chapter)\s+(?P<code>[A-Za-z0-9.]+)\s*[:.\-–]\s*(?P<title>\S.*)$", re.I
)
# single-level number with a dot and a short title that is not a sentence: "2. Product Requirements"
_SECTION_NUM = re.compile(r"^\s*(?P<code>\d{1,2})\.?\s+(?P<title>[A-Z][^.!?]{2,80})$")

_INSTRUCTION_TITLE = re.compile(
    r"instruction|general|introduction|background|terms and conditions|submission|evaluation|overview|bidder",
    re.I,
)


def parse_section_heading(line: str) -> tuple[str, str] | None:
    text = clean(line)
    m = _SECTION_WORD.match(text)
    if m:
        return m.group("code"), clean(m.group("title"))
    m = _SECTION_NUM.match(text)
    if m:
        return m.group("code"), clean(m.group("title"))
    return None


def is_instruction_section(title: str) -> bool:
    return bool(_INSTRUCTION_TITLE.search(title))


# ---------------------------------------------------------------- priority
_MANDATORY = {"m", "mandatory", "must", "required", "req", "y", "yes", "shall", "essential", "critical"}
_OPTIONAL = {"o", "optional", "desirable", "nice to have", "should", "may", "n", "no", "preferred"}
_TAG = re.compile(r"\s*[\[(]\s*(mandatory|optional|m|o)\s*[\])]\s*$", re.I)


def parse_priority(value: object) -> bool | None:
    v = clean(value).lower().strip("[]() ")
    if v in _MANDATORY:
        return True
    if v in _OPTIONAL:
        return False
    return None


def strip_priority_tag(text: str) -> tuple[str, bool | None]:
    """'Support X. [Mandatory]' -> ('Support X.', True)."""
    m = _TAG.search(text)
    if not m:
        return text.strip(), None
    return text[: m.start()].strip(), parse_priority(m.group(1))


_MUST = re.compile(r"\b(must|shall|required|mandatory)\b", re.I)
_SHOULD = re.compile(r"\b(should|may|optional|desirable|nice to have)\b", re.I)


def infer_priority_from_text(text: str) -> bool | None:
    """Fallback when the RFP gives no priority column or tag. Lower confidence than a stated one."""
    if _MUST.search(text):
        return True
    if _SHOULD.search(text):
        return False
    return None


# ---------------------------------------------------------------- table headers
_HEADER_WORDS: dict[str, tuple[str, ...]] = {
    "id": ("ref", "ref.", "reference", "id", "req id", "req. id", "requirement id", "no", "no.", "#",
           "item", "s.no", "sr no", "number", "req no"),
    "text": ("requirement", "requirements", "question", "description", "requirement description",
             "specification", "details", "requirement / question"),
    "priority": ("m/o", "m / o", "mandatory", "priority", "type", "mandatory/optional", "importance"),
    "response": ("vendor response", "response", "answer", "bidder response", "supplier response",
                 "vendor's response", "proposed response"),
    "compliance": ("compliance", "compliance status", "comply", "c/pc/nc", "status"),
    "comments": ("comments", "remarks", "notes", "vendor comments"),
}


def classify_header(cell: object) -> str | None:
    h = clean(cell).lower()
    if not h:
        return None
    for role, words in _HEADER_WORDS.items():
        if h in words:
            return role
    if h.startswith("compliance"):
        return "compliance"
    if "response" in h or "answer" in h:
        return "response"
    if "requirement" in h or "question" in h:
        return "text"
    return None


def map_header_row(cells: list[object]) -> dict[str, int] | None:
    """Column roles (0-based) if this row is a requirements header, else None."""
    roles: dict[str, int] = {}
    for i, c in enumerate(cells):
        role = classify_header(c)
        if role and role not in roles:
            roles[role] = i
    return roles if "id" in roles and "text" in roles else None


def finalize(parsed: ParsedRfp) -> ParsedRfp:
    """Assign order, flag duplicate IDs, and refuse an empty parse."""
    seen: dict[str, int] = {}
    kept: list[ParsedRequirement] = []
    for r in parsed.requirements:
        if not r.text:
            parsed.warnings.append(f"{r.req_id}: empty requirement text, skipped")
            continue
        if r.req_id in seen:
            r.parse_confidence = min(r.parse_confidence, 0.5)
            r.notes.append(f"duplicate of earlier {r.req_id}")
            parsed.warnings.append(f"{r.req_id}: duplicate requirement ID (kept, order {len(kept) + 1})")
        seen[r.req_id] = seen.get(r.req_id, 0) + 1
        kept.append(r)
    for i, r in enumerate(kept, 1):
        r.order = i
    parsed.requirements = kept
    if not kept:
        raise ParseError(
            "No requirements found. The file may use an unusual layout, or it may be a scanned document."
        )
    return parsed
