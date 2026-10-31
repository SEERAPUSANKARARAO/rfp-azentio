"""The no-invented-facts guarantee lives here, in code, not in a prompt.

A model draft goes in. What comes out has only statements that (1) name a valid source passage,
(2) state only figures that the cited text states, and (3) overlap the cited text. Anything else is
removed and reported. A status that no surviving statement supports is downgraded to "Cannot Confirm".
Citations shown to the reviewer are always verbatim passage text, and a quote is only ever attached to a
passage it was actually found in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import enums
from ..rag.retrieve import Passage
from .entities import anchors, missing_anchors
from .schemas import DraftOut
from .textutil import (
    MARK,
    best_quote,
    clean_typography,
    content_stems,
    format_cited,
    quote_supported,
    split_sentences,
    stems_overlap,
    strip_marks,
    support_score,
    unsupported_figures,
)

SUPPORT_MIN = 0.30  # min share of a statement's content words found in its cited passages
SUPPORT_INHERITED = 0.50  # stricter, for a prose sentence that borrowed its neighbour's citation

_GAP = re.compile(
    r"\b(no (approved )?(source|evidence|information)|not (stated|confirmed|specified|evidenced|available)|"
    r"cannot (be )?confirm|unable to confirm|(does|do) not (state|specify|confirm|contain|include|address)|"
    r"not (currently )?(available|supported|held))\b", re.I)

# An answer that describes a limitation cannot be a clean "Complied"; one that describes a workable route
# cannot be a flat "Not Complied". These catch the status contradicting the answer's own words.
_LIMIT = re.compile(
    r"\b((does|do) not (natively|include|bundle|provide|offer)|not natively|no native|not bundled|"
    r"is not (included|bundled|available|supported)|only (through|via|by|with)|relies on|rely on|"
    r"must be (maintained|performed|done|configured) manually|manual(ly)?|not yet available)\b", re.I)
_WORKAROUND = re.compile(
    r"\b(manual(ly)?|(through|via|by) (an? )?integration|workaround|"
    r"can be (achieved|done|met|supported|used) (by|through|via|using|if))\b", re.I)

# ---------------------------------------------------------------- prompt injection
_INJECTION = re.compile(
    r"((ignore|disregard|forget)\s+(all\s+|any\s+|your\s+|the\s+)*(previous|prior|above|earlier|your)?\s*"
    r"(instructions?|rules|prompts?|guidelines))|note to (the )?ai|ai (assistants?|systems?|models?)\b|"
    r"assistant instruction|system prompt|state that the vendor is fully compliant", re.I)


def sanitize_requirement(text: str) -> tuple[str, list[str]]:
    """Remove bracketed notes that address an AI. Returns (clean text, removed segments).

    Defence in depth: the prompt also tells the model to ignore embedded instructions, but text that
    tries to steer the model should not reach it at all, and the reviewer should be told it was there.
    """
    removed: list[str] = []

    def drop(m: re.Match) -> str:
        if _INJECTION.search(m.group(1)):
            removed.append(m.group(1).strip())
            return ""
        return m.group(0)

    clean = re.sub(r"\s*\[([^\]]*)\]", drop, text)
    if not removed and _INJECTION.search(clean):  # unbracketed: cannot strip safely, still report it
        removed.append(_INJECTION.search(clean).group(0))
    return re.sub(r"\s+", " ", clean).strip(), removed


# ---------------------------------------------------------------- result
@dataclass
class ValidatedDraft:
    status: str
    answer: str
    flags: list[str] = field(default_factory=list)
    flag_details: dict = field(default_factory=dict)
    citations: list[dict] = field(default_factory=list)
    claims_total: int = 0
    claims_supported: int = 0
    removed: list[dict] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    reasoning: str = ""
    cited: list[Passage] = field(default_factory=list)
    coverage: float = 0.0
    sentences_kept: int = 0


def gap_text(missing: list[str], anchor_labels: list[str]) -> str:
    if anchor_labels:
        return "No approved source in the knowledge base confirms: " + ", ".join(anchor_labels) + "."
    if missing:
        return "No approved source in the knowledge base confirms this. Not evidenced: " + "; ".join(missing[:3]) + "."
    return "No approved source in the knowledge base confirms this requirement."


def _evidence(p: Passage) -> str:
    return f"{p.doc_title}\n{p.text}"


def _items(out: DraftOut, by_n: dict[int, Passage]) -> tuple[list[dict], dict[int, str], int, int]:
    """Normalise the draft to statement items. Returns (items, verified quote per passage, claims, supported).

    A quote is credited to a passage only if that passage itself contains it. (Crediting it to every
    passage a statement lists would attach quotes to sources they never appear in.)
    """
    verified: dict[int, str] = {}
    items: list[dict] = []

    if out.statements:
        supported = 0
        for st in out.statements:
            text = clean_typography(st.text)
            nums = list(dict.fromkeys([*st.passages, *(int(x) for x in MARK.findall(text))]))
            own = [n for n in nums if n in by_n]
            quote = clean_typography(st.quote).strip()
            ok = False
            for n in own:
                if quote and quote_supported(quote, [_evidence(by_n[n])]):
                    verified.setdefault(n, quote)
                    ok = True
            supported += ok
            items.append({"base": strip_marks(text), "own": own, "inherited": []})
        return items, verified, len(out.statements), supported

    # fallback: prose with inline [n] markers
    supported = 0
    for c in out.claims:
        hit = False
        for n in c.passages:
            if n in by_n and quote_supported(clean_typography(c.quote), [_evidence(by_n[n])]):
                verified.setdefault(n, clean_typography(c.quote).strip())
                hit = True
        supported += hit
    for sent in split_sentences(clean_typography(out.answer)):
        own = [n for n in dict.fromkeys(int(x) for x in MARK.findall(sent)) if n in by_n]
        items.append({"base": strip_marks(sent), "own": own, "inherited": []})
    # Models often cite once at the end of a run of sentences. Uncited sentences directly before a cited
    # one inherit its citation, but face a stricter overlap test, so nothing invented can shelter there.
    pending: list[dict] = []
    for it in items:
        if it["own"]:
            for p in pending:
                p["inherited"] = list(it["own"])
            pending = []
        else:
            pending.append(it)
    return items, verified, len(out.claims), supported


def validate_draft(
    out: DraftOut,
    passages: list[Passage],
    requirement_text: str,
    *,
    conflicts_by_chunk: dict[int, list[dict]] | None = None,
    injection_removed: list[str] | None = None,
) -> ValidatedDraft:
    by_n = {i: p for i, p in enumerate(passages, 1)}
    conflicts_by_chunk = conflicts_by_chunk or {}
    flags: list[str] = []
    details: dict = {}
    status = out.status

    # ---- 1. normalise the draft and verify the model's quotes, passage by passage
    items, verified_quotes, claims_total, claims_supported = _items(out, by_n)

    # ---- 2. statement by statement
    kept: list[tuple[str, list[int]]] = []  # (statement without markers, passage numbers)
    removed: list[dict] = []
    inherited_used = stale_dropped = 0
    for it in items:
        base, valid = it["base"], it["own"] or it["inherited"]
        inherits = not it["own"] and bool(it["inherited"])
        if not base:
            continue
        if not valid:
            if status == enums.CANNOT_CONFIRM and _GAP.search(base):
                kept.append((base, []))
            else:
                removed.append({"sentence": base, "reason": "no valid citation"})
            continue
        evidence = "\n".join(_evidence(by_n[n]) for n in valid)
        bad = unsupported_figures(base, evidence)
        if bad:
            removed.append({"sentence": base, "reason": "states figures the cited sources do not: " + ", ".join(bad)})
            continue
        score = support_score(base, evidence)
        floor = SUPPORT_INHERITED if inherits else SUPPORT_MIN
        if score < floor:
            removed.append({"sentence": base, "reason": f"cited sources barely overlap this sentence ({score:.0%})"
                            + (" (it had no citation of its own)" if inherits else "")})
            continue
        # If a current source supports the statement just as well, do not also cite an old duplicate of it:
        # a statement is only "stale" when it rests on stale sources alone.
        fresh = [n for n in valid if not by_n[n].stale_reasons]
        if fresh and len(fresh) < len(valid):
            fresh_ev = "\n".join(_evidence(by_n[n]) for n in fresh)
            if not unsupported_figures(base, fresh_ev) and support_score(base, fresh_ev) >= floor:
                stale_dropped += len(valid) - len(fresh)
                valid = fresh
        inherited_used += inherits
        kept.append((base, valid))
    if inherited_used:
        details["inherited_citations"] = inherited_used
    if stale_dropped:
        details["stale_citations_dropped"] = stale_dropped
    if removed:
        flags.append(enums.FLAG_UNSUPPORTED)
        details["removed_sentences"] = removed
    cited_sentences = [k for k in kept if k[1]]

    # ---- 3. evidence anchors: a named certification/regulation no source mentions
    req_anchors = anchors(requirement_text)
    miss = missing_anchors(requirement_text, [_evidence(p) for p in passages])
    missing_info = list(out.missing_info)
    if miss:
        flags.append(enums.FLAG_MISSING)
        details["missing_anchors"] = miss
        missing_info += [f"No source mentions {m}" for m in miss]
        if status in (enums.COMPLIED, enums.PARTIAL):
            status = enums.PARTIAL if len(miss) < len(req_anchors) and cited_sentences else enums.CANNOT_CONFIRM

    # ---- 4. a status needs evidence behind it, and must not contradict the answer's own words
    if status in (enums.COMPLIED, enums.PARTIAL, enums.NOT_COMPLIED) and not cited_sentences:
        status = enums.CANNOT_CONFIRM
        details["downgraded"] = "no statement survived validation, so no status can be supported"
    cited_text = " ".join(b for b, ns in kept if ns)
    if status == enums.COMPLIED and (cue := _LIMIT.search(cited_text)):
        status = enums.PARTIAL
        details["status_adjusted"] = f"Complied -> Partially Complied: the answer itself states a limitation ('{cue.group(0)}')"
    elif status == enums.NOT_COMPLIED and (cue := _WORKAROUND.search(cited_text)):
        status = enums.PARTIAL
        details["status_adjusted"] = f"Not Complied -> Partially Complied: the answer describes a workable route ('{cue.group(0)}')"
    if out.missing_info and status in (enums.COMPLIED, enums.PARTIAL, enums.NOT_COMPLIED):
        flags.append(enums.FLAG_MISSING)  # the model itself says part of the ask is not evidenced
    if status == enums.CANNOT_CONFIRM:
        flags.append(enums.FLAG_MISSING)

    # ---- 5. rebuild the answer, renumbering citations in order of first use
    if status == enums.CANNOT_CONFIRM and (miss or not cited_sentences):
        gaps = [k[0] for k in kept if not k[1]]
        answer = gap_text(missing_info, miss) if (miss or not gaps) else " ".join(gaps)
        cited_numbers: list[int] = []
    else:
        cited_numbers = list(dict.fromkeys(n for _, ns in kept for n in ns))
        renum = {old: new for new, old in enumerate(cited_numbers, 1)}
        answer = " ".join(format_cited(text, [renum[n] for n in ns]) for text, ns in kept)

    cited = [by_n[n] for n in cited_numbers]
    citations = []
    for new, old in enumerate(cited_numbers, 1):
        p = by_n[old]
        sent_text = " ".join(t for t, ns in kept if old in ns)
        citations.append({
            "marker": new, "chunk_id": p.chunk_id, "doc_id": p.doc_id, "doc_ref": p.doc_ref,
            "doc_title": p.doc_title, "doc_date": p.doc_date, "location": p.location,
            "quote": verified_quotes.get(old) or best_quote(sent_text, p.text),
            "stale_reasons": p.stale_reasons,
        })

    # ---- 6. freshness and contradictions among what was actually cited
    stale = [{"doc": p.doc_title, "date": p.doc_date.isoformat() if p.doc_date else None, "reasons": p.stale_reasons}
             for p in cited if p.stale_reasons]
    if stale:
        flags.append(enums.FLAG_STALE)
        details["stale"] = stale
    hits: list[dict] = []
    for p in cited:
        for c in conflicts_by_chunk.get(p.chunk_id, []):
            if c.get("status", "open") == "open" and c not in hits:
                hits.append(c)
    if hits:
        flags.append(enums.FLAG_CONFLICTING)  # only a contradiction the index found and judged raises the flag
    if hits or out.conflicts:
        # What the model says it noticed is shown to the reviewer but never raises a flag by itself: it is
        # unverified, and models over-report differences in detail as conflicts.
        details["conflicts"] = {"indexed": hits, "reported_by_model": out.conflicts}

    se_cited = [p for p in cited if p.doc_kind == enums.KB_SE_PROVIDED]
    if se_cited:
        flags.append(enums.FLAG_SE_PROVIDED)
        details["se_provided"] = [{"doc": p.doc_title, "added": p.doc_date.isoformat() if p.doc_date else None}
                                  for p in se_cited]

    if injection_removed:
        flags.append(enums.FLAG_INJECTION)
        details["injection"] = injection_removed

    # The model sometimes leaves "reasoning" blank. The reviewer should never see an unexplained status.
    reasoning = clean_typography(out.reasoning).strip() or (
        f"{status}: based on {len(cited)} cited source(s)"
        + (f"; {len(removed)} unsupported statement(s) removed" if removed else "")
        + (f"; not evidenced: {'; '.join(missing_info[:2])}" if missing_info else "") + ".")

    req_stems = content_stems(requirement_text)
    cited_stems = content_stems(" ".join(_evidence(p) for p in cited))
    coverage = stems_overlap(req_stems, cited_stems) / len(req_stems) if req_stems else 0.0

    return ValidatedDraft(
        status=status, answer=answer, flags=list(dict.fromkeys(flags)), flag_details=details,
        citations=citations, claims_total=claims_total, claims_supported=claims_supported, removed=removed,
        missing_info=missing_info, reasoning=reasoning, cited=cited, coverage=coverage,
        sentences_kept=len(cited_sentences),
    )
