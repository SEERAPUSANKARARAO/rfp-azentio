"""Read the RFP's own response-format rules, and apply them to the output.

Detection is deterministic first (regular expressions over the instructions text, checked against the
sample RFPs' ground truth). A model is only a fallback for instructions worded unlike anything the patterns
know, and it only fills gaps. Whatever is detected is shown to the SE to confirm or edit before export.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

from .. import enums

_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
_DATE = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.I)


class FormatRules(BaseModel):
    word_limit: int | None = None
    compliance_codes: list[str] = Field(default_factory=list)  # [full, partial, none] as the RFP words them
    compliance_map: dict[str, str] = Field(default_factory=dict)  # {"full": "C", "partial": "PC", "none": "NC"}
    compliance_legend: str = ""
    file_name: str | None = None  # pattern such as "<VendorName>_RFP-X.xlsx"
    submission_format: str | None = None  # xlsx | docx | pdf, from the file name
    keep_ids: bool = True  # always true: requirement IDs are never changed
    restate_question: bool = False
    cite_sources: bool = False
    deadline: str | None = None  # ISO date
    clarification_deadline: str | None = None
    detected_by: str = "rules"
    confirmed: bool = False  # becomes true when the SE reviews and saves the rules


def _iso(text: str) -> str | None:
    m = _DATE.search(text)
    if not m:
        return None
    return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()


def _classify(code: str) -> str:
    """Which of full/partial/none does this code or phrase mean?"""
    c = code.strip().lower()
    if c in ("pc",) or "partial" in c:
        return "partial"
    if c in ("nc", "n", "no") or c.startswith(("non", "not", "none", "does not", "no ")) or "non-compliant" in c:
        return "none"
    return "full"


def _codes(flat_lines: list[str]) -> tuple[list[str], dict[str, str], str]:
    for line in flat_lines:
        m = re.search(r"complian\w*[^:]*?(?:using only|only use|choose from|one of|select from)\s*:?\s*([^.]+?)\.?\s*$"
                      r"|(?:using only|only use)\s*:\s*([^.]+)", line, re.I)
        if not m:
            continue
        clause = (m.group(1) or m.group(2) or "").strip().rstrip(".")
        items = [i.strip() for i in clause.split(",") if i.strip()]
        if len(items) < 2:
            continue
        legend, codes = [], []
        for it in items:
            if "=" in it:
                left, right = (x.strip() for x in it.split("=", 1))
                codes.append(left)
                legend.append(f"{left} = {right}")
            else:
                codes.append(it)
        # map by meaning, then fall back to the order full, partial, none
        mapping: dict[str, str] = {}
        for it, code in zip(items, codes):
            mapping.setdefault(_classify(it.split("=")[-1] if "=" in it else it), code)
            if "=" in it:
                mapping.setdefault(_classify(code), code)
        if len(codes) == 3 and set(mapping) != {"full", "partial", "none"}:
            mapping = dict(zip(("full", "partial", "none"), codes))
        return codes, mapping, "; ".join(legend)
    return _codes_with_labels(flat_lines)


_CODE_WITH_LABEL = re.compile(r"\b([A-Z]{1,4})\s*\(([^()]{3,40})\)")


def _codes_with_labels(flat_lines: list[str]) -> tuple[list[str], dict[str, str], str]:
    """The other common wording: "Mark each requirement C (Complies), PC (Partially complies) or NC (Does not comply)".

    Conservative: the sentence must talk about compliance and the labels must mean at least two different
    things, so "ISO (International Organization ...)" or "UAE (United Arab Emirates)" is never read as codes.
    """
    for line in flat_lines:
        if not re.search(r"complian|complie|comply", line, re.I):
            continue
        found = _CODE_WITH_LABEL.findall(line)
        if not 2 <= len(found) <= 3 or len({c for c, _ in found}) != len(found):
            continue
        mapping: dict[str, str] = {}
        for code, label in found:
            mapping.setdefault(_classify(label), code)
        if len(mapping) != len(found):
            continue  # two labels meant the same thing, so this is not a status vocabulary
        codes = [c for c, _ in found]
        return codes, mapping, "; ".join(f"{c} = {label.strip()}" for c, label in found)
    return [], {}, ""


def extract_rules(instruction_text: str) -> dict:
    """Deterministic detection. Returns only what the text actually states."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in instruction_text.split("\n") if ln.strip()]
    flat = " ".join(lines)
    # A PDF breaks sentences across visual lines, so rules are read from sentences of the joined text.
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9<])", flat)
    r = FormatRules()

    m = re.search(r"(?:limit[^.]{0,40}?|not exceed|no more than|maximum(?: of)?|max\.?|up to)\s*(\d{2,4})\s*words", flat, re.I)
    if m:
        r.word_limit = int(m.group(1))

    r.compliance_codes, r.compliance_map, r.compliance_legend = _codes(sentences)

    m = re.search(r"(<[A-Za-z ]+>[\w.\-/<>]*\.(?:xlsx|docx|pdf))", flat)
    if m:
        r.file_name = m.group(1)
        r.submission_format = m.group(1).rsplit(".", 1)[1].lower()

    r.restate_question = bool(re.search(r"restate (each|the|every) (question|requirement)", flat, re.I))
    r.cite_sources = bool(re.search(r"cite (the )?(supporting|source|document|reference)", flat, re.I))

    for sent in sentences:
        iso = _iso(sent)
        if not iso:
            continue
        low = sent.lower()
        if "clarification" in low and not r.clarification_deadline:
            r.clarification_deadline = iso
        elif re.search(r"submit|submission|due|deadline", low) and not r.deadline and "clarification" not in low:
            r.deadline = iso

    return r.model_dump(exclude_none=True)


# --------------------------------------------------------------------------- applying the rules
_ORDER = ("full", "partial", "none")


def code_for(rules: dict, status: str) -> str:
    """The RFP's own word for this status, or "" when the RFP gave no vocabulary or the status has no code.

    "Cannot Confirm" never gets a compliance code: claiming one would assert something the sources do not.
    """
    mapping = rules.get("compliance_map") or {}
    key = {enums.COMPLIED: "full", enums.PARTIAL: "partial", enums.NOT_COMPLIED: "none"}.get(status)
    if key and mapping.get(key):
        return mapping[key]
    codes = rules.get("compliance_codes") or []
    if key and len(codes) == 3:
        return codes[_ORDER.index(key)]
    return ""


def word_count(text: str) -> int:
    """Words as a human counts them: citation markers and the punctuation they leave behind do not count."""
    return len([w for w in re.sub(r"\[\d+\]", "", text).split() if re.search(r"\w", w)])


def over_limit(rules: dict, text: str) -> int | None:
    """How many words over the limit, or None if within it (or no limit)."""
    limit = rules.get("word_limit")
    n = word_count(text)
    return n - limit if limit and n > limit else None


def response_file_name(rules: dict, vendor: str, fallback_ext: str, draft: bool = False) -> str:
    pattern = rules.get("file_name") or f"<VendorName>_Response.{fallback_ext}"
    name = re.sub(r"<[^>]*>", vendor, pattern)
    name = re.sub(r"[^\w.\-]+", "_", name)
    if draft:
        stem, dot, ext = name.rpartition(".")
        name = f"{stem}_DRAFT.{ext}" if dot else f"{name}_DRAFT"
    return name
