from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .. import enums

_STATUS_ALIASES = {
    "complied": enums.COMPLIED, "complies": enums.COMPLIED, "compliant": enums.COMPLIED, "full": enums.COMPLIED,
    "fully compliant": enums.COMPLIED, "fully complied": enums.COMPLIED, "yes": enums.COMPLIED, "c": enums.COMPLIED,
    "partially complied": enums.PARTIAL, "partially complies": enums.PARTIAL, "partial": enums.PARTIAL,
    "partially compliant": enums.PARTIAL, "pc": enums.PARTIAL,
    "not complied": enums.NOT_COMPLIED, "does not comply": enums.NOT_COMPLIED, "non-compliant": enums.NOT_COMPLIED,
    "non compliant": enums.NOT_COMPLIED, "not compliant": enums.NOT_COMPLIED, "none": enums.NOT_COMPLIED,
    "no": enums.NOT_COMPLIED, "nc": enums.NOT_COMPLIED,
    "cannot confirm": enums.CANNOT_CONFIRM, "cannot be confirmed": enums.CANNOT_CONFIRM,
    "unknown": enums.CANNOT_CONFIRM, "insufficient evidence": enums.CANNOT_CONFIRM,
    "unable to confirm": enums.CANNOT_CONFIRM,
}


class Statement(BaseModel):
    """One sentence of the answer, with the sources that support it.

    Asking for statements as structured data, instead of prose with inline [n] markers, makes citation a
    field the model fills in rather than a formatting habit it may forget."""

    text: str
    passages: list[int] = Field(default_factory=list)  # numbers of the passages that support this sentence
    quote: str = ""  # short quote copied word for word from one of those passages


class Claim(BaseModel):
    """Legacy shape, still accepted when a provider returns prose plus a claims list."""

    text: str = ""
    passages: list[int] = Field(default_factory=list)
    quote: str = ""


class DraftOut(BaseModel):
    status: Literal["Complied", "Partially Complied", "Not Complied", "Cannot Confirm"]
    statements: list[Statement] = Field(default_factory=list)
    answer: str = ""  # fallback when a provider returns prose with inline [n] markers instead of statements
    claims: list[Claim] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    reasoning: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _canonical_status(cls, v):
        # Different providers drift ("Complies", "Partial"). Map instead of burning a repair call.
        if isinstance(v, str):
            return _STATUS_ALIASES.get(v.strip().lower(), v.strip())
        return v

    @model_validator(mode="after")
    def _not_empty(self):
        # "Cannot Confirm" legitimately has nothing to cite, so it may be empty (the validator writes the gap
        # sentence). Any other status must say something, or it is malformed and gets repaired.
        if self.status != enums.CANNOT_CONFIRM and not self.statements and not self.answer.strip():
            raise ValueError("a draft needs statements (or an answer)")
        return self
