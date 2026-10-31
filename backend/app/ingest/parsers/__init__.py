from __future__ import annotations

from pathlib import Path

from .common import ParsedRequirement, ParsedRfp, ParseError
from .docx import parse_docx
from .pdf import parse_pdf
from .xlsx import parse_xlsx

__all__ = ["parse_rfp", "ParsedRfp", "ParsedRequirement", "ParseError"]

_BY_SUFFIX = {".xlsx": parse_xlsx, ".xlsm": parse_xlsx, ".docx": parse_docx, ".pdf": parse_pdf}


def parse_rfp(path: Path | str) -> ParsedRfp:
    path = Path(path)
    parser = _BY_SUFFIX.get(path.suffix.lower())
    if parser is None:
        raise ParseError(
            f"Unsupported file type '{path.suffix}'. Upload an Excel (.xlsx), Word (.docx) or PDF file."
        )
    return parser(path)
