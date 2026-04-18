import asyncio
from langgraph.graph import StateGraph, END
from typing import TypedDict
from langgraph.checkpoint.memory import MemorySaver

class State(TypedDict):
    input: str
    output: str

def node1(state: State):
    return {"output": "from node1"}

def node2(state: State):
    return {"output": state.get("output", "") + " -> from node2"}

async def main():
    builder = StateGraph(State)
    builder.add_node("n1", node1)
    builder.add_node("n2", node2)
    builder.set_entry_point("n1")
    builder.add_edge("n1", "n2")
    builder.add_edge("n2", END)
    
    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer, interrupt_before=["n2"])
    
    config = {"configurable": {"thread_id": "1"}}
    state = await graph.ainvoke({"input": "hello", "output": ""}, config=config)
    print("Return from ainvoke:", state)
    
    agent_state = await graph.aget_state(config)
    print("Agent state values:", agent_state.values)
    print("Next:", agent_state.next)

asyncio.run(main())
