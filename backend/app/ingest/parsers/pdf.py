from __future__ import annotations

from pathlib import Path

import pdfplumber

from .common import (
    ID_LINE,
    ParsedRequirement,
    ParsedRfp,
    ParseError,
    clean,
    finalize,
    infer_priority_from_text,
    is_footer,
    is_instruction_section,
    looks_like_id,
    map_header_row,
    parse_priority,
    parse_section_heading,
    strip_priority_tag,
)


def _inside(line: dict, bboxes: list[tuple]) -> bool:
    cy = (line["top"] + line["bottom"]) / 2
    return any(x0 <= line["x0"] <= x1 and top <= cy <= bottom for x0, top, x1, bottom in bboxes)


def parse_pdf(path: Path) -> ParsedRfp:
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as e:
        raise ParseError(f"Cannot open PDF: {type(e).__name__}") from e

    parsed = ParsedRfp(file_type="pdf")
    instruction_parts: list[str] = []
    section_code, section_title = "", ""
    in_instructions = True
    last_req: ParsedRequirement | None = None
    last_cols: dict[str, int] | None = None
    saw_text = False

    with pdf:
        for page in pdf.pages:
            # One reading-order stream of text lines and tables for this page
            tables = page.find_tables()
            bboxes = [t.bbox for t in tables]
            items: list[tuple[float, str, object]] = []
            for line in page.extract_text_lines():
                if _inside(line, bboxes):
                    continue
                text = clean(line["text"])
                if text and not is_footer(text):
                    items.append((line["top"], "line", text))
                    saw_text = True
            for t in tables:
                items.append((t.bbox[1], "table", t))
            items.sort(key=lambda x: x[0])

            for t_i, (_, kind, payload) in enumerate(items):
                if kind == "line":
                    text = payload  # type: ignore[assignment]
                    if not parsed.title:
                        parsed.title = text
                    heading = parse_section_heading(text)
                    if heading:
                        section_code, section_title = heading
                        in_instructions = is_instruction_section(section_title)
                        last_req = None
                        if in_instructions:
                            instruction_parts.append(text)
                        continue
                    if in_instructions:
                        instruction_parts.append(text)
                        continue
                    m = ID_LINE.match(text)
                    if m:
                        body, tagged = strip_priority_tag(m.group("text"))
                        mandatory, conf, notes = tagged, 1.0, []
                        if mandatory is None:
                            mandatory = infer_priority_from_text(body)
                            if mandatory is not None:
                                conf, notes = 0.8, ["priority inferred from wording"]
                        last_req = ParsedRequirement(
                            req_id=m.group("id"), text=body, order=0, section_code=section_code,
                            section_title=section_title, mandatory=mandatory,
                            source_ref={"page": page.page_number}, parse_confidence=conf, notes=notes,
                        )
                        parsed.requirements.append(last_req)
                    elif last_req is not None:
                        last_req.text = f"{last_req.text} {text}"
                        last_req.parse_confidence = min(last_req.parse_confidence, 0.8)
                        last_req.notes.append("continuation line merged")
                    continue

                # table
                rows = [[clean(c) for c in r] for r in payload.extract()]  # type: ignore[attr-defined]
                if not rows:
                    continue
                cols = map_header_row(rows[0])
                body_rows = rows[1:]
                if cols is None and last_cols and looks_like_id(rows[0][last_cols["id"]]):
                    cols, body_rows = last_cols, rows  # table continued on a new page without a header
                if cols is None:
                    if in_instructions:
                        instruction_parts.append("\n".join(" ".join(r) for r in rows))
                    else:
                        parsed.warnings.append(f"page {page.page_number}: table without Ref/Requirement header skipped")
                    continue
                last_cols, last_req = cols, None
                for r_i, cells in enumerate(body_rows, start=1):
                    id_val = cells[cols["id"]] if cols["id"] < len(cells) else ""
                    text_val = cells[cols["text"]] if cols["text"] < len(cells) else ""
                    if not id_val or not text_val or not looks_like_id(id_val):
                        if id_val or text_val:
                            parsed.warnings.append(
                                f"page {page.page_number}: incomplete table row skipped ('{(id_val or text_val)[:40]}')"
                            )
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
                        source_ref={"page": page.page_number, "row": r_i},
                        parse_confidence=conf, notes=notes,
                    ))

    if not saw_text and not parsed.requirements:
        raise ParseError("This PDF has no text layer (it looks scanned). OCR is not supported.")
    parsed.instruction_text = "\n".join(instruction_parts)
    return finalize(parsed)
