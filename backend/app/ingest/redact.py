"""Customer confidentiality: names never reach the drafting model or the exported response.

Redaction happens once, at index time, so every later step (retrieval, prompts, citations,
quotes, exports) sees only the redacted text and the stored quote always matches the stored chunk.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[Customer]"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")


def redact(text: str, customer: str | None) -> str:
    if customer:
        text = re.sub(rf"{re.escape(customer)}(?:'s)?", PLACEHOLDER, text, flags=re.I)
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", text)
