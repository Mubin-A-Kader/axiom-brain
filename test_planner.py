import asyncio
from axiom.agent.planner import QueryPlannerNode
from axiom.config import settings

async def main():
    node = QueryPlannerNode()
    state = {
        "history_context": "Q: Top Customers\nSQL: SELECT * FROM customers;\nResult: [Bob Smith, alice@...]",
        "question": "in that anyone with name bob",
        "is_stale": False
    }
    res = await node(state)
    print(res)

asyncio.run(main())
