"""List the models each configured key can actually use, so models.yaml can be
checked against reality instead of memory. Free-tier model IDs change often.

    cd rfp-ai/backend && .venv/bin/python ../scripts/list_models.py

Reads keys from rfp-ai/.env. Prints model IDs only, never keys. Uses the first
key of each provider, so it costs one cheap list call per provider.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import argparse  # noqa: E402
import asyncio  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402
import yaml  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.llm.errors import AuthError, BadRequestError, RateLimitError, TransientError  # noqa: E402
from app.llm.providers.gemini import GeminiProvider  # noqa: E402
from app.llm.providers.openai_compat import OpenAICompatProvider  # noqa: E402


async def probe_once(provider: str, model: str, key: str, base_url: str | None, json_mode: bool) -> str:
    """One tiny real generation. A model can be LISTED yet unusable (retired, or closed to new
    users), so only a real call proves a models.yaml entry works."""
    p = GeminiProvider() if provider == "gemini" else OpenAICompatProvider(provider, base_url or "")
    t0 = time.perf_counter()
    try:
        r = await asyncio.wait_for(
            p.generate(api_key=key, model=model, system="", prompt='Reply with the JSON {"ok": true}',
                       json_mode=json_mode, temperature=0.0, max_tokens=512),
            timeout=60,
        )
        return f"ok ({time.perf_counter() - t0:.1f}s, {r.completion_tokens} tokens out)"
    except RateLimitError:
        return "rate limited right now (busy or quota; the key itself is fine)"
    except AuthError:
        return "KEY REJECTED"
    except BadRequestError as e:
        return f"MODEL REJECTED: {str(e).replace(key, '***')[:140]}"
    except (TransientError, asyncio.TimeoutError) as e:
        return f"slow or unavailable ({type(e).__name__}) after {time.perf_counter() - t0:.0f}s"


def gemini(key: str) -> list[str]:
    r = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": key, "pageSize": 200}, timeout=30,
    )
    r.raise_for_status()
    return sorted(
        m["name"].removeprefix("models/")
        for m in r.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    )


def groq(key: str) -> list[str]:
    r = httpx.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {key}"}, timeout=30,
    )
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))


def openrouter(key: str) -> tuple[list[str], str]:
    headers = {"Authorization": f"Bearer {key}"}
    # /models is public and answers even for a bad key, so validate the key against its own
    # info endpoint first. The label is not printed: it contains a fragment of the key.
    info = httpx.get("https://openrouter.ai/api/v1/key", headers=headers, timeout=30)
    if info.status_code == 404:
        info = httpx.get("https://openrouter.ai/api/v1/auth/key", headers=headers, timeout=30)
    info.raise_for_status()
    data = info.json().get("data", {})
    note = f", {'FREE TIER' if data.get('is_free_tier') else 'has credits'}, usage {data.get('usage')}"
    r = httpx.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=30)
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []) if m["id"].endswith(":free")), note


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--probe", action="store_true",
                    help="also make one tiny real call per configured model (uses a little quota)")
    ap.add_argument("--all-keys", action="store_true", help="with --probe, test every key, not just the first working one")
    args = ap.parse_args()

    s = get_settings()
    cfg = yaml.safe_load(s.models_file.read_text())
    wanted: dict[str, set[str]] = {}
    json_mode: dict[tuple[str, str], bool] = {}
    for entries in cfg["tasks"].values():
        for e in entries:
            wanted.setdefault(e["provider"], set()).add(e["model"])
            json_mode[(e["provider"], e["model"])] = e.get("json_mode", True)

    fetchers = {"gemini": gemini, "groq": groq, "openrouter": openrouter}
    bad = 0
    for provider, fn in fetchers.items():
        keys = s.keys_for(provider)
        if not keys:
            print(f"[{provider}] no keys configured, skipped")
            continue

        # Check every key on its own so one bad key is easy to find. Only aliases are printed.
        available: list[str] | None = None
        working: list[int] = []
        print(f"\n[{provider}] {len(keys)} key(s)")
        for i, key in enumerate(keys, 1):
            alias = f"{provider}#{i}"
            try:
                res = fn(key)
                models, note = res if isinstance(res, tuple) else (res, "")
                print(f"  {alias}: ok ({len(models)} models listed{note})")
                working.append(i)
                available = available if available is not None else models
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                why = {401: "rejected (invalid key)", 403: "rejected (forbidden)", 400: "rejected (invalid key?)",
                       429: "rate limited right now"}.get(code, f"HTTP {code}")
                print(f"  {alias}: FAILED, {why}")
                bad += 1
            except httpx.HTTPError as e:
                print(f"  {alias}: FAILED, {type(e).__name__} (network)")
                bad += 1
        if available is None:
            print(f"  no working key for {provider}, model check skipped")
            continue
        print(f"  {len(available)} usable models; checking models.yaml against them:")
        for m in sorted(wanted.get(provider, [])):
            mark = "ok     " if m in available else "MISSING"
            bad += m not in available
            print(f"  {mark} {m}  (used in models.yaml)")
        extra = [m for m in available if m not in wanted.get(provider, set())]
        print("  other options:", ", ".join(extra[:25]) + (" ..." if len(extra) > 25 else ""))

        if args.probe:
            print("  real call per configured model:")
            for m in sorted(wanted.get(provider, [])):
                for i in (working if args.all_keys else working[:1]):
                    out = asyncio.run(probe_once(
                        provider, m, keys[i - 1], cfg["providers"][provider].get("base_url"),
                        json_mode[(provider, m)]))
                    bad += not out.startswith(("ok", "rate limited"))
                    print(f"    {provider}#{i} {m}: {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
