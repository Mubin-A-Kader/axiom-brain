"""Seed script.

Run with::

    python -m scripts.seed

Inserts a sample document and eval dataset so you can immediately try
``/v1/rag/query`` and ``/v1/evals/runs`` without external data.
"""

import asyncio

from axiom.config import get_settings
from axiom.core.logging import configure_logging
from axiom.db.models import EvalDataset, EvalExample
from axiom.db.session import close_db, get_sessionmaker, init_db
from axiom.rag.ingest import IngestRequest, ingest_documents

SAMPLE_DOC = """
AXIOM 2.0 — Architecture Overview

AXIOM is a high-performance command nexus for autonomous agents supporting
Bring Your Own Model (BYOM) orchestration.

Key components:
- **Agent Executor**: Iterative tool-use loop with bounded iterations and timeout.
- **RAG Pipeline**: Ingest → chunk → embed → pgvector store → retrieve → rerank → generate.
- **Safety Layer**: Prompt injection heuristics, PII redaction, token-length enforcement.
- **Eval Framework**: LLM-as-judge and semantic metrics with per-metric scoring.
- **Observability**: OpenTelemetry tracing, Prometheus metrics, structured JSON logging.

Supported providers: Anthropic Claude, OpenAI GPT series (BYOM plug-in interface).
"""

SAMPLE_EVAL_EXAMPLES = [
    {
        "input": {"question": "What does AXIOM stand for?", "query": "What does AXIOM stand for?"},
        "expected": {"answer": "AXIOM is a high-performance command nexus for autonomous agents."},
    },
    {
        "input": {"question": "What observability tools does AXIOM use?", "query": "observability"},
        "expected": {"answer": "OpenTelemetry tracing, Prometheus metrics, structured JSON logging."},
    },
]


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await init_db()

    async with get_sessionmaker()() as session:
        # Ingest sample document
        docs = await ingest_documents(
            session,
            [IngestRequest(title="AXIOM Architecture Overview", text=SAMPLE_DOC)],
        )
        print(f"Ingested {len(docs)} document(s), {docs[0].chunks.__len__()} chunks.")

        # Create sample eval dataset
        dataset = EvalDataset(name="axiom-demo-v1", description="Demo eval dataset for AXIOM.")
        session.add(dataset)
        await session.flush()
        for ex in SAMPLE_EVAL_EXAMPLES:
            session.add(
                EvalExample(dataset_id=dataset.id, input=ex["input"], expected=ex["expected"])
            )
        await session.commit()
        print(f"Created eval dataset: {dataset.id}")

    await close_db()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
