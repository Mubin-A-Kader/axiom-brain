import asyncio
from axiom.agent.planner import QueryPlannerNode
from axiom.config import settings

async def main():
    node = QueryPlannerNode()
    state = {
        "history_context": "Q: Top 5 cusomters\nSQL: SELECT * FROM customers;\nResult: [Bob Smith, alice@...]",
        "question": "in that who is top",
        "is_stale": False
    }
    res = await node(state)
    print(res)

asyncio.run(main())
