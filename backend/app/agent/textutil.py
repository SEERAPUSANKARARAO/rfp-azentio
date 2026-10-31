"""Text helpers behind the grounding checks. Deliberately simple and deterministic: the checks that
stop an invented fact reaching an SE must not themselves depend on a model's opinion."""

from __future__ import annotations

import re

MARK = re.compile(r"\[(\d+)\]")

STOP = frozenset(
    "about above after again also another because been before being between both cannot could does doing "
    "each from further have having here into itself more most must only other over same shall should some "
    "such than that their them then there these they this those through under until very were what when "
    "where which while will with within would your you our ours its the and for are was can may not any "
    "provide state describe confirm please include including support supports supported solution platform "
    "vendor system".split()
)


_TYPOGRAPHY = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",  # hyphens and dashes
    "‘": "'", "’": "'", "“": '"', "”": '"',  # curly quotes
    " ": " ", " ": " ", "​": "",  # non-breaking and zero-width spaces
})


def clean_typography(text: str) -> str:
    """Models emit non-breaking hyphens and curly quotes. Fold them to plain ASCII so exports are
    clean and a quote still matches its source text."""
    return text.translate(_TYPOGRAPHY)


def format_cited(base: str, numbers: list[int]) -> str:
    """'The RTO is 1 hour.' + [2] -> 'The RTO is 1 hour [2].' One uniform style, marker before the stop."""
    if not numbers:
        return base
    m = re.match(r"^(.*?)([.!?]?)$", base.strip(), re.S)
    return f"{m.group(1).rstrip()} {''.join(f'[{n}]' for n in numbers)}{m.group(2)}"


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def stem(word: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(word) > len(suf) + 3 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def content_stems(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", MARK.sub(" ", text).lower())
    return {stem(w) for w in words if len(w) >= 4 and w not in STOP}


def stems_overlap(mine: set[str], theirs: set[str]) -> int:
    """How many of my stems appear in theirs. Long stems also match by prefix, so "encrypt" matches
    "encryption", "implement" matches "implementation", without a real stemmer."""
    count = 0
    for a in mine:
        if a in theirs or (len(a) >= 5 and any(len(b) >= 5 and (a.startswith(b) or b.startswith(a)) for b in theirs)):
            count += 1
    return count


def split_sentences(text: str) -> list[str]:
    """Split on sentence ends, keeping any citation markers with the sentence they close."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Models write the marker before or after the full stop ("hour [1]." or "hour. [1]").
    # Capture trailing markers so they stay with the sentence they cite instead of being lost.
    boundary = re.compile(r"(?<=[.!?])((?:\s*\[\d+\])*)\s+(?=[A-Z0-9\"'(])")  # a marker never starts a sentence
    out: list[str] = []
    start = 0
    for m in boundary.finditer(text):
        sentence = text[start : m.start()] + (" " + m.group(1).strip() if m.group(1).strip() else "")
        out.append(sentence.strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def strip_marks(text: str) -> str:
    t = re.sub(r"\s+([.!?,;:])", r"\1", MARK.sub("", text))  # no orphan space left before punctuation
    return re.sub(r"\s+", " ", t).strip()


# ----------------------------------------------------------------------------- figures
_UNIT_FAMILY = {
    "hour": "hour", "hr": "hour", "minute": "minute", "min": "minute", "month": "month", "year": "year",
    "day": "day", "week": "week", "percent": "percent", "%": "percent", "fte": "staff",
    "full-time staff": "staff", "staff": "staff", "region": "region",
}
_NUM_UNIT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(full-time staff|hours?|hrs?|minutes?|mins?|months?|years?|days?|weeks?|percent|%|fte|staff|regions?)\b",
    re.I,
)
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_DATE = re.compile(
    rf"\b(\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}|(?:{_MONTHS})\s+\d{{4}}|Q[1-4]\s*\d{{4}}|FY\s?\d{{2,4}})\b", re.I
)
_STANDARD = re.compile(
    r"\b(AES-?\s?\d{3}|TLS\s?\d(?:\.\d)?|ISO(?:/IEC)?\s*\d{4,5}(?::\d{4})?|SOC\s?\d|GDPR|DPDP|PCI[\s-]?DSS|"
    r"EIBOR|SAIBOR|SOFR|FAS\s?\d+|[a-z]{2}-[a-z]+-\d)\b",
    re.I,
)


def _unit_family(unit: str) -> str:
    u = unit.lower()
    if u in _UNIT_FAMILY:
        return _UNIT_FAMILY[u]
    if u.endswith("s") and u[:-1] in _UNIT_FAMILY:  # hours -> hour, regions -> region
        return _UNIT_FAMILY[u[:-1]]
    return u


def number_units(text: str) -> set[tuple[str, str]]:
    return {(num.replace(",", "."), _unit_family(unit)) for num, unit in _NUM_UNIT.findall(text)}


def dates(text: str) -> set[str]:
    return {norm(m) for m in _DATE.findall(text)}


def standards(text: str) -> set[str]:
    out = set()
    for m in _STANDARD.findall(text):
        out.add(re.sub(r"[^a-z0-9]", "", m.lower().replace("/iec", "")))
    return out


def unsupported_figures(sentence: str, evidence: str) -> list[str]:
    """Numbers-with-units, dates and named standards in the sentence that the evidence never states."""
    s = strip_marks(sentence)
    bad: list[str] = []
    ev_nu = number_units(evidence)
    for num, unit in sorted(number_units(s) - ev_nu):
        bad.append(f"{num} {unit}")
    ev_dates = dates(evidence)
    for d in sorted(dates(s) - ev_dates):
        bad.append(d)
    ev_std = standards(evidence)
    for t in sorted(standards(s) - ev_std):
        bad.append(t)
    return bad


# ----------------------------------------------------------------------------- support
def support_score(sentence: str, evidence: str) -> float:
    """Share of the sentence's content words that appear in the evidence (0..1)."""
    mine = content_stems(sentence)
    if not mine:
        return 1.0
    return stems_overlap(mine, content_stems(evidence)) / len(mine)


def quote_supported(quote: str, passages: list[str]) -> bool:
    """True if the quote is (nearly) verbatim in one of the passages."""
    q = norm(quote)
    if len(q) < 8:
        return False
    for p in passages:
        pn = norm(p)
        if q in pn:
            return True
        words = q.split()
        if len(words) >= 4:
            grams = {" ".join(words[i : i + 3]) for i in range(len(words) - 2)}
            if sum(1 for g in grams if g in pn) / len(grams) >= 0.85:
                return True
    return False


def best_quote(sentence: str, passage_text: str, max_len: int = 320) -> str:
    """The passage sentence that best matches the answer sentence: always verbatim source text."""
    pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", passage_text) if p.strip()]
    if not pieces:
        return passage_text[:max_len]
    mine = content_stems(sentence)
    best = max(pieces, key=lambda p: (stems_overlap(mine, content_stems(p)), -len(p)))
    return best[:max_len]
