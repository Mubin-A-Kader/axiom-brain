import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.stdio import stdio_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

server = Server("n8n_mcp_proxy")

WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("N8N_WEBHOOK_SECRET", "")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="call_service_api",
            description="Proxy a REST API call to the connected third-party service via n8n.",
            inputSchema={
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "HTTP method (e.g., GET, POST, PUT, PATCH). DELETE operations are strictly forbidden.",
                        "default": "GET"
                    },
                    "url": {
                        "type": "string",
                        "description": "The target API URL (e.g., https://api.github.com/user)."
                    },
                    "query": {
                        "type": "object",
                        "description": "Query parameters as key-value pairs.",
                        "default": {}
                    },
                    "payload": {
                        "type": "object",
                        "description": "JSON body payload for POST/PUT requests.",
                        "default": {}
                    }
                },
                "required": ["method", "url"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "call_service_api":
        raise ValueError(f"Unknown tool: {name}")

    webhook_url = os.environ.get("N8N_WEBHOOK_URL", "")
    webhook_secret = os.environ.get("N8N_WEBHOOK_SECRET", "")

    if not webhook_url:
        return [TextContent(type="text", text="Error: N8N_WEBHOOK_URL is not set.")]

    method = arguments.get("method", "GET").upper()
    
    if method == "DELETE":
        return [TextContent(type="text", text="Error: DELETE operations are strictly forbidden by security policy.")]

    url = arguments.get("url")
    query = arguments.get("query", {})
    payload = arguments.get("payload", {})

    headers = {}
    if webhook_secret:
        headers["X-Axiom-Secret"] = webhook_secret
    
    # We pass the instruction payload to our n8n webhook
    body = {
        "method": method,
        "url": url,
        "query": query,
        "payload": payload
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(webhook_url, json=body, headers=headers)
            r.raise_for_status()
            
            # To prevent huge API payloads (e.g. Gmail HTML bodies) from crashing the LLM context,
            # we aggressively truncate massive strings and arrays.
            def _truncate_json(data: Any, max_str_len=1500, max_list_len=50) -> Any:
                if isinstance(data, dict):
                    return {k: _truncate_json(v, max_str_len, max_list_len) for k, v in data.items()}
                elif isinstance(data, list):
                    truncated_list = [_truncate_json(item, max_str_len, max_list_len) for item in data[:max_list_len]]
                    if len(data) > max_list_len:
                        truncated_list.append(f"... {len(data) - max_list_len} more items truncated")
                    return truncated_list
                elif isinstance(data, str):
                    if len(data) > max_str_len:
                        return data[:max_str_len] + f"... [truncated {len(data) - max_str_len} chars]"
                    return data
                return data
                
            try:
                parsed_json = r.json()
                # Unpack the array if n8n returned it inside a single-element list
                if isinstance(parsed_json, list) and len(parsed_json) == 1 and isinstance(parsed_json[0], dict):
                    parsed_json = parsed_json[0]
                truncated_json = _truncate_json(parsed_json)
                response_data = json.dumps(truncated_json)
            except Exception:
                response_data = r.text
                if len(response_data) > 10000:
                    response_data = response_data[:10000] + "... [response truncated to 10k chars]"
                    
            return [TextContent(type="text", text=response_data)]
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP Error {e.response.status_code}: {e.response.text}"
        return [TextContent(type="text", text=error_msg)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error proxying request to n8n: {e}")]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())