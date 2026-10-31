"""Is a source too old to trust without an SME looking at it?

Staleness is rule-based on purpose. It must not depend on a model's opinion:
an answer built on a 2024 document should be flagged every time, for the same reason.
"""

from __future__ import annotations

import calendar
from datetime import date

from .config import get_settings


def today() -> date:
    return get_settings().as_of_date or date.today()


def months_between(earlier: date, later: date) -> int:
    """Whole months from earlier to later. For display; the stale test uses a cutoff date."""
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    return months - (1 if later.day < earlier.day else 0)


def shift_months(d: date, months: int) -> date:
    """d moved by a number of calendar months, clamping the day (31 Oct - 1 month = 30 Sep)."""
    year, month0 = divmod(d.year * 12 + (d.month - 1) + months, 12)
    month = month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _major(version: str) -> int | None:
    head = version.strip().lstrip("vV").split(".")[0]
    return int(head) if head.isdigit() else None


def staleness_reasons(
    doc_date: date | None,
    product_version: str | None,
    status: str,
    *,
    as_of: date | None = None,
    stale_months: int | None = None,
    current_version: str | None = None,
) -> list[str]:
    """Reasons a document is stale. Empty list means it is fresh."""
    s = get_settings()
    as_of = as_of or today()
    stale_months = s.stale_months if stale_months is None else stale_months
    current_version = current_version or s.current_product_version

    reasons: list[str] = []
    if status == "deprecated":
        reasons.append("marked deprecated")
    # Exact cutoff, not floored whole months: a doc 18 months and 2 days old is over the limit.
    if doc_date is not None and doc_date < shift_months(as_of, -stale_months):
        age = months_between(doc_date, as_of)
        reasons.append(f"dated {doc_date.isoformat()}, about {age} months old (limit {stale_months})")
    if product_version:
        doc_major, cur_major = _major(product_version), _major(current_version)
        if doc_major is not None and cur_major is not None and doc_major < cur_major:
            reasons.append(f"describes product v{product_version}, current is v{current_version}")
    return reasons
