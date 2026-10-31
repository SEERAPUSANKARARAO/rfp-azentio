"""Knowledge-base files in the shapes real documents come in: tables, bold instead of heading styles, text before the
first heading, PDFs with no bold at all. Text that is in a source must be able to become evidence."""

import json
from pathlib import Path

import docx
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

from app.ingest.kb_loader import load_chunks

ROOT = Path(__file__).resolve().parents[2]


def texts(path, title="Doc"):
    return [c.text.replace("\n", " | ") for c in load_chunks(path, fallback_heading=title)]


# ---------------------------------------------------------------- Word
def test_a_word_table_becomes_one_fact_per_row_under_the_heading_it_sits_beneath(tmp_path):
    d = docx.Document()
    d.add_heading("1. Regions", 2)
    t = d.add_table(rows=3, cols=2)
    for r, row in enumerate([("Region", "Status"), ("UAE", "Live"), ("Saudi Arabia", "Live")]):
        for c, v in enumerate(row):
            t.rows[r].cells[c].text = v
    d.add_heading("2. Notes", 2)
    d.add_paragraph("Backups stay in country.")
    d.save(tmp_path / "t.docx")
    assert texts(tmp_path / "t.docx") == ["1. Regions | Region: UAE; Status: Live", "1. Regions | Region: Saudi Arabia; Status: Live",
                                          "2. Notes | Backups stay in country."]


def test_a_table_with_no_header_row_keeps_each_row_as_a_line_and_merged_cells_do_not_repeat(tmp_path):
    d = docx.Document()
    d.add_heading("Limits", 2)
    t = d.add_table(rows=2, cols=3)
    t.rows[0].cells[0].merge(t.rows[0].cells[2]).text = "Support hours"  # one merged cell across three columns
    t.rows[1].cells[0].text, t.rows[1].cells[1].text, t.rows[1].cells[2].text = "Sunday", "to", "Thursday"
    d.save(tmp_path / "m.docx")
    assert texts(tmp_path / "m.docx") == ["Limits | Support hours", "Limits | Sunday; to; Thursday"]


def test_bold_text_is_a_heading_only_when_the_file_has_no_heading_styles_at_all(tmp_path):
    d = docx.Document()
    p = d.add_paragraph(); p.add_run("1. Encryption").bold = True
    d.add_paragraph("Data at rest is encrypted with AES-256.")
    p = d.add_paragraph(); p.add_run("2. Access").bold = True
    d.add_paragraph("Administrators must use multi-factor authentication.")
    d.save(tmp_path / "b.docx")
    assert texts(tmp_path / "b.docx") == ["1. Encryption | Data at rest is encrypted with AES-256.",
                                          "2. Access | Administrators must use multi-factor authentication."]

    e = docx.Document()  # headings are styled: a bold phrase is only emphasis and must not split the section
    e.add_heading("1. Encryption", 2)
    q = e.add_paragraph(); q.add_run("Important: ").bold = True; q.add_run("data at rest is encrypted with AES-256.")
    e.save(tmp_path / "e.docx")
    assert texts(tmp_path / "e.docx") == ["1. Encryption | Important: data at rest is encrypted with AES-256."]


def test_a_word_file_with_no_headings_and_no_bold_is_still_indexed_under_its_title(tmp_path):
    d = docx.Document()
    d.add_paragraph("Data at rest is encrypted with AES-256.")
    d.save(tmp_path / "p.docx")
    assert texts(tmp_path / "p.docx", "Security note") == ["Security note | Data at rest is encrypted with AES-256."]


def test_text_before_the_first_heading_is_kept(tmp_path):
    d = docx.Document()
    d.add_paragraph("This policy applies to every hosted environment.")
    d.add_heading("1. Encryption", 2)
    d.add_paragraph("Data at rest is encrypted with AES-256.")
    d.save(tmp_path / "i.docx")
    assert texts(tmp_path / "i.docx", "Hosting policy")[0] == "Hosting policy | This policy applies to every hosted environment."


# ---------------------------------------------------------------- Markdown and text
def test_markdown_text_before_the_first_heading_is_kept_but_stamps_and_the_date_line_are_not(tmp_path):
    f = tmp_path / "m.md"
    f.write_text("# Policy\n\n> LIVE TEST CONTENT: synthetic.\n\nDocument date: 2026-01-01 | Product version: 6.2\n\n"
                 "This intro explains the scope.\n\n## 1. Encryption\n\nData at rest is encrypted.\n")
    assert texts(f, "Policy") == ["Policy | This intro explains the scope.", "1. Encryption | Data at rest is encrypted."]


# ---------------------------------------------------------------- PDF
def make_pdf(path, pages, bold_first=False):
    ss = getSampleStyleSheet()
    story = []
    for i, lines in enumerate(pages):
        for n, line in enumerate(lines):
            story.append(Paragraph(f"<b>{line}</b>" if bold_first and n == 0 else line, ss["Normal"]))
        if i < len(pages) - 1:
            story.append(PageBreak())
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


def test_a_pdf_with_no_bold_text_is_indexed_page_by_page_so_a_citation_names_its_page(tmp_path):
    make_pdf(tmp_path / "n.pdf", [["Data at rest is encrypted with AES-256."], ["Backups stay in the same country."]])
    chunks = load_chunks(tmp_path / "n.pdf", fallback_heading="Whitepaper")
    assert [(c.page, c.location) for c in chunks] == [(1, "p.1, Whitepaper"), (2, "p.2, Whitepaper")]
    assert "AES-256" in chunks[0].text and "Backups" in chunks[1].text


def test_a_pdf_with_bold_headings_still_splits_on_them(tmp_path):
    make_pdf(tmp_path / "h.pdf", [["1. Encryption", "Data at rest is encrypted with AES-256."]], bold_first=True)
    assert texts(tmp_path / "h.pdf") == ["1. Encryption | Data at rest is encrypted with AES-256."]


def test_a_pdf_with_no_text_at_all_gives_nothing_and_the_warning_says_why(client, tmp_path):
    make_pdf(tmp_path / "blank.pdf", [[""]])
    assert load_chunks(tmp_path / "blank.pdf", fallback_heading="Blank") == []
    r = client.post("/api/kb/documents", data={"kind": "product_doc"},
                    files={"file": ("blank.pdf", (tmp_path / "blank.pdf").read_bytes())})
    assert r.status_code == 201 and r.json()["chunks"] == 0
    assert any("scanned PDF" in w and "no OCR" in w for w in r.json()["warnings"])
    r = client.post("/api/kb/documents", data={"kind": "product_doc"}, files={"file": ("empty.md", b"\n\n   \n")})
    assert any("no readable text" in w and "scanned" not in w for w in r.json()["warnings"])


# ---------------------------------------------------------------- the existing sets are unchanged
def test_the_sample_and_live_test_knowledge_bases_chunk_exactly_as_before():
    def total(folder: Path, names):
        return sum(len(load_chunks(folder / n, fallback_heading=Path(n).stem)) for n in names)

    sample = ROOT / "sample_data" / "kb"
    assert total(sample, [m["file"] for m in json.loads((sample / "manifest.json").read_text())]) == 112
    live = ROOT / "live_test" / "kb"
    if live.exists():  # the live-test stamp must not become evidence
        assert total(live, [m["file"] for m in json.loads((ROOT / "live_test" / "kb_metadata.json").read_text())]) == 43  # was 45 before the reader stopped indexing the stamp line of the two PDFs as a passage
