from __future__ import annotations

import re

MAX_CHARS = 900
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])")


def split_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Split on sentence boundaries into pieces of at most max_chars, with one sentence of overlap.

    A short section stays whole, so a cited passage is normally the complete statement.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    sentences = _SENTENCE.split(text)
    pieces: list[str] = []
    current: list[str] = []
    for s in sentences:
        if current and len(" ".join(current + [s])) > max_chars:
            pieces.append(" ".join(current))
            current = [current[-1], s] if len(current[-1]) + len(s) + 1 <= max_chars else [s]
        else:
            current.append(s)
    if current:
        pieces.append(" ".join(current))
    return pieces
