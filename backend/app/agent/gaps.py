"""Gap analysis, by rule: what is missing, how serious, and who should look at it.

The distinction the brief cares about is a CONFIRMED product gap (the documentation says the product does not do this)
versus a KNOWLEDGE gap (the documentation is silent, so someone has to find out). They need different people and
different conversations with the customer, so they are never merged.
"""

from __future__ import annotations

import re

from .. import enums

PRODUCT_MGMT = "Product Management"
ENGINEERING = "Engineering"
ARCHITECTURE = "Solution Architecture"
SECURITY = "Security Officer"
DOCS_OWNER = "SME"

_SECURITY = re.compile(
    r"encrypt|certificat|\bISO\b|\bSOC\s?2|penetration|vulnerab|\bMFA\b|multi-?factor|authenticat|data residency|"
    r"audit (?:log|trail)|\bPCI\b|privacy|GDPR|access control|key management|\bTLS\b|\bAES\b|firewall|intrusion|"
    r"incident|backup|disaster|business continuity|data protection|\bRTO\b|\bRPO\b|"
    r"\bhosted\b|\bhosting\b|data (?:cent(?:er|re)|location)|sovereign|in-country|localisation|localization", re.I)
_INTEGRATION = re.compile(
    r"integrat|interface|\bAPI\b|connector|third[\s-]?party|core banking|\bswift\b|payment gateway|credit bureau|"
    r"\bAML\b|\bERP\b|\bSSO\b|migrat", re.I)

# Most serious first: this is also the order in which the owner of the first gap becomes the suggested owner.
_ORDER = [enums.GAP_SECURITY, enums.GAP_CONFIRMED, enums.GAP_DEV, enums.GAP_INTEGRATION, enums.GAP_KNOWLEDGE,
          enums.GAP_DOCS]


def is_security(text: str) -> bool:
    return bool(_SECURITY.search(text or ""))


def is_integration(text: str) -> bool:
    return bool(_INTEGRATION.search(text or ""))


def analyse(
    requirement: str, mandatory: bool | None, status: str, flags: list[str], capability: str,
    unevidenced_parts: list[str],
) -> tuple[list[dict], str]:
    """Returns (gaps, suggested owner). Each gap: type, detail, owner, critical (a mandatory requirement is affected)."""
    critical = mandatory is not False
    found: dict[str, dict] = {}

    def add(kind: str, detail: str, owner: str) -> None:
        found.setdefault(kind, {"type": kind, "detail": detail, "owner": owner, "critical": critical})

    flags = set(flags or [])
    if capability == enums.CAP_NOT:
        add(enums.GAP_CONFIRMED, "The documentation says the product does not provide this.", PRODUCT_MGMT)
    elif capability == enums.CAP_DEV:
        add(enums.GAP_DEV, "Meeting this needs development work: Engineering must size it and Product Management "
                           "must agree whether to commit.", ENGINEERING)
    elif capability == enums.CAP_THIRD:
        add(enums.GAP_INTEGRATION, "This depends on a third party or an integration: confirm the partner, the "
                                   "interface and who owns support.", ARCHITECTURE)

    not_evidenced = capability == enums.CAP_UNKNOWN or status == enums.CANNOT_CONFIRM or bool(unevidenced_parts)
    if not_evidenced:
        if is_security(requirement):
            owner = SECURITY
        elif is_integration(requirement):
            owner = ARCHITECTURE
        else:
            owner = PRODUCT_MGMT
        what = ("; ".join(unevidenced_parts[:3]) if unevidenced_parts else "how or whether this is supported")
        add(enums.GAP_KNOWLEDGE, f"Our documentation does not settle {what}. This is a gap in what we know, not a "
                                 "confirmed gap in the product.", owner)

    if is_security(requirement) and (
        capability in (enums.CAP_NOT, enums.CAP_DEV, enums.CAP_THIRD, enums.CAP_UNKNOWN)
        or status in (enums.PARTIAL, enums.NOT_COMPLIED, enums.CANNOT_CONFIRM)
        or flags & {enums.FLAG_STALE, enums.FLAG_CONFLICTING, enums.FLAG_UNSUPPORTED}
    ):
        add(enums.GAP_SECURITY, "A security or assurance requirement is not fully and currently evidenced.", SECURITY)

    docs = []
    if enums.FLAG_STALE in flags:
        docs.append("relies on a stale source")
    if enums.FLAG_CONFLICTING in flags:
        docs.append("has sources that contradict each other")
    if enums.FLAG_SE_PROVIDED in flags:
        docs.append("rests on a fact typed in by the Sales Engineer, not on documentation")
    if docs:
        add(enums.GAP_DOCS, "The documentation behind this answer " + " and ".join(docs) + ".", DOCS_OWNER)

    gaps = [found[k] for k in _ORDER if k in found]
    return gaps, (gaps[0]["owner"] if gaps else "")
