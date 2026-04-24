import asyncio
import logging

from axiom.agent.graph import build_graph

logging.basicConfig(level=logging.DEBUG)

async def main():
    try:
        g = await build_graph()
        initial_state = {
            "question": "Why did revenue drop?", 
            "tenant_id": "test_tenant", 
            "thread_id": "test", 
            "session_id": "test", 
            "schema_context": "", 
            "attempts": 0, 
            "selected_tables": [], 
            "few_shot_examples": "", 
            "custom_rules": "", 
            "active_filters": [], 
            "verified_joins": [], 
            "error_log": [], 
            "negative_constraints": [], 
            "probing_options": [], 
            "confirmed_tables": [], 
            "history_tables": [], 
            "hypotheses": [], 
            "validation_results": [], 
            "investigation_log": []
        }
        
        async for chunk in g.astream(initial_state, config={'configurable': {'thread_id': 'test'}}, stream_mode="updates"):
            print("CHUNK:", chunk)
            
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
