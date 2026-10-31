"""Read knowledge base files (md, docx, pdf) into located chunks.

Every chunk carries a human-checkable location ("p.2, 3. Encryption") so an SE can
open the source and verify the citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import docx
import pdfplumber

from .chunker import split_text
from .parsers.common import clean, is_footer

_STAMP = re.compile(r"^(>\s*)?SAMPLE CONTENT", re.I)
_META = re.compile(r"^Document date:", re.I)


@dataclass
class RawSection:
    heading: str
    lines: list[str]
    page: int | None = None
    bullets: list[str] = field(default_factory=list)  # list items; each becomes its own chunk when there are 2+


@dataclass
class ChunkDraft:
    ordinal: int
    text: str  # heading + body, exactly what is stored, searched and quoted
    location: str
    page: int | None


def _skip(line: str) -> bool:
    return not line or bool(_STAMP.match(line)) or bool(_META.match(line)) or is_footer(line)


def read_md(path: Path) -> list[RawSection]:
    sections: list[RawSection] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            sections.append(RawSection(clean(line[3:]), []))
        elif line.startswith("# ") or _skip(line):
            continue
        elif sections:
            if line.startswith("- "):
                sections[-1].bullets.append(clean(line[2:]))
            else:
                sections[-1].lines.append(clean(line))
    return sections


def read_docx(path: Path) -> list[RawSection]:
    d = docx.Document(str(path))
    sections: list[RawSection] = []
    for p in d.paragraphs:
        text = clean(p.text)
        style = p.style.name if p.style is not None else ""
        if not text or style == "Title" or _skip(text):
            continue
        if style.startswith("Heading"):
            sections.append(RawSection(text, []))
        elif sections:
            (sections[-1].bullets if style.startswith("List") else sections[-1].lines).append(text)
    return sections


def read_pdf(path: Path) -> list[RawSection]:
    sections: list[RawSection] = []
    in_bullet = False
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for line in page.extract_text_lines(return_chars=True):
                text = clean(line["text"])
                if _skip(text):
                    continue
                bold = line["chars"] and all("Bold" in c.get("fontname", "") for c in line["chars"])
                if bold and len(text) < 100:
                    sections.append(RawSection(text, [], page.page_number))
                    in_bullet = False
                elif sections and text.startswith(("\u2022", "\u00b7")):
                    sections[-1].bullets.append(text.lstrip("\u2022\u00b7 ").strip())
                    in_bullet = True
                elif sections:
                    # a wrapped second line belongs to the bullet above it, not to the section's prose
                    if in_bullet and sections[-1].bullets and not text[:1].isupper():
                        sections[-1].bullets[-1] += " " + text
                    else:
                        sections[-1].lines.append(text)
                        in_bullet = False
    return sections


_READERS = {".md": read_md, ".txt": read_md, ".docx": read_docx, ".pdf": read_pdf}


def _raw_text(path: Path) -> str:
    """All the prose in a file, ignoring structure. Used when a document has no headings to split on."""
    if path.suffix.lower() in (".md", ".txt"):
        lines = [ln.strip().lstrip("#").strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    else:
        lines = [ln for s in _READERS[path.suffix.lower()](path) for ln in [s.heading, *s.lines, *s.bullets]]
    return " ".join(clean(ln) for ln in lines if ln and not _skip(clean(ln)))


def load_chunks(
    path: Path, *, customer: str | None = None, redact_fn=None, fallback_heading: str | None = None
) -> list[ChunkDraft]:
    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"unsupported knowledge base file type: {path.suffix}")
    chunks: list[ChunkDraft] = []
    for sec in reader(path):
        # A list of 2+ items is a list of separate facts: one chunk each, so a conflict or a stale flag on
        # one item does not taint answers that cite a different item. A lone bullet joins the prose.
        separate = sec.bullets if len(sec.bullets) >= 2 else []
        body = " ".join(sec.lines + ([] if separate else sec.bullets)).strip()
        pieces = split_text(body) if body else []
        for b in separate:
            pieces += split_text(b)
        if not pieces:
            continue
        for piece in pieces:
            text = f"{sec.heading}\n{piece}"
            if redact_fn:
                text = redact_fn(text, customer)
            location = f"p.{sec.page}, {sec.heading}" if sec.page else sec.heading
            chunks.append(ChunkDraft(len(chunks), text, redact_fn(location, customer) if redact_fn else location,
                                     sec.page))
    if not chunks and fallback_heading:  # no headings to split on: index the whole text under the title
        for piece in split_text(_raw_text(path)):
            text = f"{fallback_heading}\n{piece}"
            loc = fallback_heading
            chunks.append(ChunkDraft(len(chunks), redact_fn(text, customer) if redact_fn else text,
                                     redact_fn(loc, customer) if redact_fn else loc, None))
    return chunks
