from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Column, LargeBinary
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- RFPs
class Rfp(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    customer: str = ""
    filename: str
    file_type: str  # xlsx | docx | pdf
    stored_path: str
    status: str = "imported"  # imported | analysed | drafting | paused | ready
    deadline: str | None = None
    submission_instructions: str = ""
    format_rules: dict = Field(default_factory=dict, sa_column=Column(JSON))
    parse_report: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Section(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    rfp_id: int = Field(index=True, foreign_key="rfp.id")
    code: str = ""  # the RFP's own section number, e.g. "3" or "3.2"
    title: str
    order: int


class Requirement(SQLModel, table=True):
    """One question or requirement, exactly as the RFP numbered it.

    req_id and order come from the parser, never from the LLM.
    """

    id: int | None = Field(default=None, primary_key=True)
    rfp_id: int = Field(index=True, foreign_key="rfp.id")
    section_id: int | None = Field(default=None, foreign_key="section.id")
    req_id: str  # "3.2.1", "Q12", "REQ-045"
    text: str
    order: int
    mandatory: bool | None = None  # None = parser could not tell
    kind: str = "requirement"  # requirement | question | matrix_row
    # Where to write the answer back: {"sheet","row","col"} | {"block_index"} | {"page"}
    source_ref: dict = Field(default_factory=dict, sa_column=Column(JSON))
    parse_confidence: float = 1.0


# ---------------------------------------------------------------- knowledge base
class KbDocument(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str  # as authored; internal views only
    display_title: str = ""  # customer-redacted; the only title shown in citations and exports
    kind: str  # see enums.KB_KINDS
    filename: str
    version: str | None = None  # product version the doc describes, e.g. "6.2"
    doc_date: date | None = None
    status: str = "approved"  # approved | deprecated
    canonical: bool = False  # SME-designated source of truth for its topic
    reuse_permitted: bool = True  # False: never retrieved (customer confidentiality)
    answerable: bool = True  # False: not used to draft answers (e.g. pricing positions)
    customer: str | None = None  # redacted from text shown to the drafting model
    owner: str | None = None
    label: str = "SAMPLE"  # provenance label shown in the UI
    content_hash: str = ""
    created_at: datetime = Field(default_factory=_now)


class KbChunk(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    doc_id: int = Field(index=True, foreign_key="kbdocument.id")
    ordinal: int
    text: str
    location: str = ""  # "Section 3.2" / "p.14"
    page: int | None = None
    topic_tags: list = Field(default_factory=list, sa_column=Column(JSON))
    embedding: bytes | None = Field(default=None, sa_column=Column(LargeBinary))


# ---------------------------------------------------------------- answers
class Answer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    requirement_id: int = Field(index=True, unique=True, foreign_key="requirement.id")
    draft_text: str = ""
    final_text: str | None = None  # set when the SE edits; export prefers it
    compliance_status: str = "Pending"
    confidence: float | None = None
    confidence_breakdown: dict = Field(default_factory=dict, sa_column=Column(JSON))
    triage: str = "auto"
    flags: list = Field(default_factory=list, sa_column=Column(JSON))
    flag_details: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reasoning: str = ""  # why this status: shown in the evidence drawer
    owner_hint: str | None = None  # "Commercial", "Legal", ... for owner-triaged items
    review_status: str = "queued"  # queued | drafted | accepted
    needs_review: bool = False
    edited: bool = False
    assigned_to: str | None = None
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    error: str | None = None
    updated_at: datetime = Field(default_factory=_now)


class Citation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    answer_id: int = Field(index=True, foreign_key="answer.id")
    chunk_id: int | None = Field(default=None, foreign_key="kbchunk.id")
    doc_id: int | None = Field(default=None, foreign_key="kbdocument.id")
    marker: int  # the [n] used in the draft text
    quote: str = ""
    doc_title: str = ""
    doc_date: date | None = None
    location: str = ""


class ClarificationQuery(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    rfp_id: int = Field(index=True, foreign_key="rfp.id")
    text: str
    reason: str = ""
    requirement_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "open"  # open | approved | dismissed


class Conflict(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # stale | conflict
    topic: str = ""
    chunk_a_id: int | None = Field(default=None, foreign_key="kbchunk.id")
    chunk_b_id: int | None = Field(default=None, foreign_key="kbchunk.id")
    doc_a_id: int = Field(foreign_key="kbdocument.id")
    doc_b_id: int | None = Field(default=None, foreign_key="kbdocument.id")
    detail: str = ""
    canonical_doc_id: int | None = Field(default=None, foreign_key="kbdocument.id")
    status: str = "open"  # open | resolved
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------- activity
class AuditEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=_now, index=True)
    rfp_id: int | None = Field(default=None, index=True)
    entity: str  # answer | requirement | rfp | kb_document | conflict | export
    entity_id: int | None = None
    action: str  # drafted | edited | accepted | flagged | regenerated | exported | ...
    actor: str = "system"
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))


class ChatMessage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    rfp_id: int = Field(index=True, foreign_key="rfp.id")
    role: str  # user | assistant
    content: str
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    ts: datetime = Field(default_factory=_now)


class Job(SQLModel, table=True):
    """Background drafting run. Per-requirement state lives on Answer, so a job
    can be resumed after a restart or an LLM quota pause."""

    id: int | None = Field(default=None, primary_key=True)
    rfp_id: int = Field(index=True, foreign_key="rfp.id")
    status: str = "running"
    total: int = 0
    done: int = 0
    message: str = ""
    resume_at: float | None = None  # epoch seconds when the LLM quota may recover
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
