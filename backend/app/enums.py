"""String vocabularies shared by the backend and, via the API, the frontend."""

from __future__ import annotations

# Compliance status of a drafted answer (Level 1 requirement 03)
COMPLIED = "Complied"
PARTIAL = "Partially Complied"
NOT_COMPLIED = "Not Complied"
CANNOT_CONFIRM = "Cannot Confirm"  # evidence missing: never filled by assumption
PENDING = "Pending"
COMPLIANCE_STATUSES = (COMPLIED, PARTIAL, NOT_COMPLIED, CANNOT_CONFIRM, PENDING)

# Triage: what the agent decided to do with a requirement
TRIAGE_AUTO = "auto"  # answer confidently from sources
TRIAGE_LOW = "low_confidence"  # flagged low-confidence draft
TRIAGE_CLARIFY = "clarify"  # sources cannot answer: goes to the clarification list
TRIAGE_OWNER = "owner"  # pricing/legal/commercial: never auto-answered
TRIAGE_VALUES = (TRIAGE_AUTO, TRIAGE_LOW, TRIAGE_CLARIFY, TRIAGE_OWNER)

# Evidence flags raised on an answer (Level 1 requirement 03 / acceptance criteria)
FLAG_MISSING = "missing"
FLAG_CONFLICTING = "conflicting"
FLAG_STALE = "stale"
FLAG_UNSUPPORTED = "unsupported"  # a claim in the draft is not backed by a cited passage
FLAG_COMMERCIAL = "commercial"
FLAG_LEGAL = "legal"
FLAG_SE_PROVIDED = "se_provided"  # relies on a fact the SE typed in, not on the KB
FLAG_INJECTION = "injection"  # the requirement text tried to give the assistant instructions

# Review lifecycle. Nothing is final until the SE accepts it.
REVIEW_QUEUED = "queued"
REVIEW_DRAFTED = "drafted"
REVIEW_ACCEPTED = "accepted"

# Knowledge base
KB_PRODUCT_DOC = "product_doc"
KB_SOW = "sow"
KB_PAST_RFP = "past_rfp"
KB_SECURITY = "security_doc"
KB_POLICY = "policy"
KB_GLOSSARY = "glossary"
KB_SE_PROVIDED = "se_provided"
KB_KINDS = (KB_PRODUCT_DOC, KB_SOW, KB_PAST_RFP, KB_SECURITY, KB_POLICY, KB_GLOSSARY, KB_SE_PROVIDED)

DOC_APPROVED = "approved"
DOC_DEPRECATED = "deprecated"

# Conflicts
CONFLICT_STALE = "stale"
CONFLICT_CONTRADICTION = "conflict"
CONFLICT_OPEN = "open"
CONFLICT_RESOLVED = "resolved"

# Background drafting job
JOB_RUNNING = "running"
JOB_PAUSED = "paused"  # LLM quota exhausted; resumes automatically or on demand
JOB_DONE = "done"
JOB_FAILED = "failed"
