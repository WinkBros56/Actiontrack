"""Task normalisation and similarity (SPEC 6.1)."""

from __future__ import annotations

import re

MERGE_THRESHOLD = 0.85
NEAR_MISS_THRESHOLD = 0.5

_ARTICLES = {"a", "an", "the"}


def tokens(task: str) -> frozenset[str]:
    """Lowercase, drop apostrophes, punctuation and articles; return the token set."""
    t = task.lower().replace("'", "").replace("’", "")
    return frozenset(w for w in re.findall(r"[a-z0-9]+", t) if w not in _ARTICLES)


def norm_task(toks: frozenset[str]) -> str:
    return " ".join(sorted(toks))


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two token sets."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)
