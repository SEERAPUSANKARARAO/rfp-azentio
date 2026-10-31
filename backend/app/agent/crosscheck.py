"""Checks run on the answers as they stand now, after any editing, by rule.

The drafting validator only ever saw the model's draft. A reviewer can edit an answer afterwards, and an edit can
introduce a claim no source states. This applies the same support rule to the CURRENT text of an answer.
"""

from __future__ import annotations

import re

from .textutil import (_NUM_UNIT, _unit_family, content_stems, split_sentences, standards, strip_marks,
                       support_score, unsupported_figures)

SUPPORT_MIN = 0.30  # the same threshold the drafting validator uses for a sentence with its own citation


def unsupported_claims(text: str, evidence: list[str]) -> list[str]:
    """Sentences of `text` that the cited passages do not support: too little of the sentence appears in them, or
    it states a number, date or standard they do not. With no evidence at all, nothing can be judged here."""
    if not evidence:
        return []
    blob = "\n".join(evidence)
    bad = []
    for sentence in split_sentences(strip_marks(text or "")):
        s = sentence.strip()
        if len(content_stems(s)) < 3:
            continue  # too short to judge: a heading, "Yes.", a bare label
        if support_score(s, blob) < SUPPORT_MIN or unsupported_figures(s, blob):
            bad.append(s)
    return bad


# ---------------------------------------------------------------- do the answers agree with each other?
_TOPICS = {
    "RTO": re.compile(r"\bRTO\b|recovery time", re.I),
    "RPO": re.compile(r"\bRPO\b|recovery point", re.I),
    "availability": re.compile(r"availability|uptime", re.I),
    "retention": re.compile(r"\bretain|retention|kept for", re.I),
    "response time": re.compile(r"response time|latency", re.I),
}
_AES = re.compile(r"AES-?\s?(\d{3})", re.I)


def _topic_figures(sentence: str) -> list[tuple[str, str, str]]:
    """(topic, number, unit family) for each figure, tied to the nearest topic word before it in the sentence, so
    "RTO is 2 hours and RPO is 15 minutes" gives each figure to the right topic."""
    marks = sorted((m.start(), t) for t, pat in _TOPICS.items() for m in pat.finditer(sentence))
    out = []
    for m in _NUM_UNIT.finditer(sentence):
        before = [t for pos, t in marks if pos <= m.start()]
        after = [t for pos, t in marks if m.start() < pos <= m.start() + 40]
        topic = before[-1] if before else (after[0] if after else None)
        if topic:
            out.append((topic, m.group(1).replace(",", "."), _unit_family(m.group(2))))
    return out


def inconsistent_answers(rows: list[dict]) -> list[dict]:
    """Where two requirements' answers state different figures for the same thing: one RTO of 2 hours and another of
    4 hours, AES-128 in one answer and AES-256 in another. Two answers in one bid must not disagree.

    Rule-based, so it can be wrong about scope (a premium and a standard service may legitimately differ). It is
    therefore a finding a person confirms or dismisses, never an automatic rewrite."""
    facts: dict[tuple[str, str], dict[str, set[str]]] = {}
    for r in rows:
        text = strip_marks(r.get("answer_text") or "")
        if not text:
            continue
        for sentence in split_sentences(text):
            for topic, num, unit in _topic_figures(sentence):
                facts.setdefault((topic, unit), {}).setdefault(num, set()).add(r["req_id"])
            for m in _AES.finditer(sentence):
                facts.setdefault(("encryption key size", "bit"), {}).setdefault(m.group(1), set()).add(r["req_id"])
    out = []
    for (topic, unit), values in sorted(facts.items()):
        owners = set().union(*values.values())
        if len(values) < 2 or len(owners) < 2:
            continue
        # a requirement quoting two figures itself (e.g. TLS 1.3 and 1.2) is not a disagreement between requirements
        if any(len(ids) == len(owners) for ids in values.values()):
            continue
        plural = lambda n: unit if n == "1" or unit in ("percent", "bit") else unit + "s"  # noqa: E731
        shown = {f"{n} {plural(n)}": sorted(ids) for n, ids in sorted(values.items())}
        out.append({"topic": topic, "values": shown, "requirements": sorted(owners),
                    "detail": f"{topic}: " + "; ".join(f"{', '.join(ids)} say {n}" for n, ids in shown.items())})
    return out
