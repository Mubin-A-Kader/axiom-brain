import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from axiom.agent.temporal.workflows import ExecutionWorkflow
from axiom.agent.temporal.activities import SQLActivities
from axiom.config import settings
from axiom.rag.schema import SchemaRAG
from axiom.agent.thread import ThreadManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("axiom-worker")


async def run_worker() -> None:
    rag = SchemaRAG()
    thread_mgr = ThreadManager()
    activities = SQLActivities(rag, thread_mgr)

    client = await Client.connect(settings.temporal_url)
    logger.info("Connected to Temporal at %s", settings.temporal_url)

    worker = Worker(
        client,
        task_queue="sql-agent-tasks",
        workflows=[ExecutionWorkflow],
        activities=[
            activities.execute_sql,
            activities.generate_sql,
            activities.run_critic,
            activities.run_discovery,
            activities.generate_python_code,
            activities.build_notebook,
            activities.synthesize_response,
        ],
    )

    logger.info("Temporal worker started — listening on 'sql-agent-tasks'")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
