from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..llm import get_gateway

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "keys_configured": {p: len(s.keys_for(p)) for p in ("gemini", "groq", "openrouter")},
        "access_key_required": bool(s.app_access_key),  # lets the app ask for the key before anything else fails
    }


@router.get("/llm/status")
def llm_status() -> dict:
    """Quota panel data: per key and model status, 24h usage, task chains. No key material."""
    return get_gateway().status()


@router.post("/llm/reset-keys")
def llm_reset_keys() -> dict:
    """Forget cooldown/exhausted/disabled state, e.g. after replacing a bad key."""
    gw = get_gateway()
    for pool in gw.pools.values():
        pool.reset()
    return {"ok": True}
