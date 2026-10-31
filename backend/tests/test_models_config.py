"""models.yaml is configuration the app cannot run without, and a slow free-tier model must not
be able to stall a request, so both get tests that need no network and no real keys."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import yaml

from app.config import Settings
from app.llm import build_gateway
from app.llm.gateway import LLMGateway, ModelRef
from app.llm.keypool import KeyPool
from app.llm.providers.base import ProviderResponse
from app.llm.store import LlmStore

ROOT = Path(__file__).resolve().parents[2]


def no_keys() -> Settings:
    # explicit empty values win over whatever is in the developer's real .env
    return Settings(gemini_api_keys="", groq_api_keys="", openrouter_api_keys="")


def test_models_yaml_is_wellformed():
    cfg = yaml.safe_load((ROOT / "models.yaml").read_text())
    assert set(cfg["tasks"]) == {"structure", "draft", "fast"}
    for task, entries in cfg["tasks"].items():
        assert entries, task
        pairs = [(e["provider"], e["model"]) for e in entries]
        assert len(pairs) == len(set(pairs)), f"duplicate model in {task}"
        assert all(p in cfg["providers"] for p, _ in pairs), task
        assert all(isinstance(e.get("timeout_s", 1), (int, float)) for e in entries), task


def test_every_task_has_a_groq_entry_so_the_app_runs_on_groq_alone():
    gw = build_gateway(no_keys(), LlmStore())
    for task, chain in gw.chains.items():
        assert any(r.provider == "groq" for r in chain), task


def test_openrouter_entries_carry_a_timeout_and_the_slow_tier_is_last_resort():
    gw = build_gateway(no_keys(), LlmStore())
    for task, chain in gw.chains.items():
        slow = [r for r in chain if r.provider == "openrouter"]
        assert slow and all(r.timeout_s and r.timeout_s <= 60 for r in slow), task
        assert chain[-1].provider == "openrouter", f"{task}: free OpenRouter must be the last resort"


class _Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


async def test_timeout_skips_the_whole_model_instead_of_waiting_on_every_key():
    """A stalled model costs one wait, not one wait per key, and is then skipped for a while."""

    class Provider:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        async def generate(self, *, api_key, model, system, prompt, json_mode, temperature, max_tokens):
            self.calls.append((api_key, model))
            if model == "stalled":
                await asyncio.sleep(5)  # a congested model that accepts the call and never answers
            return ProviderResponse(text="hello")

    store, prov, clock = LlmStore(), Provider(), _Clock()
    gw = LLMGateway(
        {"t": [ModelRef("p", "stalled", True, timeout_s=0.05), ModelRef("p", "healthy")]},
        {"p": KeyPool("p", ["k1", "k2", "k3"], store, clock)}, {"p": prov}, store,
        clock=clock, timeout_s=90, transient_backoff_s=0,
    )
    stalled_calls = lambda: sum(1 for _, m in prov.calls if m == "stalled")  # noqa: E731

    t0 = time.perf_counter()
    r = await gw.complete("t", "x")
    assert r.model == "healthy" and stalled_calls() == 1  # one wait, not three
    assert time.perf_counter() - t0 < 2  # not the 90s default, not the provider's 5s sleep
    assert any("timed out" in a for a in r.attempts)

    # every key sits the stalled model out, so later requests go straight to the healthy one
    snaps = gw.pools["p"].snapshot(["stalled"])
    assert all(k["models"][0]["status"] == "cooldown" and k["models"][0]["seconds_left"] >= 100 for k in snaps)
    t1 = time.perf_counter()
    await gw.complete("t", "y")
    assert stalled_calls() == 1 and time.perf_counter() - t1 < 0.5

    clock.t += 121  # cooldown over: the model gets another chance
    await gw.complete("t", "z")
    assert stalled_calls() == 2


def test_gemini_client_has_a_hard_http_timeout():
    """The SDK's async client blocks a worker thread and defaults to no timeout, so a stalled call
    would pin the thread. The backstop makes the HTTP request itself end."""
    from app.llm.providers.gemini import GeminiProvider

    client = GeminiProvider(timeout_s=12)._client("not-a-real-key")
    assert client._api_client._http_options["timeout"] == 12000
    assert GeminiProvider()._client("another-fake-key")._api_client._http_options["timeout"] == 95000


async def test_without_a_per_model_timeout_the_gateway_default_applies():
    class Slow:
        async def generate(self, **kw):
            await asyncio.sleep(0.3)
            return ProviderResponse(text="late")

    store = LlmStore()
    gw = LLMGateway({"t": [ModelRef("p", "m")]}, {"p": KeyPool("p", ["k1"], store)}, {"p": Slow()}, store,
                    timeout_s=0.05, transient_backoff_s=0)
    try:
        await gw.complete("t", "x")
    except Exception as e:  # noqa: BLE001
        assert type(e).__name__ == "AllProvidersExhausted"
    else:
        raise AssertionError("expected the 0.05s default timeout to fire")
