from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from axiom.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FailureContext — structured input to the router
# ---------------------------------------------------------------------------

@dataclass
class FailureContext:
    """
    A focused snapshot of the current failure, extracted from SQLAgentState.

    Keeps RecoveryPattern conditions decoupled from the full 40-field state
    TypedDict — patterns receive only what they need to make a decision.
    """
    error: str                          # current error message (empty string if none)
    attempts: int                       # number of SQL generation attempts so far
    has_sql_result: bool                # True if a successful result already exists
    sql_query: str                      # the query that failed (empty string if none)
    prior_errors: list[str] = field(default_factory=list)  # error_log from state

    @property
    def repeat_count(self) -> int:
        """How many times the same error class has appeared in prior_errors."""
        if not self.error:
            return 0
        error_class = _classify_error(self.error)
        return sum(1 for e in self.prior_errors if _classify_error(e) == error_class)

    @classmethod
    def from_state(cls, state: dict) -> "FailureContext":
        return cls(
            error=state.get("error") or "",
            attempts=state.get("attempts", 0),
            has_sql_result=bool(state.get("sql_result")),
            sql_query=state.get("sql_query") or "",
            prior_errors=list(state.get("error_log") or []),
        )


def _classify_error(error: str) -> str:
    """Bucket an error message into a coarse class for repeat-detection."""
    e = error.lower()
    if "zero_results" in e:
        return "zero_results"
    if ("relation" in e or "table" in e) and "does not exist" in e:
        return "schema_missing"
    if "undefined table" in e or "unknown table" in e:
        return "schema_missing"
    if "permission denied" in e or "access denied" in e:
        return "permission_denied"
    if "exhausted maximum" in e:
        return "exhausted"
    if "syntax error" in e or "parse error" in e:
        return "syntax"
    if "timeout" in e or "timed out" in e:
        return "timeout"
    if "connection" in e and ("refused" in e or "reset" in e or "closed" in e):
        return "connection"
    return "generic"


# ---------------------------------------------------------------------------
# RecoveryStrategy
# ---------------------------------------------------------------------------

class RecoveryStrategy(str, Enum):
    VISUALIZE = "visualize"
    CRITIC = "critic"
    DISCOVERY = "discovery"
    SYNTHESIZE = "synthesize_response"


# ---------------------------------------------------------------------------
# RecoveryPattern
# ---------------------------------------------------------------------------

@dataclass
class RecoveryPattern:
    """A single routing rule evaluated against a FailureContext."""
    name: str
    strategy: RecoveryStrategy
    condition: Callable[[FailureContext], bool]
    priority: int = 0  # lower = evaluated first


# ---------------------------------------------------------------------------
# RecoveryRouter
# ---------------------------------------------------------------------------

class RecoveryRouter:
    """
    Data-driven router that maps a FailureContext to a RecoveryStrategy.

    Patterns are evaluated in priority order (lowest number first). The first
    matching pattern wins. New failure modes are registered via .register()
    without touching graph.py.

    Key behaviours vs the old _should_correct() string matcher:
      - Receives a typed FailureContext, not the raw 40-field state dict.
      - Uses repeat_count to escalate when the same error class recurs.
      - Schema-missing errors always route to discovery on attempt 1,
        then to critic on repeat (instead of silently falling to generic_error).
      - Timeout and connection errors route to synthesize immediately
        (retrying a DB that's down wastes quota).
    """

    def __init__(self) -> None:
        self._patterns: list[RecoveryPattern] = []
        self._register_defaults()

    def register(self, pattern: RecoveryPattern) -> None:
        self._patterns.append(pattern)
        self._patterns.sort(key=lambda p: p.priority)

    def route(self, state: dict) -> str:
        ctx = FailureContext.from_state(state)
        for pattern in self._patterns:
            if pattern.condition(ctx):
                logger.debug(
                    "RecoveryRouter matched '%s' (attempts=%d, repeat=%d) → %s",
                    pattern.name, ctx.attempts, ctx.repeat_count, pattern.strategy,
                )
                return pattern.strategy.value
        return RecoveryStrategy.SYNTHESIZE.value

    def _register_defaults(self) -> None:
        # ── Success ──────────────────────────────────────────────────────────
        self.register(RecoveryPattern(
            name="sql_result_ok",
            priority=0,
            strategy=RecoveryStrategy.VISUALIZE,
            condition=lambda ctx: ctx.has_sql_result,
        ))

        # ── Hard stops (retry would waste quota / never recover) ─────────────
        self.register(RecoveryPattern(
            name="attempts_exhausted",
            priority=10,
            strategy=RecoveryStrategy.SYNTHESIZE,
            condition=lambda ctx: ctx.attempts >= settings.max_correction_attempts,
        ))
        self.register(RecoveryPattern(
            name="exhausted_correction_signal",
            priority=11,
            strategy=RecoveryStrategy.SYNTHESIZE,
            condition=lambda ctx: "Exhausted maximum SQL correction" in ctx.error,
        ))
        self.register(RecoveryPattern(
            name="permission_denied",
            priority=12,
            strategy=RecoveryStrategy.SYNTHESIZE,
            condition=lambda ctx: _classify_error(ctx.error) == "permission_denied",
        ))
        self.register(RecoveryPattern(
            name="connection_error",
            priority=13,
            strategy=RecoveryStrategy.SYNTHESIZE,
            condition=lambda ctx: _classify_error(ctx.error) == "connection",
        ))
        self.register(RecoveryPattern(
            name="timeout",
            priority=14,
            strategy=RecoveryStrategy.SYNTHESIZE,
            condition=lambda ctx: _classify_error(ctx.error) == "timeout",
        ))

        # ── Zero results ─────────────────────────────────────────────────────
        self.register(RecoveryPattern(
            name="zero_results",
            priority=30,
            strategy=RecoveryStrategy.CRITIC,
            condition=lambda ctx: _classify_error(ctx.error) == "zero_results",
        ))

        # ── Schema missing: discovery on first occurrence, critic on repeat ──
        self.register(RecoveryPattern(
            name="schema_missing_first",
            priority=40,
            strategy=RecoveryStrategy.DISCOVERY,
            condition=lambda ctx: (
                _classify_error(ctx.error) == "schema_missing"
                and ctx.repeat_count == 0
            ),
        ))
        self.register(RecoveryPattern(
            name="schema_missing_repeat",
            priority=41,
            strategy=RecoveryStrategy.CRITIC,
            condition=lambda ctx: (
                _classify_error(ctx.error) == "schema_missing"
                and ctx.repeat_count > 0
            ),
        ))

        # ── Generic fallback ─────────────────────────────────────────────────
        self.register(RecoveryPattern(
            name="generic_error",
            priority=50,
            strategy=RecoveryStrategy.CRITIC,
            condition=lambda ctx: bool(ctx.error),
        ))


# Module-level singleton used by graph.py
router = RecoveryRouter()
