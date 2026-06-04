"""Prometheus metrics.

We rely on ``prometheus_fastapi_instrumentator`` for HTTP metrics
(request count, latency, in-progress) and define a few custom metrics
that the rest of the app uses directly:

- ``llm_tokens_total{provider, model, kind=in|out}``
- ``llm_request_duration_seconds{provider, model}``
- ``agent_iterations_total{outcome}``
- ``rag_retrieval_duration_seconds``
- ``safety_blocks_total{reason}``
"""

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

from axiom.config import get_settings

# --- Custom metrics ----------------------------------------------------------

llm_tokens_total = Counter(
    "llm_tokens_total",
    "Total LLM tokens consumed.",
    ["provider", "model", "kind"],
)

llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "End-to-end LLM call latency.",
    ["provider", "model"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)

agent_iterations_total = Counter(
    "agent_iterations_total",
    "Agent loop iterations grouped by terminal outcome.",
    ["outcome"],  # answered | tool_error | max_iterations | timeout | safety_block
)

rag_retrieval_duration_seconds = Histogram(
    "rag_retrieval_duration_seconds",
    "Vector-search retrieval latency.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2),
)

safety_blocks_total = Counter(
    "safety_blocks_total",
    "Requests blocked by safety guardrails.",
    ["reason"],
)


def setup_metrics(app: FastAPI) -> None:
    settings = get_settings()
    if not settings.enable_prometheus:
        return
    Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/metrics", "/health"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
