"""Write the response into a Word document: in the RFP's own document if it was Word, otherwise a rebuilt
document that mirrors the RFP's structure and keeps every requirement ID."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph

from .common import ExportPlan, ExportRow, sources_text


def _banner(doc: Document, plan: ExportPlan, anchor=None) -> None:
    if not plan.draft_copy:
        return
    text = "DRAFT: this copy contains answers that have not been approved. Do not submit."
    if anchor is not None:
        p = OxmlElement("w:p")
        anchor.addprevious(p)
        para = Paragraph(p, doc._body)
    else:
        para = doc.add_paragraph()
    run = para.add_run(text)
    run.bold = True
    run.font.color.rgb = RGBColor(0xB0, 0x1E, 0x1E)
    run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def _para_after(doc: Document, anchor, label: str, text: str, *, italic: bool = False, small: bool = False) -> None:
    p = OxmlElement("w:p")
    anchor.addnext(p)
    para = Paragraph(p, doc._body)
    head = para.add_run(label + " ")
    head.bold = True
    body = para.add_run(text)
    body.italic = italic
    if small:
        head.font.size = body.font.size = Pt(9)


def _add_table(doc: Document, headers: list[str], rows: list[list[str]], widths: list[float] | None = None) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    for c, h in zip(t.rows[0].cells, headers):
        c.text = h
        for run in c.paragraphs[0].runs:
            run.bold = True
    for r in rows:
        cells = t.add_row().cells
        for c, v in zip(cells, r):
            c.text = str(v)
    if widths:
        for row in t.rows:
            for c, w in zip(row.cells, widths):
                c.width = Cm(w)


def _appendices(doc: Document, plan: ExportPlan) -> None:
    if plan.clarifications:
        doc.add_heading("Clarification Questions", 1)
        _add_table(doc, ["Requirements", "Question", "Reason"],
                   [[", ".join(c.requirement_ids), c.text, c.reason] for c in plan.clarifications], [3, 8, 6])
    doc.add_heading("Source Index", 1)
    _add_table(doc, ["Source", "Date", "Locations cited", "Used for"],
               [[s["title"], s["date"].isoformat() if s["date"] else "", "; ".join(s["locations"]),
                 ", ".join(s["requirements"])] for s in plan.sources], [6.5, 2.2, 5, 3.3])
    note = doc.add_paragraph(f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by RFP AI. "
                             "[n] after a statement refers to the Sources listed for that requirement.")
    note.runs[0].italic = True


def _annotate_original(plan: ExportPlan) -> Document:
    doc = Document(str(plan.rfp.stored_path))
    children = list(doc.element.body.iterchildren())

    # requirements written as numbered paragraphs: add Response / Compliance / Sources under each
    para_rows = sorted(
        ((r.requirement.source_ref["block_index"], r) for r in plan.rows
         if r.included and "block_index" in (r.requirement.source_ref or {})),
        key=lambda x: -x[0])  # last first, so earlier insertions do not shift the ones still to do
    for idx, row in para_rows:
        anchor = children[idx]
        if row.citations:  # inserted in reverse so they read Response, Compliance, Sources
            _para_after(doc, anchor, "Sources:", sources_text(row.citations), italic=True, small=True)
        if row.code:
            _para_after(doc, anchor, "Compliance:", row.code)
        _para_after(doc, anchor, "Response:", row.text)

    # requirements in a table: add Response / Compliance / Sources columns
    by_table: dict[int, list[ExportRow]] = {}
    for r in plan.rows:
        ref = r.requirement.source_ref or {}
        if "table_index" in ref:
            by_table.setdefault(ref["table_index"], []).append(r)
    for ti, rows in by_table.items():
        t = doc.tables[ti]
        for title, width in (("Response", 7), ("Compliance", 2.5), ("Sources", 5)):
            t.add_column(Cm(width))
            t.rows[0].cells[-1].text = title
            for run in t.rows[0].cells[-1].paragraphs[0].runs:
                run.bold = True
        for r in rows:
            if not r.included:
                continue
            cells = t.rows[r.requirement.source_ref["row"]].cells
            cells[-3].text, cells[-2].text = r.text, r.code
            cells[-1].text = sources_text(r.citations)

    _banner(doc, plan, children[0] if children else None)
    _appendices(doc, plan)
    return doc


def _rebuild(plan: ExportPlan) -> Document:
    """For a PDF RFP there is no document to write into, so rebuild its structure with every ID kept."""
    doc = Document()
    doc.add_heading(f"Response: {plan.rfp.name}", 0)
    _banner(doc, plan)
    doc.add_paragraph(f"Submitted by {plan.vendor}")
    by_section: dict[tuple[str, str], list[ExportRow]] = {}
    for r in plan.rows:
        by_section.setdefault((r.section_code, r.section_title), []).append(r)
    for (code, title), rows in by_section.items():
        doc.add_heading(f"Section {code}: {title}" if code else title, 1)
        _add_table(doc, ["Ref", "Requirement", "Response", "Compliance", "Sources"],
                   [[r.requirement.req_id, r.requirement.text, r.text if r.included else "",
                     r.code if r.included else "", sources_text(r.citations) if r.included else ""] for r in rows],
                   [1.6, 5, 6, 2, 3.4])
    _appendices(doc, plan)
    return doc


def export_docx(plan: ExportPlan, out_path: Path) -> None:
    doc = _annotate_original(plan) if plan.rfp.file_type == "docx" else _rebuild(plan)
    doc.core_properties.title = f"Response: {plan.rfp.name}"
    doc.save(out_path)
