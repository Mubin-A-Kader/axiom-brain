"""Prompt-injection heuristics.

A lightweight, fast first pass — these patterns catch the bulk of naïve
jailbreaks and document-borne injections without an extra LLM round-trip.
Pair with a model-based classifier for higher recall in production.
"""

from __future__ import annotations

import re

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts)", re.I),
    re.compile(r"disregard\s+(the\s+)?(system|previous)\s+(prompt|instructions)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.I),
    re.compile(r"reveal\s+(your|the)\s+(system\s+)?prompt", re.I),
    re.compile(r"print\s+(your|the)\s+(initial|system)\s+instructions", re.I),
    re.compile(r"</?\s*system\s*>", re.I),
    re.compile(r"\\n\\n(human|assistant|system):", re.I),
]


def detect_prompt_injection(text: str) -> str | None:
    """Return the matched pattern description if injection is detected, else None."""
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return pat.pattern
    return None
