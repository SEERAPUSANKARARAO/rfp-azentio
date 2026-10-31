from __future__ import annotations


class LLMError(Exception):
    """Base class for everything the gateway raises."""


class ProviderError(LLMError):
    """A single provider call failed."""


class RateLimitError(ProviderError):
    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class AuthError(ProviderError):
    """Key is invalid or revoked. Retrying with it is pointless."""


class TransientError(ProviderError):
    """Network, timeout or 5xx. Another key or model may succeed."""


class CallTimeout(TransientError):
    """A call outlived its time limit. Unlike a fast 503, this is expensive, so the gateway skips
    the whole model for a while rather than paying the same wait again on every key."""


class BadRequestError(ProviderError):
    """The model rejected this request (unknown model, unsupported option, blocked output)."""


class SchemaError(LLMError):
    """Model output could not be coerced into the requested schema."""


class NoKeysConfigured(LLMError):
    pass


class AllAttemptsFailed(LLMError):
    """Every model in the chain was tried and none gave a usable answer, but none is rate-limited either
    (invalid output, rejected requests, disabled keys). Waiting will not help, so callers must not treat
    this as a quota pause."""

    def __init__(self, message: str, *, attempts: list[str]):
        super().__init__(message)
        self.attempts = attempts


class AllProvidersExhausted(LLMError):
    """Every key/model in the chain is rate-limited, exhausted or failing.

    resume_at is the earliest epoch time at which something may work again,
    or None if nothing will recover on its own (e.g. all keys invalid).
    """

    def __init__(self, message: str, *, resume_at: float | None, attempts: list[str]):
        super().__init__(message)
        self.resume_at = resume_at
        self.attempts = attempts
