from __future__ import annotations

import json

import httpx
from google import genai
from google.genai import errors, types

from ..errors import AuthError, BadRequestError, RateLimitError, TransientError
from .base import ProviderResponse


def _map(e: errors.APIError) -> Exception:
    code = getattr(e, "code", 0) or 0
    # details carries the quota metric name (e.g. ...PerDayPerProjectPerModel...),
    # which is how the gateway tells a daily limit from a per-minute one.
    try:
        details = json.dumps(getattr(e, "details", None))[:1500]
    except (TypeError, ValueError):
        details = ""
    msg = f"{getattr(e, 'status', '')}: {getattr(e, 'message', '')} {details}".strip()
    if code == 429:
        return RateLimitError(msg)
    if code in (401, 403) or "API key not valid" in msg or "API_KEY_INVALID" in msg:
        return AuthError(msg)
    if code in (408, 499) or code >= 500:
        return TransientError(msg)
    return BadRequestError(msg)


class GeminiProvider:
    name = "gemini"

    def __init__(self, timeout_s: float = 95.0) -> None:
        # This SDK's "async" client runs a blocking HTTP call in a worker thread, and its default
        # timeout is None. asyncio.wait_for can abandon such a call but not stop the thread, so a
        # stalled request would pin a thread (and delay shutdown) forever. This backstop makes the
        # request itself end, just after the gateway's own, shorter, per-call timeout has fired.
        self.timeout_s = timeout_s
        self._clients: dict[str, genai.Client] = {}

    def _client(self, api_key: str) -> genai.Client:
        if api_key not in self._clients:
            self._clients[api_key] = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)),
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
        config = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else None,
        )
        try:
            r = await self._client(api_key).aio.models.generate_content(
                model=model, contents=prompt, config=config
            )
        except errors.APIError as e:
            raise _map(e) from None
        except (httpx.HTTPError, OSError) as e:
            raise TransientError(f"{type(e).__name__}: {e}") from None

        text = r.text
        if not text:
            raise BadRequestError("empty or blocked response")
        um = r.usage_metadata
        return ProviderResponse(
            text=text,
            prompt_tokens=(um.prompt_token_count or 0) if um else 0,
            completion_tokens=(um.candidates_token_count or 0) if um else 0,
        )
