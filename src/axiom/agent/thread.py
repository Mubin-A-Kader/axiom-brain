import hashlib
import json
import logging
import time
from typing import TypedDict

import redis.asyncio as redis

from axiom.config import settings

logger = logging.getLogger(__name__)


class Turn(TypedDict):
    timestamp: float
    question: str
    sql: str
    result: str


class ThreadManager:
    def __init__(self) -> None:
        self._redis_url = settings.redis_url
        self._client: redis.Redis | None = None
        self._history_size = 5
        self._stale_threshold = 1800  # 30 minutes
        self._token_limit = 0.8  # 80% of context window

    async def _get_client(self) -> redis.Redis:
        if not self._client:
            self._client = await redis.from_url(self._redis_url)
        return self._client

    async def get_history(self, thread_id: str) -> list[Turn]:
        """Fetch conversation history from Redis."""
        client = await self._get_client()
        key = f"axiom:thread:{thread_id}"
        try:
            data = await client.get(key)
            if not data:
                return []
            parsed = json.loads(data)
            return parsed.get("turns", [])
        except Exception as exc:
            logger.warning("Failed to load thread history: %s", exc)
            return []

    async def get_context_injection(self, thread_id: str, schema_context: str) -> tuple[str, bool]:
        """Build context string for the LLM and detect if stale."""
        history = await self.get_history(thread_id)
        is_stale = await self.is_stale(thread_id)

        if not history:
            return "No prior conversation history.", is_stale

        # Estimate tokens (rough: ~4 chars per token)
        context_lines = ["Recent conversation history:"]
        token_count = len(schema_context) // 4

        for turn in history[-self._history_size :]:
            turn_text = f"Q: {turn['question']}\nSQL: {turn['sql']}\n"
            turn_tokens = len(turn_text) // 4
            token_count += turn_tokens

            # Stop adding if we exceed 80% of typical context (assume 128k token window)
            if token_count > int(128000 * self._token_limit):
                context_lines.append("[... history truncated due to token limit ...]")
                break
            context_lines.append(f"Q: {turn['question']}")
            context_lines.append(f"SQL: {turn['sql']}")

        context = "\n".join(context_lines)
        return context, is_stale

    async def save_turn(self, thread_id: str, question: str, sql: str, result: str) -> None:
        """Save a conversation turn to Redis with 24h TTL."""
        client = await self._get_client()
        key = f"axiom:thread:{thread_id}"

        history = await self.get_history(thread_id)
        turn: Turn = {
            "timestamp": time.time(),
            "question": question,
            "sql": sql,
            "result": result,
        }
        history.append(turn)
        history = history[-self._history_size :]

        data = json.dumps({"turns": history, "last_active": time.time()})
        await client.setex(key, 86400, data)

    async def is_stale(self, thread_id: str) -> bool:
        """Check if thread hasn't been active in 30+ minutes."""
        history = await self.get_history(thread_id)
        if not history:
            return True
        last_turn = history[-1]
        return time.time() - last_turn["timestamp"] > self._stale_threshold

    @staticmethod
    def cache_key(thread_id: str, question: str) -> str:
        """Generate composite cache key: hash(thread_id + question)."""
        content = f"{thread_id}:{question}"
        return f"axiom:cache:{hashlib.sha256(content.encode()).hexdigest()}"

    async def get_cached_result(self, thread_id: str, question: str) -> dict | None:
        """Fetch cached SQL/result if available."""
        client = await self._get_client()
        key = self.cache_key(thread_id, question)
        try:
            data = await client.get(key)
            if data:
                return json.loads(data)
        except Exception:
            pass
        return None

    async def set_cached_result(self, thread_id: str, question: str, sql: str, result: str) -> None:
        """Cache query result for exact match replay."""
        client = await self._get_client()
        key = self.cache_key(thread_id, question)
        data = json.dumps({"sql": sql, "result": result})
        await client.setex(key, 3600, data)  # 1h cache TTL
