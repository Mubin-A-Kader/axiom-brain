import asyncio
import logging
from temporalio.client import Client
from temporalio.worker import Worker

from axiom.agent.temporal.workflows import SQLAgentWorkflow
from axiom.agent.temporal.activities import SQLActivities
from axiom.rag.schema import SchemaRAG
from axiom.agent.thread import ThreadManager

logging.basicConfig(level=logging.INFO)

async def run_worker():
    # Initialize dependencies
    rag = SchemaRAG()
    thread_mgr = ThreadManager()
    activities = SQLActivities(rag, thread_mgr)

    # Connect to Temporal
    client = await Client.connect("localhost:7233")

    # Run the worker
    worker = Worker(
        client,
        task_queue="sql-agent-tasks",
        workflows=[SQLAgentWorkflow],
        activities=[
            activities.retrieve_schema,
            activities.plan_query,
            activities.generate_sql,
            activities.execute_sql,
        ],
    )
    
    logging.info("Temporal worker started on task queue 'sql-agent-tasks'")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(run_worker())
