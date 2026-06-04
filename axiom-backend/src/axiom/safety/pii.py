"""PII redaction.

Regex-based redaction of the most common identifiers. Good enough as a
default; swap in Presidio or a dedicated NER model for stronger
guarantees on names, addresses, and unusual formats.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    ("PHONE", re.compile(r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{3}\)?[\s\-]?)\d{3}[\s\-]?\d{4}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


def redact_pii(text: str) -> tuple[str, dict[str, int]]:
    """Return ``(redacted_text, counts_by_kind)``."""
    counts: dict[str, int] = {}
    for kind, pat in _PATTERNS:
        new, n = pat.subn(f"[REDACTED_{kind}]", text)
        if n:
            counts[kind] = n
            text = new
    return text, counts
