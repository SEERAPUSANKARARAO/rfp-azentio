"""Shared test helpers: a scripted stand-in for the model, and small builders for sample RFP data."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import Session, select

from app.agent.evaluate import audit_answer, collect
from app.agent.schemas import Claim, DraftOut
from app.models import Requirement
from app.services.rfp_import import import_rfp

SAMPLES = Path(__file__).resolve().parents[2] / "sample_data"

# ------------------------------------------------------------------ scripted model
def grounded(prompt: str) -> DraftOut:
    """Answer from passage 1 with its first real sentence, cited, as a careful model would."""
    m = re.search(r"\[1\] [^\n]*\n<passage>\n(.*?)\n</passage>", prompt, re.S)
    if not m:
        return DraftOut(status="Cannot Confirm", answer="No source states this.", missing_info=["no passage"])
    lines = [ln for ln in m.group(1).split("\n") if ln.strip()]
    body = lines[1] if len(lines) > 1 else lines[0]
    sentence = re.split(r"(?<=[.!?])\s", body)[0]
    return DraftOut(status="Complied", answer=f"{sentence} [1]",
                    claims=[Claim(text=sentence, passages=[1], quote=sentence)], reasoning="from passage 1")


class FakeGateway:
    def __init__(self, script=None):
        self.script = script or (lambda task, prompt, n: grounded(prompt))
        self.calls: list[tuple[str, str]] = []
        self.pools = {"fake": SimpleNamespace(has_keys=lambda: True)}

    async def complete(self, task, prompt, *, system="", schema=None, max_tokens=2048, use_cache=True, temperature=0.0):
        self.calls.append((task, prompt))
        out = self.script(task, prompt, len(self.calls))
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(data=out, provider="fake", model="fake-1", key_alias="fake#1", cached=False, text="")

    def drafts(self):
        return [c for c in self.calls if c[0] == "draft"]


# ------------------------------------------------------------------ sample data
def import_sample(engine, name: str, keep: set[str] | None = None) -> dict[str, int]:
    """Import a sample RFP; optionally keep only some requirement IDs. Returns req_id -> pk."""
    with Session(engine) as s:
        rfp = import_rfp(s, SAMPLES / "rfps" / name, name)
        if keep is not None:
            for r in s.exec(select(Requirement).where(Requirement.rfp_id == rfp.id)).all():
                if r.req_id not in keep:
                    s.delete(r)
            s.commit()
        reqs = s.exec(select(Requirement).where(Requirement.rfp_id == rfp.id)).all()
        return {"_rfp": rfp.id, **{r.req_id: r.id for r in reqs}}


XLSX = "ASIB_RFP-2026-017_Core_Islamic_Banking.xlsx"
DOCX = "FHFH_NFH-RFP-2026-09_Retail_Lending.docx"


def answer_of(engine, req_pk):
    with Session(engine) as s:
        req = s.get(Requirement, req_pk)
        d, ans, cites = collect(s, req)
        return d, ans, cites, audit_answer(s, ans, d["text"], cites) if ans else None

