"""One entry point for every LLM call in the app.

    result = await gateway.complete("draft", prompt, system=..., schema=DraftOut)

The gateway picks a model chain by task, rotates keys inside each provider
when one is rate-limited, falls through to the next provider when a whole
pool is spent, validates JSON against a pydantic schema (with one repair
retry), and caches by prompt so repeat runs cost no quota.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from .errors import (
    AllAttemptsFailed,
    AllProvidersExhausted,
    AuthError,
    BadRequestError,
    CallTimeout,
    NoKeysConfigured,
    ProviderError,
    RateLimitError,
    TransientError,
)
from .keypool import KeyInfo, KeyPool, scrub
from .providers.base import Provider, ProviderResponse
from .store import LlmStore


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str
    json_mode: bool = True
    timeout_s: float | None = None  # per-call limit; None uses the gateway default


@dataclass
class LLMResult:
    text: str
    data: Any  # validated pydantic instance when a schema was given
    provider: str
    model: str
    key_alias: str
    cached: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    attempts: list[str] = field(default_factory=list)


class _NextKey:
    pass


class _NextModel:
    pass


def extract_json(text: str) -> Any:
    """Parse model output as JSON, tolerating code fences and surrounding prose."""
    t = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S | re.I)
    if fence:
        t = fence.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in model output")
    obj, _ = json.JSONDecoder().raw_decode(t[min(starts):])
    return obj


def _with_schema(system: str, schema: type[BaseModel]) -> str:
    spec = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    return (
        f"{system}\n\nReturn ONLY one JSON object, no prose and no code fences, "
        f"that validates against this JSON Schema:\n{spec}"
    ).strip()


class LLMGateway:
    def __init__(
        self,
        chains: dict[str, list[ModelRef]],
        pools: dict[str, KeyPool],
        providers: dict[str, Provider],
        store: LlmStore,
        *,
        clock: Callable[[], float] = time.time,
        timeout_s: float = 90.0,
        transient_backoff_s: float = 0.5,
    ):
        self.chains = chains
        self.pools = pools
        self.providers = providers
        self.store = store
        self.clock = clock
        self.timeout_s = timeout_s
        self.transient_backoff_s = transient_backoff_s

    # ------------------------------------------------------------------
    @staticmethod
    def _cache_key(task: str, system: str, prompt: str, temperature: float) -> str:
        blob = json.dumps([task, system, prompt, temperature], ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _log(self, ks: KeyInfo, ref: ModelRef, task: str, status: str, t0: float,
             resp: ProviderResponse | None = None, error: str | None = None) -> None:
        self.store.usage_add(
            provider=ref.provider, key_alias=ks.alias, model=ref.model, task=task,
            status=status,
            prompt_tokens=resp.prompt_tokens if resp else 0,
            completion_tokens=resp.completion_tokens if resp else 0,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error=scrub(error, ks.secret) if error else None,
            ts=self.clock(),
        )

    async def _generate(self, ks: KeyInfo, ref: ModelRef, task: str, system: str,
                        prompt: str, temperature: float, max_tokens: int) -> ProviderResponse:
        t0 = time.perf_counter()
        provider = self.providers[ref.provider]
        pool = self.pools[ref.provider]
        try:
            resp = await asyncio.wait_for(
                provider.generate(
                    api_key=ks.secret, model=ref.model, system=system, prompt=prompt,
                    json_mode=ref.json_mode, temperature=temperature, max_tokens=max_tokens,
                ),
                timeout=ref.timeout_s or self.timeout_s,
            )
        except asyncio.TimeoutError:
            pool.report_model_timeout(ref.model, "timeout")
            self._log(ks, ref, task, "timeout", t0, error="timeout")
            raise CallTimeout("timeout") from None
        except RateLimitError as e:
            kind, wait = pool.report_rate_limited(ks, ref.model, str(e), e.retry_after)
            self._log(ks, ref, task, kind, t0, error=str(e))
            raise
        except AuthError as e:
            pool.report_auth_failure(ks, str(e))
            self._log(ks, ref, task, "auth", t0, error=str(e))
            raise
        except TransientError as e:
            pool.report_transient(ks, ref.model, str(e))
            self._log(ks, ref, task, "transient", t0, error=str(e))
            raise
        except BadRequestError as e:
            self._log(ks, ref, task, "bad_request", t0, error=str(e))
            raise
        pool.report_success(ks, ref.model)
        self._log(ks, ref, task, "ok", t0, resp=resp)
        return resp

    # ------------------------------------------------------------------
    async def complete(
        self,
        task: str,
        prompt: str,
        *,
        system: str = "",
        schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        use_cache: bool = True,
    ) -> LLMResult:
        if task not in self.chains:
            raise KeyError(f"unknown LLM task '{task}'")
        chain = self.chains[task]
        if not any(self.pools[r.provider].has_keys() for r in chain if r.provider in self.pools):
            raise NoKeysConfigured(
                "No API keys configured. Add GEMINI_API_KEYS / GROQ_API_KEYS / "
                "OPENROUTER_API_KEYS to rfp-ai/.env"
            )

        full_system = _with_schema(system, schema) if schema else system
        ckey = self._cache_key(task, full_system, prompt, temperature)
        if use_cache:
            hit = self.store.cache_get(ckey)
            if hit:
                try:
                    data = schema.model_validate(extract_json(hit["text"])) if schema else None
                    return LLMResult(
                        text=hit["text"], data=data, provider=hit["provider"],
                        model=hit["model"], key_alias="cache", cached=True,
                        prompt_tokens=hit["prompt_tokens"] or 0,
                        completion_tokens=hit["completion_tokens"] or 0,
                    )
                except (ValueError, ValidationError):
                    pass  # stale or corrupt cache entry; fall through to a live call

        attempts: list[str] = []
        for ref in chain:
            pool = self.pools.get(ref.provider)
            if pool is None or not pool.has_keys():
                attempts.append(f"{ref.provider}/{ref.model}: no keys")
                continue
            tried: set[str] = set()
            while (ks := pool.acquire(ref.model, exclude=tried)) is not None:
                tried.add(ks.fp)
                out = await self._attempt(
                    ks, ref, task, full_system, prompt, schema, temperature, max_tokens, attempts
                )
                if isinstance(out, _NextKey):
                    continue
                if isinstance(out, _NextModel):
                    break
                self.store.cache_put(
                    ckey, out.text, out.provider, out.model, out.prompt_tokens,
                    out.completion_tokens,
                )
                out.attempts = attempts
                return out

        resume_at = self._resume_at(chain)
        if resume_at is None:  # nothing is waiting on a clock, so this is a failure, not a quota pause
            raise AllAttemptsFailed("No model returned a usable answer: " + "; ".join(attempts[-4:]), attempts=attempts)
        when = datetime.fromtimestamp(resume_at).strftime("%H:%M")
        raise AllProvidersExhausted(
            "All LLM providers are rate-limited or unavailable"
            + (f"; earliest retry about {when}" if when else ""),
            resume_at=resume_at,
            attempts=attempts,
        )

    async def _attempt(
        self, ks: KeyInfo, ref: ModelRef, task: str, system: str, prompt: str,
        schema: type[BaseModel] | None, temperature: float, max_tokens: int,
        attempts: list[str],
    ) -> LLMResult | _NextKey | _NextModel:
        tag = f"{ks.alias}/{ref.model}"
        t0 = time.perf_counter()
        try:
            resp = await self._generate(ks, ref, task, system, prompt, temperature, max_tokens)
        except RateLimitError:
            attempts.append(f"{tag}: rate limited")
            return _NextKey()
        except AuthError:
            attempts.append(f"{tag}: auth failed, key disabled")
            return _NextKey()
        except CallTimeout:
            attempts.append(f"{tag}: timed out, model skipped on every key for a while")
            return _NextModel()
        except TransientError as e:
            attempts.append(f"{tag}: transient ({scrub(str(e), ks.secret, 80)})")
            if self.transient_backoff_s:
                await asyncio.sleep(self.transient_backoff_s)
            return _NextKey()
        except BadRequestError as e:
            attempts.append(f"{tag}: rejected ({scrub(str(e), ks.secret, 120)})")
            return _NextModel()

        data = None
        if schema is not None:
            try:
                data = schema.model_validate(extract_json(resp.text))
            except (ValueError, ValidationError) as first:
                repair = (
                    f"{prompt}\n\n---\nYour previous reply was not valid ({str(first)[:400]}).\n"
                    f"Previous reply:\n{resp.text[:3000]}\n\nReply again with corrected JSON only."
                )
                try:
                    resp = await self._generate(
                        ks, ref, task, system, repair, temperature, max_tokens
                    )
                    data = schema.model_validate(extract_json(resp.text))
                    attempts.append(f"{tag}: schema repaired")
                except (ValueError, ValidationError):
                    attempts.append(f"{tag}: schema invalid after repair")
                    return _NextModel()
                except RateLimitError:
                    attempts.append(f"{tag}: rate limited during repair")
                    return _NextKey()
                except ProviderError as e:
                    attempts.append(f"{tag}: failed during repair ({scrub(str(e), ks.secret, 80)})")
                    return _NextModel()

        return LLMResult(
            text=resp.text, data=data, provider=ref.provider, model=ref.model,
            key_alias=ks.alias, prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            latency_ms=int((time.perf_counter() - t0) * 1000), attempts=attempts,
        )

    def _resume_at(self, chain: list[ModelRef]) -> float | None:
        """When the earliest rate-limited (key, model) comes back, or None if nothing is waiting on a clock."""
        now = self.clock()
        times = []
        for ref in chain:
            pool = self.pools.get(ref.provider)
            if pool and pool.has_keys():
                t = pool.next_available_at(ref.model)
                if t is not None and t > now:
                    times.append(t)
        return min(times) if times else None

    # ------------------------------------------------------------------
    def status(self) -> dict:
        """Everything the Settings > quota panel shows. Never includes key material."""
        since = self.clock() - 24 * 3600
        usage = self.store.usage_summary(since)
        models_by_provider: dict[str, list[str]] = {}
        for refs in self.chains.values():
            for r in refs:
                lst = models_by_provider.setdefault(r.provider, [])
                if r.model not in lst:
                    lst.append(r.model)
        providers = []
        for name, pool in self.pools.items():
            keys = pool.snapshot(models_by_provider.get(name, []))
            for k in keys:
                rows = [u for u in usage if u["key_alias"] == k["alias"]]
                k["calls_24h"] = sum(u["calls"] for u in rows)
                k["ok_24h"] = sum(u["calls"] for u in rows if u["status"] == "ok")
                k["tokens_24h"] = sum(u["prompt_tokens"] + u["completion_tokens"] for u in rows)
            providers.append({"provider": name, "configured_keys": len(pool.keys), "keys": keys})
        return {
            "providers": providers,
            "tasks": {t: [{"provider": r.provider, "model": r.model} for r in refs]
                      for t, refs in self.chains.items()},
            "cache_entries": self.store.cache_count(),
        }
