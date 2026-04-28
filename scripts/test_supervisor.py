import asyncio
import json
from axiom.agent.supervisor import SupervisorNode
from axiom.agent.state import GlobalAgentState

async def test():
    node = SupervisorNode()
    
    print("--- TEST: REFINEMENT WITH 'graph' ---")
    state = {
        "question": "can u a graph from this",
        "tenant_id": "paralymc",
        "history_context": "User: Sow smary of top 10 emails\nGMAIL_AGENT: Here is your summary of 10 emails from OpenAI, Google, LinkedIn..."
    }
    res = await node(state)
    print(f"ROUTING RESULT: {json.dumps(res, indent=2)}")

if __name__ == "__main__":
    asyncio.run(test())
