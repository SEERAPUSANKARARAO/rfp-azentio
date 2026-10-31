"""Per-provider pool of API keys with quota-aware rotation.

Quota is tracked per (key, model), not per key: a key that has used up its
daily allowance on one model usually still works on another, so a chain with
two models per provider gives each key two independent budgets.

Only a key-level auth failure (401/403) disables a key outright.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Callable

from .ratelimit import TIMEOUT_COOLDOWN, TRANSIENT_COOLDOWN, classify_rate_limit
from .store import LlmStore


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[:10]


def scrub(message: str, secret: str, limit: int = 300) -> str:
    """Make an error message safe to store and show: no key material, bounded length."""
    return message.replace(secret, "***")[:limit]


@dataclass
class KeyInfo:
    provider: str
    alias: str  # e.g. "gemini#3"; the only thing the UI ever sees
    fp: str
    secret: str = field(repr=False)


@dataclass
class _ModelState:
    blocked_until: float = 0.0
    blocked_kind: str | None = None  # "cooldown" | "exhausted"
    last_tick: int = 0
    last_error: str | None = None


class KeyPool:
    def __init__(
        self,
        provider: str,
        keys: list[str],
        store: LlmStore,
        clock: Callable[[], float] = time.time,
    ):
        self.provider = provider
        self.store = store
        self.clock = clock
        self.keys: list[KeyInfo] = []
        seen: set[str] = set()
        for secret in keys:
            fp = fingerprint(secret)
            if fp in seen:
                continue
            seen.add(fp)
            self.keys.append(KeyInfo(provider, f"{provider}#{len(self.keys) + 1}", fp, secret))
        self._by_fp = {k.fp: k for k in self.keys}
        self._disabled: dict[str, str] = {}
        self._states: dict[tuple[str, str], _ModelState] = {}
        self._tick = 0
        self._load()

    def _load(self) -> None:
        for row in self.store.state_rows(self.provider):
            if row["fp"] not in self._by_fp:
                continue  # key was removed or replaced since last run
            if row["model"] == "":
                if row["disabled"]:
                    self._disabled[row["fp"]] = row["last_error"] or "disabled"
            else:
                self._states[(row["fp"], row["model"])] = _ModelState(
                    blocked_until=row["blocked_until"] or 0.0,
                    blocked_kind=row["blocked_kind"],
                    last_error=row["last_error"],
                )

    def has_keys(self) -> bool:
        return bool(self.keys)

    def _state(self, fp: str, model: str) -> _ModelState:
        return self._states.setdefault((fp, model), _ModelState())

    def acquire(self, model: str, exclude: set[str] | None = None) -> KeyInfo | None:
        """Least-recently-used healthy key for this model, or None if none are usable."""
        now = self.clock()
        exclude = exclude or set()
        best: KeyInfo | None = None
        best_tick = 0
        for k in self.keys:
            if k.fp in exclude or k.fp in self._disabled:
                continue
            st = self._state(k.fp, model)
            if st.blocked_until > now:
                continue
            if best is None or st.last_tick < best_tick:
                best, best_tick = k, st.last_tick
        if best is not None:
            self._tick += 1
            self._state(best.fp, model).last_tick = self._tick
        return best

    def report_success(self, ks: KeyInfo, model: str) -> None:
        st = self._state(ks.fp, model)
        if st.blocked_kind:
            st.blocked_until, st.blocked_kind, st.last_error = 0.0, None, None
            self.store.state_set(self.provider, ks.fp, model)

    def report_rate_limited(
        self, ks: KeyInfo, model: str, message: str, retry_after: float | None
    ) -> tuple[str, float]:
        now = self.clock()
        kind, wait = classify_rate_limit(self.provider, message, retry_after, now)
        st = self._state(ks.fp, model)
        st.blocked_until, st.blocked_kind = now + wait, kind
        st.last_error = scrub(message, ks.secret)
        self.store.state_set(
            self.provider, ks.fp, model,
            blocked_until=st.blocked_until, blocked_kind=kind, last_error=st.last_error,
        )
        return kind, wait

    def _cool(self, ks: KeyInfo, model: str, message: str, seconds: float) -> None:
        st = self._state(ks.fp, model)
        until = self.clock() + seconds
        if st.blocked_until >= until:
            return  # never shorten an existing block, least of all a daily-limit one
        st.blocked_until, st.blocked_kind = until, "cooldown"
        st.last_error = scrub(message, ks.secret)
        self.store.state_set(
            self.provider, ks.fp, model,
            blocked_until=until, blocked_kind="cooldown", last_error=st.last_error,
        )

    def report_transient(self, ks: KeyInfo, model: str, message: str) -> None:
        """A fast 5xx: a short cooldown for this (key, model), not a quota mark."""
        self._cool(ks, model, message, TRANSIENT_COOLDOWN)

    def report_model_timeout(self, model: str, message: str) -> None:
        """A timeout is model-wide trouble: every key sits this model out, not just the one that waited."""
        for k in self.keys:
            if k.fp not in self._disabled:
                self._cool(k, model, message, TIMEOUT_COOLDOWN)

    def report_auth_failure(self, ks: KeyInfo, message: str) -> None:
        msg = scrub(message, ks.secret)
        self._disabled[ks.fp] = msg
        self.store.state_set(self.provider, ks.fp, "", disabled=True, last_error=msg)

    def next_available_at(self, model: str) -> float | None:
        """Earliest time any non-disabled key could serve this model. None if no key ever will."""
        times = [
            self._state(k.fp, model).blocked_until
            for k in self.keys
            if k.fp not in self._disabled
        ]
        return min(times) if times else None

    def reset(self) -> None:
        self._disabled.clear()
        self._states.clear()
        self.store.state_reset(self.provider)

    def snapshot(self, models: list[str]) -> list[dict]:
        now = self.clock()
        out = []
        for k in self.keys:
            disabled = self._disabled.get(k.fp)
            per_model = []
            for m in models:
                st = self._state(k.fp, m)
                if disabled:
                    status, left = "disabled", 0.0
                elif st.blocked_until > now:
                    status, left = st.blocked_kind or "cooldown", st.blocked_until - now
                else:
                    status, left = "ready", 0.0
                per_model.append(
                    {"model": m, "status": status, "seconds_left": round(left),
                     "last_error": st.last_error}
                )
            out.append({"alias": k.alias, "disabled": bool(disabled),
                        "disabled_reason": disabled, "models": per_model})
        return out
