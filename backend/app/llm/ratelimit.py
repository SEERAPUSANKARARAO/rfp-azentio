"""Turn a provider's 429 into "how long should this (key, model) sit out?".

Free tiers have two very different limits. A per-minute limit clears in
seconds; a per-day limit will not clear until the provider's reset. We tell
them apart from the error text so one burst does not park a key for a day,
and one exhausted key does not get hammered every few seconds.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_DAILY = re.compile(r"per[\s_-]?day|daily|PerDay|\bRPD\b|\bTPD\b|free-models-per-day", re.I)
_TOKEN = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

MIN_COOLDOWN = 5.0
DEFAULT_COOLDOWN = 60.0
# A timeout or 503 usually means the model is overloaded. Sit it out briefly so the next
# request goes straight to a healthy option instead of paying for the same failure again.
TRANSIENT_COOLDOWN = 30.0
# A call that timed out is far more costly than a fast 503, and stalls tend to last, so back off longer.
TIMEOUT_COOLDOWN = 120.0
MAX_MINUTE_COOLDOWN = 900.0
MAX_BLOCK = 26 * 3600.0


def parse_retry_hint(message: str) -> float | None:
    """Pull '12.5s', '1h2m3s', 'retryDelay: 30s' style hints out of an error body."""
    m = re.search(
        r"(?:try again|retry)\s*(?:in|after)\s*((?:\d+(?:\.\d+)?(?:ms|h|m|s)\s*)+)",
        message,
        re.I,
    ) or re.search(
        r"retryDelay['\"]?\s*:\s*['\"]?((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)", message
    )
    if not m:
        return None
    total = sum(float(v) * _UNIT[u] for v, u in _TOKEN.findall(m.group(1)))
    return total if total > 0 else None


def next_daily_reset(provider: str, now: float) -> float:
    """Next quota reset boundary. Gemini resets at midnight Pacific, others at UTC midnight."""
    tz = ZoneInfo("America/Los_Angeles") if provider == "gemini" else timezone.utc
    local = datetime.fromtimestamp(now, tz)
    nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def classify_rate_limit(
    provider: str, message: str, retry_after: float | None, now: float
) -> tuple[str, float]:
    """Return ("exhausted" | "cooldown", seconds to wait)."""
    hint = retry_after if retry_after is not None else parse_retry_hint(message)
    if _DAILY.search(message):
        wait = hint if hint is not None else next_daily_reset(provider, now) - now
        return "exhausted", max(MIN_COOLDOWN, min(wait, MAX_BLOCK))
    wait = hint if hint is not None else DEFAULT_COOLDOWN
    return "cooldown", max(MIN_COOLDOWN, min(wait, MAX_MINUTE_COOLDOWN))
