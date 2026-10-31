from __future__ import annotations

import json
from pathlib import Path

import pytest

SAMPLE_RFPS = Path(__file__).resolve().parents[2] / "sample_data" / "rfps"
GOLDEN = sorted((Path(__file__).parent / "golden").glob("*.json"))


def upload(client, name: str, data: bytes | None = None, filename: str | None = None):
    payload = data if data is not None else (SAMPLE_RFPS / name).read_bytes()
    return client.post("/api/rfps", files={"file": (filename or name, payload)})


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
def test_upload_each_format_and_read_structure_back(client, path):
    g = json.loads(path.read_text())
    r = upload(client, g["filename"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["requirement_count"] == g["requirement_count"]
    assert body["file_type"] == g["file_type"]

    detail = client.get(f"/api/rfps/{body['id']}").json()
    got = [(q["req_id"], q["order"], q["mandatory"]) for s in detail["sections"] for q in s["requirements"]]
    want = [(q["req_id"], q["order"], q["mandatory"]) for q in g["requirements"]]
    assert got == want  # IDs, order and priority survive the round trip through the database
    assert [s["title"] for s in detail["sections"]] == list(dict.fromkeys(q["section_title"] for q in g["requirements"]))
    assert str(g["format_rules"]["word_limit"]) + " words" in detail["submission_instructions"]


def test_list_shows_uploaded_rfps_newest_first(client):
    upload(client, "ASIB_RFP-2026-017_Core_Islamic_Banking.xlsx")
    upload(client, "GHF_PGF-RFP-0426_Digital_Lending.pdf")
    names = [x["filename"] for x in client.get("/api/rfps").json()]
    assert names == ["GHF_PGF-RFP-0426_Digital_Lending.pdf", "ASIB_RFP-2026-017_Core_Islamic_Banking.xlsx"]


def test_unparseable_file_is_rejected_and_stores_nothing(client, tmp_path):
    r = upload(client, "x", data=b"not a workbook", filename="broken.xlsx")
    assert r.status_code == 422 and "Cannot open workbook" in r.json()["detail"]
    assert client.get("/api/rfps").json() == []
    from app.config import get_settings

    assert list(get_settings().upload_dir.iterdir()) == []  # no orphaned file


def test_wrong_type_empty_and_missing(client):
    assert upload(client, "x", data=b"hello", filename="notes.txt").status_code == 422
    assert upload(client, "x", data=b"", filename="empty.docx").status_code == 422
    assert client.get("/api/rfps/999").status_code == 404


def test_import_is_audited(client):
    rid = upload(client, "GHF_PGF-RFP-0426_Digital_Lending.pdf").json()["id"]
    from sqlmodel import Session, select

    from app.db import get_engine
    from app.models import AuditEvent

    with Session(get_engine()) as s:
        ev = s.exec(select(AuditEvent).where(AuditEvent.rfp_id == rid)).all()
    assert [(e.entity, e.action) for e in ev] == [("rfp", "imported")]
    assert ev[0].payload["requirements"] == 20
