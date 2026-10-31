from __future__ import annotations

from pathlib import Path

import openpyxl

from .common import (
    ParsedRequirement,
    ParsedRfp,
    ParseError,
    clean,
    finalize,
    infer_priority_from_text,
    map_header_row,
    parse_priority,
    parse_section_heading,
)


def _sheet_text(ws) -> str:
    lines = []
    for row in ws.iter_rows(values_only=True):
        cells = [clean(c) for c in row if clean(c)]
        if cells:
            lines.append(" ".join(cells))
    return "\n".join(lines)


def _find_header(ws) -> tuple[int, dict[str, int]] | None:
    for r in range(1, min(ws.max_row, 40) + 1):
        mapping = map_header_row([c.value for c in ws[r]])
        if mapping:
            return r, mapping
    return None


def _id_text(value: object, warnings: list[str], where: str) -> str:
    if isinstance(value, float):
        # Excel stores 1.10 as the number 1.1, which silently renumbers the requirement.
        warnings.append(f"{where}: ID '{value}' is a number; a trailing zero (1.10) may have been lost")
    return clean(value)


def parse_xlsx(path: Path) -> ParsedRfp:
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:  # corrupt or not a workbook
        raise ParseError(f"Cannot open workbook: {type(e).__name__}") from e

    parsed = ParsedRfp(file_type="xlsx")
    instruction_parts: list[str] = []

    for ws in wb.worksheets:
        found = _find_header(ws)
        if not found:
            text = _sheet_text(ws)
            if text:
                instruction_parts.append(text)
            continue

        header_row, cols = found
        # Title block above the header often carries instructions
        for r in range(1, header_row):
            line = " ".join(clean(c.value) for c in ws[r] if clean(c.value))
            if line:
                instruction_parts.append(line)
                if not parsed.title:
                    parsed.title = line

        section_code, section_title = "", ws.title
        last_id_row = header_row
        for r in range(header_row + 1, ws.max_row + 1):
            row = ws[r]
            values = [c.value for c in row]
            non_empty = [i for i, v in enumerate(values) if clean(v)]
            if not non_empty:
                continue

            id_i, text_i = cols["id"], cols["text"]
            id_val, text_val = clean(values[id_i]) if id_i < len(values) else "", \
                clean(values[text_i]) if text_i < len(values) else ""

            # Section band: a lone cell (usually merged across the row)
            if non_empty == [0] or (len(non_empty) == 1 and not (id_val and text_val)):
                heading = parse_section_heading(clean(values[non_empty[0]]))
                section_code, section_title = heading if heading else ("", clean(values[non_empty[0]]))
                continue

            if text_val and not id_val:
                parsed.warnings.append(
                    f"{ws.title} row {r}: text without a requirement ID, skipped (not numbered: '{text_val[:50]}')"
                )
                continue
            if not (id_val and text_val):
                continue

            req_id = _id_text(values[id_i], parsed.warnings, f"{ws.title} row {r}")
            mandatory, conf, notes = None, 1.0, []
            if "priority" in cols and cols["priority"] < len(values):
                mandatory = parse_priority(values[cols["priority"]])
            if mandatory is None:
                mandatory = infer_priority_from_text(text_val)
                if mandatory is not None:
                    conf, notes = 0.8, ["priority inferred from wording"]
            parsed.requirements.append(ParsedRequirement(
                req_id=req_id, text=text_val, order=0, section_code=section_code,
                section_title=section_title, mandatory=mandatory,
                source_ref={
                    "sheet": ws.title, "row": r,
                    "response_col": cols["response"] + 1 if "response" in cols else None,
                    "compliance_col": cols["compliance"] + 1 if "compliance" in cols else None,
                    "comments_col": cols["comments"] + 1 if "comments" in cols else None,
                },
                parse_confidence=conf, notes=notes,
            ))
            last_id_row = r

    parsed.instruction_text = "\n".join(instruction_parts)
    return finalize(parsed)
