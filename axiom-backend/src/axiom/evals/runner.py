"""Eval runner.

Takes a target function and a dataset, runs the target on each example
with bounded concurrency, applies each configured metric, and stores
``EvalResult`` rows. The aggregate summary lands on the ``EvalRun``
row (pass rate, per-metric means, p50/p95 latency).
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from axiom.core.logging import get_logger
from axiom.db.models import EvalDataset, EvalExample, EvalResult, EvalRun
from axiom.evals.metrics import REGISTRY

log = get_logger(__name__)

Target = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class EvalConfig:
    metrics: list[str]
    threshold: float = 0.7  # an example passes if min(metric_score) >= threshold
    concurrency: int = 4


async def run_eval(
    session: AsyncSession,
    dataset_id: UUID,
    target: Target,
    config: EvalConfig,
) -> EvalRun:
    dataset = await session.get(EvalDataset, dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset {dataset_id} not found.")

    examples = (
        await session.execute(select(EvalExample).where(EvalExample.dataset_id == dataset_id))
    ).scalars().all()

    run = EvalRun(dataset_id=dataset_id, config={"metrics": config.metrics}, status="running")
    session.add(run)
    await session.flush()

    sem = asyncio.Semaphore(config.concurrency)
    durations: list[float] = []
    pass_count = 0
    metric_scores: dict[str, list[float]] = {m: [] for m in config.metrics}

    async def evaluate_one(example: EvalExample) -> None:
        nonlocal pass_count
        async with sem:
            started = time.perf_counter()
            try:
                output = await target(example.input)
            except Exception as exc:  # noqa: BLE001
                log.warning("eval.target_error", example_id=str(example.id), error=str(exc))
                output = {"error": str(exc), "answer": ""}
            elapsed = time.perf_counter() - started
            durations.append(elapsed)

            predicted = output.get("answer", "")
            expected = example.expected.get("answer", "")
            ctx = {**example.input, **output}
            scores: dict[str, float] = {}
            for name in config.metrics:
                fn = REGISTRY.get(name)
                if fn is None:
                    scores[name] = 0.0
                    continue
                try:
                    scores[name] = await fn(predicted, expected, ctx)
                except Exception:  # noqa: BLE001
                    scores[name] = 0.0
                metric_scores[name].append(scores[name])

            passed = bool(scores) and min(scores.values()) >= config.threshold
            if passed:
                pass_count += 1

            session.add(
                EvalResult(
                    run_id=run.id, example_id=example.id, output=output,
                    scores=scores, passed=passed,
                )
            )

    await asyncio.gather(*(evaluate_one(e) for e in examples))

    summary = {
        "n_examples": len(examples),
        "pass_rate": pass_count / len(examples) if examples else 0.0,
        "metrics": {
            name: {
                "mean": statistics.fmean(vals) if vals else 0.0,
                "n": len(vals),
            }
            for name, vals in metric_scores.items()
        },
        "latency": {
            "p50": statistics.median(durations) if durations else 0.0,
            "p95": _percentile(durations, 95) if durations else 0.0,
            "mean": statistics.fmean(durations) if durations else 0.0,
        },
    }
    run.summary = summary
    run.status = "completed"
    await session.flush()

    log.info("eval.run.complete", run_id=str(run.id), summary=summary)
    return run


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)
