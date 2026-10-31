"""Islamic finance term expansion.

RFPs and product docs name the same thing differently ("cost-plus sale" vs "Murabaha",
"late fee" vs "gharamah"). Expanding a query with the glossary's synonyms lets keyword
search find the document regardless of which wording the RFP used.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import get_settings


def _norm(term: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", term.lower().replace("'", "")).strip()


@lru_cache
def load_glossary(path: str | None = None) -> list[dict]:
    p = Path(path) if path else get_settings().sample_dir / "glossary.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else []


def expand_terms(query: str, glossary: list[dict] | None = None) -> list[str]:
    """Extra search terms for glossary entries the query mentions, by term or synonym."""
    glossary = load_glossary() if glossary is None else glossary
    q = f" {_norm(query)} "
    extra: list[str] = []
    for entry in glossary:
        names = [entry["term"], *entry.get("synonyms", [])]
        if any(f" {_norm(n)} " in q for n in names if _norm(n)):
            for n in names:
                n = n.replace("'", "").strip()
                if n and n.lower() not in query.lower() and n not in extra:
                    extra.append(n)
    return extra
