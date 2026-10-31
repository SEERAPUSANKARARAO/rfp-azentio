"""Render the synthetic knowledge base and test RFPs to real files.

    cd rfp-ai/backend && .venv/bin/python ../scripts/generate_sample_data.py

Writes:
  sample_data/kb/*            knowledge base docs (md, docx, pdf) + manifest.json
  sample_data/glossary.yaml   Islamic finance term expansion used by retrieval
  sample_data/rfps/*          three test RFPs (xlsx, docx, pdf)
  sample_data/TRAPS.md        what each planted case is and what the agent must do
  backend/tests/golden/*.json expected structure and outcomes per requirement
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from sample_kb import DOCS, GLOSSARY, STAMP  # noqa: E402
from sample_rfps import FORBIDDEN_DOCS, RFPS  # noqa: E402

KB_DIR = ROOT / "sample_data" / "kb"
RFP_DIR = ROOT / "sample_data" / "rfps"
GOLDEN_DIR = ROOT / "backend" / "tests" / "golden"
NAVY = "1F3A5F"


# ====================================================================== KB renderers
def meta_line(d: dict) -> str:
    return f"Document date: {d['date']} | Product version: {d['version']}"


def render_md(d: dict, path: Path) -> None:
    out = [f"# {d['title']}", "", f"> {STAMP}", "", meta_line(d), ""]
    for kind, val in d["blocks"]:
        if kind == "h":
            out += [f"## {val}", ""]
        elif kind == "p":
            out += [val, ""]
        elif kind == "b":
            out += [f"- {b}" for b in val] + [""]
    path.write_text("\n".join(out), encoding="utf-8")


def render_docx(d: dict, path: Path) -> None:
    doc = Document()
    doc.add_heading(d["title"], 0)
    doc.add_paragraph(STAMP).runs[0].italic = True
    doc.add_paragraph(meta_line(d))
    for kind, val in d["blocks"]:
        if kind == "h":
            doc.add_heading(val, 2)
        elif kind == "p":
            doc.add_paragraph(val)
        elif kind == "b":
            for b in val:
                doc.add_paragraph(b, style="List Bullet")
    doc.core_properties.title = d["title"]
    doc.core_properties.created = datetime.fromisoformat(d["date"])
    doc.save(path)


def _styles():
    ss = getSampleStyleSheet()
    return dict(
        title=ParagraphStyle("t", parent=ss["Title"], fontSize=17, textColor=colors.HexColor("#" + NAVY)),
        h=ParagraphStyle("h", parent=ss["Heading2"], textColor=colors.HexColor("#" + NAVY), spaceBefore=10),
        p=ParagraphStyle("p", parent=ss["BodyText"], leading=14),
        stamp=ParagraphStyle("s", parent=ss["Italic"], fontSize=9, textColor=colors.grey),
        cell=ParagraphStyle("c", parent=ss["BodyText"], fontSize=9, leading=11),
        cellb=ParagraphStyle("cb", parent=ss["BodyText"], fontSize=9, leading=11, fontName="Helvetica-Bold",
                             textColor=colors.white),
    )


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(d: dict, path: Path) -> None:
    st = _styles()
    story = [Paragraph(escape(d["title"]), st["title"]), Paragraph(escape(STAMP), st["stamp"]),
             Spacer(1, 6), Paragraph(escape(meta_line(d)), st["p"])]
    for kind, val in d["blocks"]:
        if kind == "h":
            story.append(Paragraph(escape(val), st["h"]))
        elif kind == "p":
            story.append(Paragraph(escape(val), st["p"]))
            story.append(Spacer(1, 4))
        elif kind == "b":
            for b in val:
                story.append(Paragraph("&bull; " + escape(b), st["p"]))
    SimpleDocTemplate(str(path), pagesize=A4, title=d["title"], leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=2 * cm, bottomMargin=2 * cm).build(story, onFirstPage=_footer, onLaterPages=_footer)


RENDER = {"md": render_md, "docx": render_docx, "pdf": render_pdf}


def build_kb() -> list[dict]:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for d in DOCS:
        fname = f"{d['slug']}.{d['fmt']}"
        RENDER[d["fmt"]](d, KB_DIR / fname)
        manifest.append(dict(
            slug=d["slug"], file=fname, title=d["title"], kind=d["kind"], version=d["version"],
            doc_date=d["date"], status=d["status"], canonical=d["canonical"],
            reuse_permitted=d["reuse_permitted"], answerable=d["answerable"], customer=d["customer"],
            owner=d["owner"], label="SAMPLE",
        ))
    (KB_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    glossary = [dict(term=t, synonyms=s, definition=defn) for t, s, defn in GLOSSARY]
    (ROOT / "sample_data" / "glossary.yaml").write_text(
        yaml.safe_dump(glossary, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return manifest


# ====================================================================== RFP renderers
def mo(req: dict) -> str:
    return "Mandatory" if req["mandatory"] else "Optional"


def render_rfp_xlsx(r: dict, path: Path) -> None:
    wb = Workbook()
    ins = wb.active
    ins.title = "Instructions"
    bold = Font(bold=True)
    ins["A1"], ins["A1"].font = r["name"], Font(bold=True, size=14)
    ins["A2"] = f"Reference: {r['rfp_no']}"
    ins["A3"] = r["intro"]
    ins["A5"], ins["A5"].font = "Instructions to bidders", bold
    for i, line in enumerate(r["instructions"], 1):
        ins.cell(row=5 + i, column=1, value=f"{i}. {line}")
    ins.column_dimensions["A"].width = 130
    for row in ins.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    ws = wb.create_sheet("Requirements")
    ws["A1"], ws["A1"].font = r["name"], Font(bold=True, size=14)
    ws["A2"] = f"Reference: {r['rfp_no']}"
    ws["A3"] = "Complete columns D and E for every requirement."
    headers = ["Ref", "Requirement", "M/O", "Vendor Response", "Compliance (C / PC / NC)", "Comments"]
    thin = Side(style="thin", color="BBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=5, column=col, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = border
    row = 6
    dv = DataValidation(type="list", formula1='"C,PC,NC"', allow_blank=True)
    ws.add_data_validation(dv)
    for sec in r["sections"]:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        band = ws.cell(row=row, column=1, value=f"Section {sec['code']}: {sec['title']}")
        band.font = bold
        band.fill = PatternFill("solid", fgColor="DCE6F1")
        row += 1
        for req in sec["reqs"]:
            vals = [req["id"], req["text"], "M" if req["mandatory"] else "O", None, None, None]
            for col, v in enumerate(vals, 1):
                c = ws.cell(row=row, column=col, value=v)
                c.alignment = Alignment(wrap_text=True, vertical="top")
                c.border = border
            dv.add(ws.cell(row=row, column=5))
            row += 1
    for col, w in zip("ABCDEF", (8, 70, 7, 60, 16, 30)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A6"
    wb.save(path)


def render_rfp_docx(r: dict, path: Path) -> None:
    doc = Document()
    doc.add_heading(f"Request for Proposal {r['rfp_no']}", 0)
    doc.add_paragraph(r["name"])
    doc.add_paragraph(r["intro"])
    doc.add_heading("1. Instructions to Bidders", 1)
    for i, line in enumerate(r["instructions"], 1):
        doc.add_paragraph(f"1.{i} {line}")
    for sec in r["sections"]:
        doc.add_heading(f"{sec['code']}. {sec['title']}", 1)
        if sec["code"] == "4":  # one section as a table so both parser paths are exercised
            t = doc.add_table(rows=1, cols=3)
            t.style = "Table Grid"
            for cell, h in zip(t.rows[0].cells, ("Ref", "Requirement", "Priority")):
                cell.text = h
            for req in sec["reqs"]:
                cells = t.add_row().cells
                cells[0].text, cells[1].text, cells[2].text = req["id"], req["text"], mo(req)
        else:
            for req in sec["reqs"]:
                doc.add_paragraph(f"{req['id']} {req['text']} [{mo(req)}]")
    doc.core_properties.title = r["name"]
    doc.save(path)


def render_rfp_pdf(r: dict, path: Path) -> None:
    st = _styles()
    story = [Paragraph(escape(f"Request for Proposal {r['rfp_no']}"), st["title"]),
             Paragraph(escape(r["name"]), st["p"]), Spacer(1, 4),
             Paragraph(escape(r["intro"]), st["p"]),
             Paragraph("1. Instructions to Bidders", st["h"])]
    for i, line in enumerate(r["instructions"], 1):
        story.append(Paragraph(escape(f"1.{i} {line}"), st["p"]))
    for sec in r["sections"]:
        story.append(Paragraph(escape(f"Section {sec['code']}: {sec['title']}"), st["h"]))
        data = [[Paragraph(h, st["cellb"]) for h in ("Ref", "Requirement", "Priority")]]
        for req in sec["reqs"]:
            data.append([Paragraph(escape(req["id"]), st["cell"]), Paragraph(escape(req["text"]), st["cell"]),
                         Paragraph(mo(req), st["cell"])])
        tbl = Table(data, colWidths=[1.8 * cm, 12.2 * cm, 2.4 * cm], repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + NAVY)),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
    SimpleDocTemplate(str(path), pagesize=A4, title=r["name"], leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=2 * cm, bottomMargin=2 * cm).build(story, onFirstPage=_footer, onLaterPages=_footer)


RFP_RENDER = {"xlsx": render_rfp_xlsx, "docx": render_rfp_docx, "pdf": render_rfp_pdf}


def build_rfps() -> None:
    RFP_DIR.mkdir(parents=True, exist_ok=True)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for r in RFPS:
        RFP_RENDER[r["file_type"]](r, RFP_DIR / r["filename"])
        reqs, order = [], 0
        for sec in r["sections"]:
            for req in sec["reqs"]:
                order += 1
                reqs.append(dict(order=order, req_id=req["id"], section_code=sec["code"],
                                 section_title=sec["title"], mandatory=req["mandatory"],
                                 text=req["text"], expect=req["expect"]))
        golden = dict(key=r["key"], filename=r["filename"], file_type=r["file_type"], rfp_no=r["rfp_no"],
                      name=r["name"], customer=r["customer"], format_rules=r["format_rules"],
                      forbidden_docs=FORBIDDEN_DOCS, requirement_count=len(reqs), requirements=reqs)
        (GOLDEN_DIR / f"{r['key']}.json").write_text(json.dumps(golden, indent=2), encoding="utf-8")


def build_traps_doc() -> None:
    rows = ["# Planted test cases", "",
            "Generated by `scripts/generate_sample_data.py`. Each row is a requirement where the agent must",
            "behave in a specific way. The expected outcomes are checked by the golden eval.", "",
            "| RFP | Ref | Expected | Why |", "|---|---|---|---|"]
    for r in RFPS:
        for sec in r["sections"]:
            for req in sec["reqs"]:
                e = req["expect"]
                if not e["note"]:
                    continue
                bits = [e["triage"]]
                if e["status_in"]:
                    bits.append("/".join(e["status_in"]))
                if e["must_flag"]:
                    bits.append("flags: " + ", ".join(e["must_flag"]))
                rows.append(f"| {r['rfp_no']} | {req['id']} | {'; '.join(bits)} | {e['note']} |")
    (ROOT / "sample_data" / "TRAPS.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    manifest = build_kb()
    build_rfps()
    build_traps_doc()
    total = sum(len(s["reqs"]) for r in RFPS for s in r["sections"])
    print(f"KB: {len(manifest)} documents -> {KB_DIR}")
    print(f"RFPs: {len(RFPS)} files, {total} requirements -> {RFP_DIR}")
    print(f"Golden expectations -> {GOLDEN_DIR}")


if __name__ == "__main__":
    main()
