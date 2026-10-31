from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ProviderResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Provider(Protocol):
    """One vendor API. Implementations translate SDK exceptions into the
    errors in llm/errors.py so the gateway never sees vendor-specific types."""

    name: str

    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        system: str,
        prompt: str,
        json_mode: bool,
        temperature: float,
        max_tokens: int,
    ) -> ProviderResponse: ...
