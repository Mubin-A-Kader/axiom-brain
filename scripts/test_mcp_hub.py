import asyncio
import httpx
import json
from axiom.api.app import app
from fastapi.testclient import TestClient

def test_hub_and_security():
    client = TestClient(app)
    
    print("\n--- 1. Testing MCP Hub Discovery ---")
    # This checks if the server registered in startup is visible
    # (Note: This depends on the hub router being mounted)
    
    print("\n--- 2. Testing Zero Trust Blocking (No DID) ---")
    # Try to call a tool without the X-Agent-DID header
    response = client.post("/mcp/postgres/messages?sessionId=test", json={
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "run_query", "arguments": {"sql": "SELECT 1"}},
        "id": 1
    })
    # Should be blocked or session not found (since SSE isn't active)
    print(f"Response (Expected 404 or block): {response.status_code}")

    print("\n--- 3. Testing ABAC Policy Evaluation ---")
    # Direct test of the policy engine logic
    from axiom.security.trust.pep import ABACPolicyEngine
    engine = ABACPolicyEngine()
    
    # Knowledge agent trying to run SQL (Should fail)
    is_safe = engine.evaluate(
        subject_did="did:axiom:agent:knowledge_retrieval:parent:123",
        action="run_query",
        resource_id="did:axiom:mcp_server:postgres",
        context={"tenant_id": "test", "sql": "SELECT * FROM users"}
    )
    print(f"Knowledge Agent -> run_query: {'ALLOWED' if is_safe else 'BLOCKED (Correct)'}")

    # SQL agent trying to run read-only SQL (Should pass)
    is_safe = engine.evaluate(
        subject_did="did:axiom:agent:sql_execution:parent:123",
        action="run_query",
        resource_id="did:axiom:mcp_server:postgres",
        context={"tenant_id": "test", "sql": "SELECT * FROM users"}
    )
    print(f"SQL Agent -> run_query: {'ALLOWED (Correct)' if is_safe else 'BLOCKED'}")

    # SQL agent trying to DROP table (Should fail)
    is_safe = engine.evaluate(
        subject_did="did:axiom:agent:sql_execution:parent:123",
        action="run_query",
        resource_id="did:axiom:mcp_server:postgres",
        context={"tenant_id": "test", "sql": "DROP TABLE users"}
    )
    print(f"SQL Agent -> DROP TABLE: {'ALLOWED' if is_safe else 'BLOCKED (Correct)'}")

if __name__ == "__main__":
    test_hub_and_security()
