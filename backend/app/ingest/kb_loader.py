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

_STAMP = re.compile(r"^(>\s*)?(?:SAMPLE|LIVE TEST)\s+CONTENT", re.I)
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
    pre = RawSection("", [])  # text before the first heading: kept, indexed under the document's title
    sections: list[RawSection] = []
    cur = pre
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            cur = RawSection(clean(line[3:]), [])
            sections.append(cur)
        elif line.startswith("# ") or _skip(line):
            continue
        elif line.startswith("- "):
            cur.bullets.append(clean(line[2:]))
        else:
            cur.lines.append(clean(line))
    return ([pre] if pre.lines or pre.bullets else []) + sections


def _cells(row) -> list[str]:
    out: list[str] = []
    for c in row.cells:
        t = clean(c.text)
        if t and (not out or out[-1] != t):  # a merged cell repeats itself in every column it spans
            out.append(t)
    return out


def _table_lines(table) -> list[str]:
    """A table as facts, one line per row. With a header row each line reads "Region: UAE; Status: Live", so a row is
    understandable on its own when it is quoted."""
    rows = [r for r in (_cells(r) for r in table.rows) if r]
    if not rows:
        return []
    head = rows[0]
    if len(rows) >= 2 and len(head) >= 2 and len(set(head)) == len(head) and all(len(h) <= 40 for h in head):
        return ["; ".join(f"{h}: {v}" for h, v in zip(head, r)) for r in rows[1:]]
    return ["; ".join(r) for r in rows]


def _docx_blocks(d):
    """Paragraphs and tables in the order they appear, so a table stays under the heading it sits beneath."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in d.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, d)
        elif child.tag.endswith("}tbl"):
            yield Table(child, d)


def _bold_heading(p, text: str) -> bool:
    runs = [r for r in p.runs if r.text.strip()]
    return bool(runs) and all(r.bold for r in runs) and len(text) < 100 and not text.endswith((".", ",", ";"))


def read_docx(path: Path) -> list[RawSection]:
    d = docx.Document(str(path))
    # Real documents often use bold text instead of Heading styles. Only when a file has no heading style at all
    # is a short bold paragraph treated as a heading; otherwise bold is just emphasis.
    styled = any((p.style.name if p.style is not None else "").startswith("Heading") for p in d.paragraphs)
    pre = RawSection("", [])
    sections: list[RawSection] = []
    cur = pre
    for block in _docx_blocks(d):
        if hasattr(block, "rows"):
            cur.bullets.extend(_table_lines(block))
            continue
        text = clean(block.text)
        style = block.style.name if block.style is not None else ""
        if not text or style == "Title" or _skip(text):
            continue
        if style.startswith("Heading") or (not styled and _bold_heading(block, text)):
            cur = RawSection(text, [])
            sections.append(cur)
        elif style.startswith("List"):
            cur.bullets.append(text)
        else:
            cur.lines.append(text)
    return ([pre] if pre.lines or pre.bullets else []) + sections


def read_pdf(path: Path) -> list[RawSection]:
    sections: list[RawSection] = []
    cur: RawSection | None = None
    seen_heading = in_bullet = False
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for line in page.extract_text_lines(return_chars=True):
                text = clean(line["text"])
                if _skip(text):
                    continue
                bold = line["chars"] and all("Bold" in c.get("fontname", "") for c in line["chars"])
                if bold and len(text) < 100:
                    cur = RawSection(text, [], page.page_number)
                    sections.append(cur)
                    seen_heading, in_bullet = True, False
                    continue
                # No heading yet (or none at all): text still counts, one section per page so a citation names its page.
                if cur is None or (not seen_heading and cur.page != page.page_number):
                    cur = RawSection("", [], page.page_number)
                    sections.append(cur)
                if text.startswith(("\u2022", "\u00b7")):
                    cur.bullets.append(text.lstrip("\u2022\u00b7 ").strip())
                    in_bullet = True
                elif in_bullet and cur.bullets and not text[:1].isupper():
                    cur.bullets[-1] += " " + text  # a wrapped second line belongs to the bullet above it
                else:
                    cur.lines.append(text)
                    in_bullet = False
    return [x for x in sections if x.lines or x.bullets]


LOADER_VERSION = "2"  # part of what decides a file has changed: a better reader re-indexes files it improves
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
        heading = sec.heading or fallback_heading or "Introduction"
        for piece in pieces:
            text = f"{heading}\n{piece}"
            if redact_fn:
                text = redact_fn(text, customer)
            location = f"p.{sec.page}, {heading}" if sec.page else heading
            chunks.append(ChunkDraft(len(chunks), text, redact_fn(location, customer) if redact_fn else location,
                                     sec.page))
    if not chunks and fallback_heading:  # no headings to split on: index the whole text under the title
        for piece in split_text(_raw_text(path)):
            text = f"{fallback_heading}\n{piece}"
            loc = fallback_heading
            chunks.append(ChunkDraft(len(chunks), redact_fn(text, customer) if redact_fn else text,
                                     redact_fn(loc, customer) if redact_fn else loc, None))
    return chunks
