"""Write the response into the RFP's own workbook, in place.

The original sheet is loaded, not rebuilt, so the buyer's formatting, merged section bands, column widths and
data validation all survive. Answers go in the response column the RFP gave us; if it has none, columns are added.
"""

from __future__ import annotations

from copy import copy
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..agent.formatrules import over_limit, word_count
from ..ingest.parsers.xlsx import _find_header
from .common import DRAFT_MARK, ExportPlan, sources_text  # noqa: F401

NAVY = "1F3A5F"
WRAP = Alignment(wrap_text=True, vertical="top")


def _unique(wb, name: str) -> str:
    n, i = name, 2
    while n in wb.sheetnames:
        n, i = f"{name} {i}", i + 1
    return n


def _table(ws, headers: list[str], rows: list[list], widths: list[int]) -> None:
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = WRAP
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def _matrix_rows(plan: ExportPlan) -> list[list]:
    out = []
    for r in plan.rows:
        a = r.answer
        out.append([
            r.requirement.req_id, r.section_title, r.requirement.text,
            {True: "Mandatory", False: "Optional", None: ""}[r.requirement.mandatory],
            (a.compliance_status if a else "Not drafted"), r.code or "",
            f"{a.confidence:.0%}" if a and a.confidence is not None else "",
            ", ".join(a.flags) if a and a.flags else "",
            (a.assigned_to or "") if a else "",
            ("Approved" if r.approved else ("Draft" if r.included else "Not included")),
            (a.reviewer or "") if a and r.approved else "",
            len(r.citations),
        ])
    return out


MATRIX_HEAD = ["Ref", "Section", "Requirement", "Priority", "Status", "RFP code", "Confidence", "Flags",
               "Assigned to", "Export state", "Approved by", "Sources cited"]
MATRIX_W = [8, 24, 60, 11, 18, 10, 11, 26, 18, 14, 14, 10]


def add_support_sheets(wb, plan: ExportPlan) -> None:
    ws = wb.create_sheet(_unique(wb, "Compliance Matrix"))
    _table(ws, MATRIX_HEAD, _matrix_rows(plan), MATRIX_W)

    ws = wb.create_sheet(_unique(wb, "Clarifications"))
    _table(ws, ["Requirements", "Question for the customer", "Why we are asking", "Status"],
           [[", ".join(c.requirement_ids), c.text, c.reason, c.status] for c in plan.clarifications], [18, 70, 60, 10])

    ws = wb.create_sheet(_unique(wb, "Source Index"))
    _table(ws, ["Source", "Date", "Product version", "Kind", "Locations cited", "Used for"],
           [[s["title"], s["date"].isoformat() if s["date"] else "", s["version"] or "", s["kind"],
             "; ".join(s["locations"]), ", ".join(s["requirements"])] for s in plan.sources], [50, 12, 14, 14, 40, 30])

    ws = wb.create_sheet(_unique(wb, "Export Notes"))
    rules = plan.rules
    notes = [
        ("Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        ("Status", "DRAFT: includes answers that have not been approved. Do not submit." if plan.draft_copy
         else "Approved answers only"),
        ("Requirements", len(plan.rows)),
        ("Answered in this file", sum(r.included for r in plan.rows)),
        ("Approved", sum(r.approved for r in plan.rows)),
        ("Word limit per answer", rules.get("word_limit") or "none stated"),
        ("Compliance codes", rules.get("compliance_legend") or ", ".join(rules.get("compliance_codes", [])) or "none stated"),
        ("Citations", "[n] in an answer refers to the n-th entry of that row's Sources cell; see Source Index."),
        ("Drafted by", "RFP AI. Every answer is drafted from approved sources and reviewed by a Sales Engineer."),
    ]
    for k, v in notes:
        ws.append([k, v])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 90
    for row in ws.iter_rows():
        row[0].font = Font(bold=True)
        for c in row:
            c.alignment = WRAP


def export_xlsx(plan: ExportPlan, out_path: Path) -> None:
    wb = openpyxl.load_workbook(plan.rfp.stored_path)
    by_sheet: dict[str, list] = {}
    for row in plan.rows:
        name = (row.requirement.source_ref or {}).get("sheet")
        if name in wb.sheetnames:
            by_sheet.setdefault(name, []).append(row)

    for name, rows in by_sheet.items():
        ws = wb[name]
        ref = rows[0].requirement.source_ref
        found = _find_header(ws)
        header_row = found[0] if found else 1
        resp, comp, cmts = ref.get("response_col"), ref.get("compliance_col"), ref.get("comments_col")
        last = ws.max_column

        def new_col(title: str, width: int) -> int:
            nonlocal last
            last += 1
            hdr = ws.cell(row=header_row, column=last, value=title)
            src = ws.cell(row=header_row, column=max(1, last - 1))
            hdr.font, hdr.fill, hdr.alignment = copy(src.font), copy(src.fill), copy(src.alignment)
            hdr.border = copy(src.border)
            ws.column_dimensions[get_column_letter(last)].width = width
            return last

        resp = resp or new_col("Vendor Response", 60)
        comp = comp or new_col("Compliance", 14)
        cmts = cmts or new_col("Sources", 50)
        for row in rows:
            if not row.included:
                continue
            r = row.requirement.source_ref["row"]
            ws.cell(row=r, column=resp, value=row.text).alignment = WRAP
            if row.code:
                ws.cell(row=r, column=comp, value=row.code).alignment = WRAP
            ws.cell(row=r, column=cmts, value="Sources: " + sources_text(row.citations) if row.citations else None
                    ).alignment = WRAP

    add_support_sheets(wb, plan)
    wb.save(out_path)


def export_matrix(plan: ExportPlan, out_path: Path) -> None:
    """Compliance matrix on its own: one row per requirement with status, the RFP's code and review state."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Compliance Matrix"
    _table(ws, MATRIX_HEAD, _matrix_rows(plan), MATRIX_W)
    wb.save(out_path)
