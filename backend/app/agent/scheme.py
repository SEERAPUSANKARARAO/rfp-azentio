"""The response codes an RFP asks for, read from its own instructions.

Level 1 only knew the compliance wording (Complies / Partially complies / Does not comply). Level 2 RFPs ask bidders
to classify each capability instead (Standard / Configuration / Development / Third party / Not available), and every
customer words and abbreviates that differently. So nothing here assumes "S", "C", "D": what a code MEANS is decided by
its label, and a code whose label we cannot place stays unmapped until the Sales Engineer maps it.

Detection is by pattern, like the other format rules. Whatever is found is shown to the SE to confirm or edit.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from .. import enums


class SchemeCode(BaseModel):
    code: str
    label: str = ""
    klass: str = ""  # enums.CAP_* (capability scheme), "full" / "partial" / "none" (compliance scheme), or "" if unmapped
    needs: list[str] = Field(default_factory=list)  # explanation | estimate | attachment


class ResponseScheme(BaseModel):
    kind: str = ""  # enums.SCHEME_CAPABILITY | enums.SCHEME_COMPLIANCE
    codes: list[SchemeCode] = Field(default_factory=list)
    source: str = "detected"  # detected | edited


# Which capability class does a label describe? Checked in this order, so "Not available" is never read as "available".
_CLASS_PATTERNS = [
    (enums.CAP_NOT, r"not\s+(?:be\s+)?(?:available|supported|offered|provided)|unavailable|unsupported|"
                    r"cannot\s+(?:be\s+)?(?:met|provided|supported)|\bn/a\b"),
    (enums.CAP_THIRD, r"third[\s-]?part|\bpartner|\bexternal\b|\b3rd\b"),
    (enums.CAP_DEV, r"\bdevelop|\bcustom|bespoke|enhancement|change\s+request|\bbuild\b"),
    (enums.CAP_CONFIG, r"configur|parameteri[sz]|\bsettings?\b"),
    (enums.CAP_STANDARD, r"\bstandard\b|out[\s-]of[\s-](?:the[\s-])?box|\bnative\b|\bavailable\b"),
]
_SEP = r"(?:=|:|-|–|—)"
_PAIR_INLINE = re.compile(
    rf"\b([A-Z]{{1,3}})\s*{_SEP}\s*([A-Za-z][A-Za-z/\- ]{{1,40}}?)"
    rf"(?=\s*(?:[,;.)]|\s+or\s|\s+and\s|\s+[A-Z]{{1,3}}\s*{_SEP}\s*[A-Za-z]|$))")
_PAIR_PAREN = re.compile(r"\b([A-Z]{1,3})\s*\(([^()]{2,40})\)")
_PAIR_LINE = re.compile(rf"^\s*(?:[-*•]\s*)?([A-Z]{{1,3}})(?:\s*[-:=–—.)]\s*|\s+)([A-Za-z][^\n]{{1,60}}?)\s*$")
_LIST_INTRO = re.compile(r"(?:\bas\b|:|\bone of\b|\busing\b|\bfrom\b|\bwith\b)\s*([^:]+)$", re.I)

_NEED_PATTERNS = {
    enums.NEED_EXPLANATION: re.compile(r"explanation|justif|rationale|\bcomments?\b|describe how", re.I),
    enums.NEED_ESTIMATE: re.compile(r"estimate|\beffort\b|\bcost\b|timeline|man[\s-]?days?|person[\s-]?days?", re.I),
    enums.NEED_ATTACHMENT: re.compile(r"attach|annex|appendix|supporting document|evidence document|enclose", re.I),
}
_REQUIRE = re.compile(r"\b(?:require|requires|must|need|needs|should|accompan|include|provide|submit|supply|attach|enclose)", re.I)


def label_class(label: str) -> str:
    for klass, pattern in _CLASS_PATTERNS:
        if re.search(pattern, label, re.I):
            return klass
    return ""


def _group(pairs: list[tuple[str, str]]) -> list[SchemeCode] | None:
    """A set of (code, label) pairs is a capability scheme if it names at least three different capability classes."""
    seen, codes = set(), []
    for code, label in pairs:
        label = label.strip(" .,;")
        if code in seen or not label:
            continue
        seen.add(code)
        codes.append(SchemeCode(code=code, label=label, klass=label_class(label)))
    if len({c.klass for c in codes if c.klass}) >= 3:
        return codes
    return None


def _find_pairs(text: str) -> list[SchemeCode] | None:
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", re.sub(r"[ \t]+", " ", text))
    for s in sentences:
        for pattern in (_PAIR_INLINE, _PAIR_PAREN):
            got = _group([(m.group(1), m.group(2)) for m in pattern.finditer(s)])
            if got:
                return got
    block: list[tuple[str, str]] = []
    for line in text.split("\n"):
        m = _PAIR_LINE.match(line)
        if m:
            block.append((m.group(1), m.group(2)))
        elif block:
            got = _group(block)
            if got:
                return got
            block = []
    return _group(block) if block else None


def _word_list(text: str) -> list[SchemeCode] | None:
    """"Classify every capability as Standard, Configuration, Development, Third Party or Not Available."
    The customer gave no abbreviations, so each word is its own code."""
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n", text):
        m = _LIST_INTRO.search(sentence)
        if not m:
            continue
        parts = [p.strip(" .") for p in re.split(r",|;|\bor\b|\band\b", m.group(1)) if p.strip(" .")]
        if not 3 <= len(parts) <= 8 or any(len(p.split()) > 4 for p in parts):
            continue
        codes = [SchemeCode(code=p, label=p, klass=label_class(p)) for p in parts]
        if len({c.klass for c in codes if c.klass}) >= 3:
            return codes
    return None


def _needs(text: str, codes: list[SchemeCode]) -> None:
    """Which codes need an explanation, an estimate or an attachment, where the instructions say so."""
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n", text):
        if not _REQUIRE.search(sentence):
            continue
        wanted = [n for n, pat in _NEED_PATTERNS.items() if pat.search(sentence)]
        if not wanted:
            continue
        mentioned = {c.code for c in codes if re.search(rf"(?<![A-Za-z]){re.escape(c.code)}(?![A-Za-z])", sentence)
                     and len(c.code) <= 3}
        classes = {k for k, pat in _CLASS_PATTERNS if re.search(pat, sentence, re.I)}
        for c in codes:
            if c.code in mentioned or (c.klass and c.klass in classes) or (len(c.code) > 3 and c.code.lower() in sentence.lower()):
                for n in wanted:
                    if n not in c.needs:
                        c.needs.append(n)


def _from_compliance(compliance_map: dict, legend: str) -> ResponseScheme | None:
    if not compliance_map:
        return None
    labels = {}
    for chunk in (legend or "").split(";"):
        if "=" in chunk:
            left, right = (x.strip() for x in chunk.split("=", 1))
            labels[left] = right
    codes = [SchemeCode(code=code, label=labels.get(code, ""), klass=klass)
             for klass, code in compliance_map.items()]
    return ResponseScheme(kind=enums.SCHEME_COMPLIANCE, codes=codes)


def detect_scheme(instruction_text: str, compliance_map: dict | None = None, compliance_legend: str = "") -> dict:
    """The response scheme the instructions describe, as a dict, or {} when none is stated."""
    text = instruction_text or ""
    codes = _find_pairs(text) or _word_list(text)
    if codes:
        _needs(text, codes)
        return ResponseScheme(kind=enums.SCHEME_CAPABILITY, codes=codes).model_dump()
    scheme = _from_compliance(compliance_map or {}, compliance_legend)
    return scheme.model_dump() if scheme else {}


# ---------------------------------------------------------------- using a scheme
def scheme_of(rules: dict | None) -> ResponseScheme | None:
    raw = (rules or {}).get("response_scheme") or {}
    return ResponseScheme.model_validate(raw) if raw.get("codes") else None


def code_for_class(scheme: ResponseScheme | None, klass: str) -> str:
    """The RFP's code for a capability class, or "" when the RFP has no such code (or the class is unknown)."""
    if scheme is None or scheme.kind != enums.SCHEME_CAPABILITY or klass in ("", enums.CAP_UNKNOWN):
        return ""
    for c in scheme.codes:
        if c.klass == klass:
            return c.code
    return ""


def needs_for_code(scheme: ResponseScheme | None, code: str) -> list[str]:
    if scheme is None or not code:
        return []
    for c in scheme.codes:
        if c.code == code:
            return list(c.needs)
    return []


def unmapped_codes(scheme: ResponseScheme | None) -> list[str]:
    return [c.code for c in scheme.codes if not c.klass] if scheme else []
