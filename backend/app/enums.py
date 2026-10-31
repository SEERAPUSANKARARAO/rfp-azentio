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


# ---- Level 2: capability classification, gaps, clarifications, readiness
CAP_STANDARD = "standard"  # supported out of the box
CAP_CONFIG = "configuration"  # achieved by configuring the product
CAP_DEV = "development"  # needs custom development
CAP_THIRD = "third_party"  # relies on a partner or another vendor's product
CAP_NOT = "not_available"  # the documentation says the product does not support it
CAP_UNKNOWN = "unknown"  # the evidence does not settle it: never given a response code
CAPABILITIES = (CAP_STANDARD, CAP_CONFIG, CAP_DEV, CAP_THIRD, CAP_NOT, CAP_UNKNOWN)
# When one requirement has several parts, the least favourable evidenced part decides the overall class.
CAP_SEVERITY = {CAP_STANDARD: 1, CAP_CONFIG: 2, CAP_THIRD: 3, CAP_DEV: 4, CAP_NOT: 5}

SCHEME_CAPABILITY = "capability"  # the RFP asks for Standard / Configuration / Development / ...
SCHEME_COMPLIANCE = "compliance"  # the RFP asks for Complies / Partially complies / Does not comply
NEED_EXPLANATION, NEED_ESTIMATE, NEED_ATTACHMENT = "explanation", "estimate", "attachment"

# A confirmed product gap is not the same as a gap in our documentation.
GAP_CONFIRMED = "confirmed_gap"  # the sources say the product does not do this
GAP_DEV = "development_required"
GAP_KNOWLEDGE = "knowledge_gap"  # the sources do not say: someone has to find out
GAP_INTEGRATION = "integration_dependency"
GAP_SECURITY = "security_concern"
GAP_DOCS = "documentation_gap"  # stale, conflicting or missing documentation
GAP_TYPES = (GAP_CONFIRMED, GAP_DEV, GAP_KNOWLEDGE, GAP_INTEGRATION, GAP_SECURITY, GAP_DOCS)

PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW = "high", "medium", "low"
IMPACT_DESIGN, IMPACT_DELIVERY, IMPACT_COMMERCIAL, IMPACT_COMPLIANCE = "design", "delivery", "commercial", "compliance"
