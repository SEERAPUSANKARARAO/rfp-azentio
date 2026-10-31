from __future__ import annotations

from functools import lru_cache

import yaml

from ..config import Settings, get_settings
from .gateway import LLMGateway, LLMResult, ModelRef
from .keypool import KeyPool
from .providers.gemini import GeminiProvider
from .providers.openai_compat import OpenAICompatProvider
from .store import LlmStore

__all__ = ["LLMGateway", "LLMResult", "build_gateway", "get_gateway"]


def build_gateway(settings: Settings | None = None, store: LlmStore | None = None) -> LLMGateway:
    s = settings or get_settings()
    cfg = yaml.safe_load(s.models_file.read_text())
    store = store or LlmStore(s.db_path.with_name("llm.db"))

    providers = {}
    for name, p in cfg["providers"].items():
        if p["kind"] == "gemini":
            providers[name] = GeminiProvider()
        elif p["kind"] == "openai_compat":
            providers[name] = OpenAICompatProvider(name, p["base_url"])
        else:
            raise ValueError(f"unknown provider kind '{p['kind']}' for {name}")

    pools = {name: KeyPool(name, s.keys_for(name), store) for name in providers}
    chains = {
        task: [
            ModelRef(e["provider"], e["model"], e.get("json_mode", True), e.get("timeout_s"))
            for e in entries
        ]
        for task, entries in cfg["tasks"].items()
    }
    return LLMGateway(chains, pools, providers, store)


@lru_cache
def get_gateway() -> LLMGateway:
    return build_gateway()
