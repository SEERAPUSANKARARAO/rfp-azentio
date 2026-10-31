"""Gateway behaviour under quota pressure, using scripted fake providers.

No network, no real keys. These are the tests that matter for the daily-limit
constraint: rotate keys, fall through providers, remember exhaustion across a
restart, never leak a key, and never invent output when everything is spent.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from app.llm.errors import (
    AllAttemptsFailed,
    AllProvidersExhausted,
    AuthError,
    BadRequestError,
    NoKeysConfigured,
    RateLimitError,
    TransientError,
)
from app.llm.gateway import LLMGateway, ModelRef, extract_json
from app.llm.keypool import KeyPool
from app.llm.providers.base import ProviderResponse
from app.llm.ratelimit import classify_rate_limit, next_daily_reset, parse_retry_hint
from app.llm.store import LlmStore


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeProvider:
    def __init__(self, script):
        self.script = script
        self.calls: list[tuple[str, str, str]] = []

    async def generate(self, *, api_key, model, system, prompt, json_mode, temperature, max_tokens):
        self.calls.append((api_key, model, prompt))
        out = self.script(api_key, model, prompt, len(self.calls))
        if isinstance(out, Exception):
            raise out
        return ProviderResponse(text=out, prompt_tokens=10, completion_tokens=5)


def make_gateway(spec, chain, store=None, clock=None):
    """spec: {provider: (script, [keys])}; chain: [(provider, model), ...]"""
    store = store or LlmStore()
    clock = clock or Clock()
    providers = {n: FakeProvider(s) for n, (s, _) in spec.items()}
    pools = {n: KeyPool(n, keys, store, clock) for n, (_, keys) in spec.items()}
    chains = {"t": [ModelRef(p, m) for p, m in chain]}
    gw = LLMGateway(chains, pools, providers, store, clock=clock, transient_backoff_s=0)
    return gw, providers, pools, store, clock


def ok(_k, _m, _p, _n):
    return "hello"


# --------------------------------------------------------------- rotation
async def test_rotates_to_next_key_on_429():
    def script(key, model, prompt, n):
        return RateLimitError("slow down", retry_after=30) if key == "k1" else "hello"

    gw, prov, *_ = make_gateway({"p": (script, ["k1", "k2"])}, [("p", "m")])
    r = await gw.complete("t", "one")
    assert r.key_alias == "p#2" and r.text == "hello"
    assert [c[0] for c in prov["p"].calls] == ["k1", "k2"]

    # k1 is cooling down, so the next call goes straight to k2 with no wasted attempt.
    await gw.complete("t", "two")
    assert [c[0] for c in prov["p"].calls] == ["k1", "k2", "k2"]


async def test_cooldown_expires_and_key_returns():
    state = {"limited": True}

    def script(key, model, prompt, n):
        if key == "k1" and state["limited"]:
            return RateLimitError("slow down", retry_after=30)
        return "hello"

    gw, prov, _, _, clock = make_gateway({"p": (script, ["k1", "k2"])}, [("p", "m")])
    await gw.complete("t", "one")
    state["limited"] = False
    clock.t += 31
    seen = {(await gw.complete("t", f"q{i}")).key_alias for i in range(4)}
    assert "p#1" in seen  # k1 is back in rotation


async def test_least_recently_used_spreads_load():
    gw, prov, *_ = make_gateway({"p": (ok, ["k1", "k2", "k3"])}, [("p", "m")])
    for i in range(6):
        await gw.complete("t", f"q{i}")
    used = [c[0] for c in prov["p"].calls]
    assert used == ["k1", "k2", "k3", "k1", "k2", "k3"]


# --------------------------------------------------------------- failover
async def test_falls_through_to_next_provider_when_pool_is_spent():
    limited = lambda k, m, p, n: RateLimitError("limit", retry_after=60)
    gw, prov, *_ = make_gateway(
        {"a": (limited, ["a1", "a2"]), "b": (ok, ["b1"])}, [("a", "m"), ("b", "m")]
    )
    r = await gw.complete("t", "x")
    assert r.provider == "b"
    assert len(prov["a"].calls) == 2  # tried both keys before moving on
    assert any("rate limited" in a for a in r.attempts)


async def test_quota_is_tracked_per_model_not_per_key():
    def script(key, model, prompt, n):
        return RateLimitError("limit", retry_after=3600) if model == "big" else "hello"

    gw, prov, *_ = make_gateway({"p": (script, ["k1"])}, [("p", "big"), ("p", "small")])
    r = await gw.complete("t", "x")
    assert r.model == "small" and r.key_alias == "p#1"


async def test_bad_request_skips_to_next_model_without_burning_other_keys():
    def script(key, model, prompt, n):
        return BadRequestError("model not found") if model == "ghost" else "hello"

    gw, prov, *_ = make_gateway({"p": (script, ["k1", "k2"])}, [("p", "ghost"), ("p", "real")])
    r = await gw.complete("t", "x")
    assert r.model == "real"
    assert sum(1 for c in prov["p"].calls if c[1] == "ghost") == 1


async def test_transient_error_rotates_key():
    def script(key, model, prompt, n):
        return TransientError("503") if key == "k1" else "hello"

    gw, *_ = make_gateway({"p": (script, ["k1", "k2"])}, [("p", "m")])
    assert (await gw.complete("t", "x")).key_alias == "p#2"


async def test_transient_failure_puts_the_pair_on_a_short_cooldown():
    """An overloaded model (503) should not be re-tried on every key by every request."""
    state = {"down": True}

    def script(key, model, prompt, n):
        return TransientError("503 high demand") if key == "k1" and state["down"] else "hello"

    gw, prov, pools, _, clock = make_gateway({"p": (script, ["k1", "k2"])}, [("p", "m")])
    await gw.complete("t", "one")
    assert [c[0] for c in prov["p"].calls] == ["k1", "k2"]

    await gw.complete("t", "two")  # k1 is cooling down: no wasted attempt
    assert [c[0] for c in prov["p"].calls] == ["k1", "k2", "k2"]
    snap = pools["p"].snapshot(["m"])[0]["models"][0]
    assert snap["status"] == "cooldown" and "503" in snap["last_error"]

    state["down"] = False
    clock.t += 31  # past the 30s cooldown
    seen = {(await gw.complete("t", f"q{i}")).key_alias for i in range(4)}
    assert "p#1" in seen


async def test_transient_cooldown_applies_per_model_not_per_key():
    def script(key, model, prompt, n):
        return TransientError("busy") if model == "busy-model" else "hello"

    gw, *_ = make_gateway({"p": (script, ["k1"])}, [("p", "busy-model"), ("p", "other")])
    assert (await gw.complete("t", "x")).model == "other"
    assert (await gw.complete("t", "y")).model == "other"  # same key, still usable on another model


async def test_transient_cooldown_never_shortens_a_daily_limit_block():
    msg = "quota PerDay exceeded"
    gw, _, pools, _, clock = make_gateway({"p": (lambda *a: RateLimitError(msg), ["k1"])}, [("p", "m")])
    with pytest.raises(AllProvidersExhausted):
        await gw.complete("t", "x")
    ks = pools["p"].keys[0]
    before = pools["p"]._state(ks.fp, "m").blocked_until
    pools["p"].report_transient(ks, "m", "timeout")
    after = pools["p"]._state(ks.fp, "m")
    assert after.blocked_until == before and after.blocked_kind == "exhausted"


# --------------------------------------------------------------- persistence
async def test_daily_exhaustion_survives_restart(tmp_path):
    msg = "Quota exceeded for metric GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    script = lambda k, m, p, n: RateLimitError(msg)
    store = LlmStore(tmp_path / "llm.db")
    gw, _, pools, _, clock = make_gateway({"p": (script, ["k1"])}, [("p", "m")], store=store)
    with pytest.raises(AllProvidersExhausted):
        await gw.complete("t", "x")

    # Simulate a process restart: new pool, same database.
    reborn = KeyPool("p", ["k1"], LlmStore(tmp_path / "llm.db"), clock)
    assert reborn.acquire("m") is None
    snap = reborn.snapshot(["m"])[0]["models"][0]
    assert snap["status"] == "exhausted" and snap["seconds_left"] > 60


async def test_disabled_key_survives_restart_and_leaks_nothing(tmp_path):
    secret = "sk-SUPERSECRET-123456"
    script = lambda k, m, p, n: AuthError(f"Incorrect API key provided: {secret}")
    store = LlmStore(tmp_path / "llm.db")
    gw, _, pools, _, clock = make_gateway({"p": (script, [secret])}, [("p", "m")], store=store)
    with pytest.raises(AllAttemptsFailed):  # a rejected key is a failure to report, not quota to wait for
        await gw.complete("t", "x")

    dumped = json.dumps(gw.status()) + json.dumps(store.usage_recent()) + json.dumps(
        store.state_rows("p")
    )
    assert secret not in dumped

    reborn = KeyPool("p", [secret], LlmStore(tmp_path / "llm.db"), clock)
    assert reborn.snapshot(["m"])[0]["disabled"] is True


# --------------------------------------------------------------- exhaustion
async def test_all_exhausted_reports_resume_time_and_invents_nothing():
    limited = lambda k, m, p, n: RateLimitError("limit", retry_after=120)
    gw, _, _, _, clock = make_gateway({"p": (limited, ["k1", "k2"])}, [("p", "m")])
    with pytest.raises(AllProvidersExhausted) as ei:
        await gw.complete("t", "x")
    assert ei.value.resume_at == pytest.approx(clock.t + 120, abs=1)


async def test_no_keys_raises_clear_error():
    gw, *_ = make_gateway({"p": (ok, [])}, [("p", "m")])
    with pytest.raises(NoKeysConfigured):
        await gw.complete("t", "x")


# --------------------------------------------------------------- cache
async def test_cache_hit_skips_provider_and_costs_no_quota():
    gw, prov, *_ = make_gateway({"p": (ok, ["k1"])}, [("p", "m")])
    first = await gw.complete("t", "same prompt")
    second = await gw.complete("t", "same prompt")
    assert second.cached and second.text == first.text
    assert len(prov["p"].calls) == 1
    forced = await gw.complete("t", "same prompt", use_cache=False)
    assert not forced.cached and len(prov["p"].calls) == 2


# --------------------------------------------------------------- schema
class Out(BaseModel):
    x: int


async def test_schema_repair_retry_on_same_key():
    replies = iter(["not json at all", '{"x": 3}'])
    gw, prov, *_ = make_gateway({"p": (lambda *a: next(replies), ["k1"])}, [("p", "m")])
    r = await gw.complete("t", "x", schema=Out)
    assert r.data.x == 3 and len(prov["p"].calls) == 2
    assert "previous reply was not valid" in prov["p"].calls[1][2]


async def test_schema_invalid_after_repair_moves_to_next_model():
    def script(key, model, prompt, n):
        return '{"x": "nope"}' if model == "bad" else '{"x": 7}'

    gw, *_ = make_gateway({"p": (script, ["k1"])}, [("p", "bad"), ("p", "good")])
    r = await gw.complete("t", "x", schema=Out)
    assert r.model == "good" and r.data.x == 7


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here you go: {"a": [1, 2]} hope that helps') == {"a": [1, 2]}
    assert extract_json("[1, 2, 3]") == [1, 2, 3]
    with pytest.raises(ValueError):
        extract_json("no json here")


# --------------------------------------------------------------- status
async def test_status_reports_keys_without_secrets():
    secret = "AIza-REALLY-SECRET"
    gw, *_ = make_gateway({"p": (ok, [secret, "other-secret"])}, [("p", "m")])
    await gw.complete("t", "x")
    st = gw.status()
    assert st["providers"][0]["configured_keys"] == 2
    assert st["providers"][0]["keys"][0]["calls_24h"] == 1
    assert secret not in json.dumps(st) and "other-secret" not in json.dumps(st)


# --------------------------------------------------------------- rate-limit parsing
def test_parse_retry_hint_formats():
    assert parse_retry_hint("Please try again in 1h2m3.5s.") == pytest.approx(3723.5)
    assert parse_retry_hint("Rate limit. Try again in 45.2s") == pytest.approx(45.2)
    assert parse_retry_hint("retry in 12s") == pytest.approx(12)
    assert parse_retry_hint("{'retryDelay': '30s'}") == pytest.approx(30)
    assert parse_retry_hint("something unrelated") is None


def test_classify_daily_vs_minute():
    now = 1_800_000_000.0
    kind, wait = classify_rate_limit("gemini", "quota PerDay exceeded", None, now)
    assert kind == "exhausted" and 5 <= wait <= 26 * 3600
    kind, wait = classify_rate_limit("groq", "tokens per day (TPD) limit; try again in 2h", None, now)
    assert kind == "exhausted" and wait == pytest.approx(7200)
    kind, wait = classify_rate_limit("groq", "requests per minute", None, now)
    assert kind == "cooldown" and wait == 60
    kind, wait = classify_rate_limit("groq", "rpm", 9999, now)
    assert kind == "cooldown" and wait == 900  # a minute-limit never parks a key for long
    kind, wait = classify_rate_limit("groq", "rpm", 0.1, now)
    assert wait == 5  # floor stops a tight retry loop


def test_gemini_resets_at_midnight_pacific():
    now = datetime(2026, 10, 31, 15, 30, tzinfo=ZoneInfo("UTC")).timestamp()
    reset = datetime.fromtimestamp(next_daily_reset("gemini", now), ZoneInfo("America/Los_Angeles"))
    assert (reset.hour, reset.minute) == (0, 0) and reset.day == 1  # Nov 1, 00:00 PT
    other = datetime.fromtimestamp(next_daily_reset("groq", now), ZoneInfo("UTC"))
    assert (other.hour, other.minute) == (0, 0) and other.day == 1


async def test_unusable_output_from_every_model_is_a_failure_not_a_quota_pause():
    """The live bug: invalid drafts on every model were reported as 'quota exhausted' and paused the job."""
    gw, *_ = make_gateway({"p": (lambda *a: "never valid json", ["k1", "k2"])}, [("p", "m1"), ("p", "m2")])
    with pytest.raises(AllAttemptsFailed) as ei:
        await gw.complete("t", "x", schema=Out)
    assert not isinstance(ei.value, AllProvidersExhausted) and "schema invalid" in str(ei.value)


async def test_rejected_requests_on_every_model_are_a_failure_not_a_quota_pause():
    gw, *_ = make_gateway({"p": (lambda *a: BadRequestError("nope"), ["k1"])}, [("p", "m1"), ("p", "m2")])
    with pytest.raises(AllAttemptsFailed):
        await gw.complete("t", "x")


async def test_a_real_rate_limit_is_still_reported_as_exhausted_with_a_resume_time():
    gw, _, _, _, clock = make_gateway({"p": (lambda *a: RateLimitError("slow", retry_after=90), ["k1"])}, [("p", "m")])
    with pytest.raises(AllProvidersExhausted) as ei:
        await gw.complete("t", "x")
    assert ei.value.resume_at == pytest.approx(clock.t + 90, abs=1)
