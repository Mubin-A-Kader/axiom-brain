"""
SharedMemoryStore — thread-scoped cross-agent knowledge base.

Provides two things:
  1. SchemaContract: a negotiated column-mapping between two data sources,
     e.g. {"user_id": "customer_id"} between source-A and source-B.
  2. SharedMemoryStore: persists contracts in Redis (with in-process fallback)
     so they survive across the fan-out/curator boundary within a thread.

Usage
-----
Contracts flow in one direction per thread:
  LakeCuratorNode (writes)  →  Redis  →  LakeOrchestratorNode (reads)

On the FIRST query in a thread, the store is empty — workers run independently.
After the Curator successfully merges results, it negotiates and writes contracts.
On SUBSEQUENT queries in the same thread, the Orchestrator reads those contracts
and injects join-key context into each LakeWorker's SQL generation prompt.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SchemaContract:
    """
    A negotiated agreement between two data sources about how their columns
    relate to each other.

    join_key_mapping: maps column names from source_a → source_b.
      e.g. {"user_id": "customer_id", "order_date": "transaction_date"}

    agreed_columns: columns that exist in BOTH sources under the SAME name
      (no mapping needed — they can be merged directly).

    confidence: 0.0 – 1.0. Contracts below 0.4 are soft hints only.
    """
    source_a: str
    source_b: str
    join_key_mapping: dict[str, str]      # col_in_a → col_in_b
    agreed_columns: list[str]             # cols present in both with same name
    confidence: float
    negotiated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SchemaContract":
        return cls(**d)

    def prompt_hint(self, perspective_source: str) -> str:
        """Return a one-line hint suitable for injecting into an LLM prompt."""
        if perspective_source == self.source_a:
            other = self.source_b
            mapping = self.join_key_mapping  # a→b, show what b calls them
            direction = {v: k for k, v in mapping.items()}
        else:
            other = self.source_a
            mapping = {v: k for k, v in self.join_key_mapping.items()}  # b→a
            direction = mapping

        if not direction and not self.agreed_columns:
            return ""

        parts: list[str] = []
        if self.agreed_columns:
            parts.append(f"shared columns with {other}: {', '.join(self.agreed_columns)}")
        if direction:
            mapped = ", ".join(f"{local} ↔ {remote}" for local, remote in direction.items())
            parts.append(f"equivalent columns in {other}: {mapped}")
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# Negotiator
# ---------------------------------------------------------------------------

_IGNORE_COLS = {"_source", "id"}
_ID_SUFFIXES = ("_id", "_key", "_uuid", "_ref")


class SchemaContractNegotiator:
    """
    Compares column sets from two LakeWorkerResult dicts and produces a
    SchemaContract when meaningful overlap is found.

    Matching strategy (in order of confidence):
      1. Exact name match → agreed_columns (confidence += 0.3 per match)
      2. One column ends with _id/_key and the other column's stem matches
         (e.g. user_id ↔ customer_id both reference "identity" concept)
         → join_key_mapping (confidence += 0.15 per match)
      3. SequenceMatcher ratio > 0.75 on normalised names
         → join_key_mapping (confidence += 0.1 per match)
    """

    MIN_CONFIDENCE = 0.15

    def negotiate(
        self,
        result_a: dict[str, Any],
        result_b: dict[str, Any],
    ) -> Optional[SchemaContract]:
        cols_a = self._extract_columns(result_a)
        cols_b = self._extract_columns(result_b)

        if not cols_a or not cols_b:
            return None

        agreed: list[str] = []
        mapping: dict[str, str] = {}  # col_in_a → col_in_b
        confidence = 0.0

        set_a, set_b = set(cols_a), set(cols_b)

        # 1. Exact matches
        for col in sorted(set_a & set_b):
            if col not in _IGNORE_COLS:
                agreed.append(col)
                confidence += 0.3

        remaining_a = sorted(set_a - set_b - _IGNORE_COLS)
        remaining_b = sorted(set_b - set_a - _IGNORE_COLS)

        # 2. ID-suffix heuristic
        for ca in remaining_a:
            for cb in remaining_b:
                if self._id_match(ca, cb):
                    mapping[ca] = cb
                    confidence += 0.15
                    remaining_b = [c for c in remaining_b if c != cb]
                    break

        # 3. Fuzzy name matching
        for ca in remaining_a:
            if ca in mapping:
                continue
            best_ratio, best_cb = 0.0, ""
            for cb in remaining_b:
                if cb in mapping.values():
                    continue
                ratio = SequenceMatcher(None, self._normalise(ca), self._normalise(cb)).ratio()
                if ratio > best_ratio:
                    best_ratio, best_cb = ratio, cb
            if best_ratio >= 0.75 and best_cb:
                mapping[ca] = best_cb
                confidence += 0.1

        if confidence < self.MIN_CONFIDENCE:
            return None

        return SchemaContract(
            source_a=result_a["source_id"],
            source_b=result_b["source_id"],
            join_key_mapping=mapping,
            agreed_columns=agreed,
            confidence=min(confidence, 1.0),
        )

    @staticmethod
    def _extract_columns(result: dict[str, Any]) -> list[str]:
        try:
            data = json.loads(result.get("sql_result") or "{}")
            return [c for c in data.get("columns", []) if c != "_source"]
        except Exception:
            return []

    @staticmethod
    def _id_match(a: str, b: str) -> bool:
        """True when both columns are ID-like and share a common stem."""
        a_is_id = any(a.endswith(s) for s in _ID_SUFFIXES) or a == "id"
        b_is_id = any(b.endswith(s) for s in _ID_SUFFIXES) or b == "id"
        if not (a_is_id and b_is_id):
            return False
        # strip suffix and check if stems overlap meaningfully
        stem_a = SchemaContractNegotiator._stem(a)
        stem_b = SchemaContractNegotiator._stem(b)
        if not stem_a or not stem_b:
            return False
        return (
            stem_a == stem_b
            or stem_a in stem_b
            or stem_b in stem_a
            or SequenceMatcher(None, stem_a, stem_b).ratio() >= 0.6
        )

    @staticmethod
    def _stem(col: str) -> str:
        for suf in _ID_SUFFIXES:
            if col.endswith(suf):
                return col[: -len(suf)]
        return col

    @staticmethod
    def _normalise(col: str) -> str:
        return col.lower().replace("_", "").replace("-", "")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class SharedMemoryStore:
    """
    Thread-scoped store for SchemaContracts.

    Writes and reads from Redis when available; falls back to an in-process
    dict (useful in tests or when Redis is down). TTL defaults to 24 h so
    contracts persist across a full working day's conversation.
    """

    _TTL_SECONDS = 86_400  # 24 h
    _KEY_PREFIX = "axiom:schema_contracts:"

    def __init__(self) -> None:
        self._local: dict[str, list[dict[str, Any]]] = {}
        self._redis: Any = None
        self._redis_available = False
        self._init_redis()

    def _init_redis(self) -> None:
        try:
            import redis.asyncio as aioredis
            from axiom.config import settings
            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            self._redis_available = True
        except Exception:
            logger.warning("SharedMemoryStore: Redis unavailable, using in-process dict")

    # ── Write ───────────────────────────────────────────────────────────────

    async def write_contracts(
        self,
        thread_id: str,
        contracts: list[SchemaContract],
    ) -> None:
        if not contracts:
            return
        existing = await self.read_contracts(thread_id)
        merged = self._merge_contracts(existing, contracts)
        payload = json.dumps([c.to_dict() for c in merged])
        if self._redis_available:
            try:
                await self._redis.setex(
                    f"{self._KEY_PREFIX}{thread_id}", self._TTL_SECONDS, payload
                )
                return
            except Exception as exc:
                logger.warning("SharedMemoryStore Redis write failed: %s", exc)
        self._local[thread_id] = [c.to_dict() for c in merged]

    # ── Read ────────────────────────────────────────────────────────────────

    async def read_contracts(self, thread_id: str) -> list[SchemaContract]:
        if self._redis_available:
            try:
                raw = await self._redis.get(f"{self._KEY_PREFIX}{thread_id}")
                if raw:
                    return [SchemaContract.from_dict(d) for d in json.loads(raw)]
            except Exception as exc:
                logger.warning("SharedMemoryStore Redis read failed: %s", exc)
        raw_local = self._local.get(thread_id, [])
        return [SchemaContract.from_dict(d) for d in raw_local]

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _merge_contracts(
        existing: list[SchemaContract],
        new: list[SchemaContract],
    ) -> list[SchemaContract]:
        """Upsert by (source_a, source_b) pair — newer contract wins."""
        index: dict[tuple[str, str], SchemaContract] = {
            (c.source_a, c.source_b): c for c in existing
        }
        for c in new:
            index[(c.source_a, c.source_b)] = c
            # also index the reverse so lookup works both ways
            index[(c.source_b, c.source_a)] = SchemaContract(
                source_a=c.source_b,
                source_b=c.source_a,
                join_key_mapping={v: k for k, v in c.join_key_mapping.items()},
                agreed_columns=c.agreed_columns,
                confidence=c.confidence,
                negotiated_at=c.negotiated_at,
            )
        return list(index.values())

    def contracts_for_source(
        self,
        contracts: list[SchemaContract],
        source_id: str,
    ) -> list[SchemaContract]:
        """Filter contracts that involve this source on either side."""
        return [c for c in contracts if c.source_a == source_id or c.source_b == source_id]


# Module-level singleton used by Orchestrator and Curator nodes.
shared_memory = SharedMemoryStore()
