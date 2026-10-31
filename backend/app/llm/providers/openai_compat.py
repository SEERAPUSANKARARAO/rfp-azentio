"""Groq and OpenRouter both speak the OpenAI chat-completions protocol."""

from __future__ import annotations

import time

import httpx
import openai

from ..errors import AuthError, BadRequestError, RateLimitError, TransientError
from .base import ProviderResponse


def _retry_after(e: openai.APIStatusError) -> float | None:
    h = e.response.headers
    ra = h.get("retry-after")
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    # OpenRouter free models send the reset moment as epoch milliseconds.
    reset = h.get("x-ratelimit-reset")
    if reset:
        try:
            return max(0.0, float(reset) / 1000.0 - time.time())
        except ValueError:
            pass
    return None


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, timeout: float = 60.0):
        self.name = name
        self.base_url = base_url
        self.timeout = timeout
        self._clients: dict[str, openai.AsyncOpenAI] = {}

    def _client(self, api_key: str) -> openai.AsyncOpenAI:
        if api_key not in self._clients:
            self._clients[api_key] = openai.AsyncOpenAI(
                api_key=api_key, base_url=self.base_url, max_retries=0, timeout=self.timeout
            )
        return self._clients[api_key]

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
    ) -> ProviderResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict = dict(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            r = await self._client(api_key).chat.completions.create(**kwargs)
        except openai.RateLimitError as e:
            raise RateLimitError(str(e), retry_after=_retry_after(e)) from None
        except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
            raise AuthError(str(e)) from None
        except (openai.APIConnectionError, openai.APITimeoutError, httpx.HTTPError) as e:
            raise TransientError(f"{type(e).__name__}: {e}") from None
        except openai.APIStatusError as e:
            if e.status_code >= 500 or e.status_code in (408, 409):
                raise TransientError(str(e)) from None
            raise BadRequestError(str(e)) from None

        choice = r.choices[0] if r.choices else None
        text = choice.message.content if choice and choice.message else None
        if not text:
            raise BadRequestError("empty response")
        u = r.usage
        return ProviderResponse(
            text=text,
            prompt_tokens=(u.prompt_tokens or 0) if u else 0,
            completion_tokens=(u.completion_tokens or 0) if u else 0,
        )
