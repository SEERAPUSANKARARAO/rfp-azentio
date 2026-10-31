from __future__ import annotations

from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from .common import (
    ID_LINE,
    ParsedRequirement,
    ParsedRfp,
    ParseError,
    clean,
    finalize,
    infer_priority_from_text,
    is_instruction_section,
    looks_like_id,
    map_header_row,
    parse_priority,
    parse_section_heading,
    strip_priority_tag,
)


def _heading_level(p: Paragraph) -> int | None:
    name = (p.style.name or "") if p.style is not None else ""
    if name.startswith("Heading"):
        tail = name.split()[-1]
        return int(tail) if tail.isdigit() else 1
    return None


def parse_docx(path: Path) -> ParsedRfp:
    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise ParseError(f"Cannot open document: {type(e).__name__}") from e

    parsed = ParsedRfp(file_type="docx")
    instruction_parts: list[str] = []
    section_code, section_title = "", ""
    in_instructions = True  # front matter counts as instructions until the first requirement section
    last_req: ParsedRequirement | None = None
    table_index = -1

    for block_index, el in enumerate(d.element.body.iterchildren()):
        tag = el.tag.split("}")[1]

        if tag == "p":
            p = Paragraph(el, d)
            text = clean(p.text)
            if not text:
                continue
            style = (p.style.name or "") if p.style is not None else ""
            if style == "Title":
                parsed.title = parsed.title or text
                instruction_parts.append(text)
                continue

            level = _heading_level(p)
            if level == 1:
                heading = parse_section_heading(text)
                section_code, section_title = heading if heading else ("", text)
                in_instructions = is_instruction_section(section_title)
                last_req = None
                if in_instructions:
                    instruction_parts.append(text)
                continue
            if level is not None:  # sub-headings add no structure we need
                last_req = None
                continue

            if in_instructions:
                instruction_parts.append(text)
                continue

            m = ID_LINE.match(text)
            if m:
                body, tagged = strip_priority_tag(m.group("text"))
                conf, notes = 1.0, []
                mandatory = tagged
                if mandatory is None:
                    mandatory = infer_priority_from_text(body)
                    if mandatory is not None:
                        conf, notes = 0.8, ["priority inferred from wording"]
                last_req = ParsedRequirement(
                    req_id=m.group("id"), text=body, order=0, section_code=section_code,
                    section_title=section_title, mandatory=mandatory,
                    source_ref={"block_index": block_index}, parse_confidence=conf, notes=notes,
                )
                parsed.requirements.append(last_req)
            elif last_req is not None:
                # Unnumbered paragraph right after a requirement: treat as its continuation, but say so.
                last_req.text = f"{last_req.text} {text}"
                last_req.parse_confidence = min(last_req.parse_confidence, 0.8)
                last_req.notes.append("continuation paragraph merged")
            continue

        if tag == "tbl":
            table_index += 1
            t = Table(el, d)
            rows = [[clean(c.text) for c in r.cells] for r in t.rows]
            if not rows:
                continue
            cols = map_header_row(rows[0])
            if cols is None:
                if in_instructions:
                    instruction_parts.append("\n".join(" ".join(r) for r in rows))
                else:
                    parsed.warnings.append(f"table {table_index + 1}: no Ref/Requirement header, skipped")
                continue
            last_req = None
            for r_i, cells in enumerate(rows[1:], start=1):
                id_val = cells[cols["id"]] if cols["id"] < len(cells) else ""
                text_val = cells[cols["text"]] if cols["text"] < len(cells) else ""
                if not id_val or not text_val:
                    if text_val or id_val:
                        parsed.warnings.append(f"table {table_index + 1} row {r_i + 1}: incomplete row skipped")
                    continue
                if not looks_like_id(id_val):
                    parsed.warnings.append(f"table {table_index + 1} row {r_i + 1}: '{id_val}' is not an ID, skipped")
                    continue
                mandatory = parse_priority(cells[cols["priority"]]) if "priority" in cols else None
                conf, notes = 1.0, []
                if mandatory is None:
                    mandatory = infer_priority_from_text(text_val)
                    if mandatory is not None:
                        conf, notes = 0.8, ["priority inferred from wording"]
                parsed.requirements.append(ParsedRequirement(
                    req_id=id_val, text=text_val, order=0, section_code=section_code,
                    section_title=section_title, mandatory=mandatory,
                    source_ref={"table_index": table_index, "row": r_i,
                                "response_col": cols.get("response")},
                    parse_confidence=conf, notes=notes,
                ))

    parsed.instruction_text = "\n".join(instruction_parts)
    return finalize(parsed)
