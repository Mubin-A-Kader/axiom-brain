"""Background worker tasks (ARQ — async Redis Queue).

ARQ workers pick jobs off a Redis list, execute the coroutine, and
handle retries. The ``WorkerSettings`` class is what ``arq`` reads when
you run ``arq axiom.workers.tasks.WorkerSettings``.

Adding a new task:
1. Write an ``async def task_name(ctx, ...)`` function.
2. Add it to ``functions`` in ``WorkerSettings``.
3. Enqueue it with ``await pool.enqueue_job("task_name", ...)``.
"""

from __future__ import annotations

import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from axiom.config import get_settings
from axiom.core.logging import configure_logging, get_logger

log = get_logger(__name__)

# --- Task implementations ----------------------------------------------------


async def run_eval_task(ctx: dict, run_id: str) -> None:
    """Execute an EvalRun asynchronously."""
    from axiom.agents.executor import AgentInput, run_agent
    from axiom.db.session import init_db, get_sessionmaker
    from axiom.evals.runner import EvalConfig, run_eval
    from axiom.rag.pipeline import answer as rag_answer

    settings = get_settings()
    await init_db()

    async with get_sessionmaker()() as session:
        from axiom.db.models import EvalRun
        run = await session.get(EvalRun, uuid.UUID(run_id))
        if run is None:
            log.error("eval_worker.run_not_found", run_id=run_id)
            return

        target_name = run.config.get("target", "rag")

        if target_name == "rag":
            async def target(inp: dict) -> dict:
                async with get_sessionmaker()() as s:
                    result = await rag_answer(s, inp.get("question", inp.get("query", "")))
                return {
                    "answer": result.answer,
                    "citations_text": [c.chunk_id for c in result.citations],
                }
        else:
            async def target(inp: dict) -> dict:
                result = await run_agent(
                    input_=AgentInput(user_message=inp.get("message", inp.get("query", ""))),
                    principal_id="eval_worker",
                    request_id=run_id,
                )
                return {"answer": result.answer}

        eval_config = EvalConfig(
            metrics=run.config.get("metrics", ["llm_judge"]),
            threshold=run.config.get("threshold", 0.7),
            concurrency=run.config.get("concurrency", 4),
        )
        await run_eval(session, run.dataset_id, target, eval_config)

    log.info("eval_worker.done", run_id=run_id)


# --- Enqueue helper ----------------------------------------------------------

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        settings = get_settings()
        # Parse redis://host:port/db
        url = str(settings.redis_url)
        host, port, db = _parse_redis_url(url)
        _pool = await create_pool(RedisSettings(host=host, port=port, database=db))
    return _pool


async def enqueue_eval_run(run_id: str) -> None:
    pool = await _get_pool()
    await pool.enqueue_job("run_eval_task", run_id)


def _parse_redis_url(url: str) -> tuple[str, int, int]:
    # redis://host:port/db
    url = url.replace("redis://", "")
    host_port, _, db_str = url.partition("/")
    host, _, port_str = host_port.partition(":")
    return host or "localhost", int(port_str or 6379), int(db_str or 0)


# --- ARQ Worker settings -----------------------------------------------------


class WorkerSettings:
    functions = [run_eval_task]
    redis_settings = RedisSettings()  # reads REDIS_URL from env via ARQ

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        settings = get_settings()
        configure_logging(settings.log_level)
        log.info("worker.startup")

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        log.info("worker.shutdown")
