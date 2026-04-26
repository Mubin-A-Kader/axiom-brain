"""
Axiom Gmail MCP Server.

Reads GMAIL_OAUTH_TOKEN from the environment and exposes Gmail tools
over the STDIO MCP protocol. Run as:

    python -m axiom.connectors.apps.gmail_mcp_server
"""
import asyncio
import base64
import json
import os

import httpx
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

server = Server("axiom-gmail")


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("GMAIL_OAUTH_TOKEN", "")
    return {"Authorization": f"Bearer {token}"}


def _decode_body(payload: dict) -> str:
    """Recursively extract plain-text body from a message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        result = _decode_body(part)
        if result:
            return result
    return ""


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_threads",
            description=(
                "Search Gmail threads. Use standard Gmail search syntax "
                "(e.g. 'is:unread', 'from:alice@example.com', 'subject:invoice')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query string.",
                    },
                    "maxResults": {
                        "type": "integer",
                        "description": "Maximum number of threads to return (default 10).",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_thread",
            description="Get the full content of a Gmail thread by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "threadId": {
                        "type": "string",
                        "description": "Gmail thread ID.",
                    },
                },
                "required": ["threadId"],
            },
        ),
        types.Tool(
            name="create_draft",
            description="Create a Gmail draft message.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Plain-text email body."},
                },
                "required": ["to", "subject", "body"],
            },
        ),
        types.Tool(
            name="send_email",
            description="Send an email immediately (not as a draft).",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Plain-text email body."},
                },
                "required": ["to", "subject", "body"],
            },
        ),
        types.Tool(
            name="list_labels",
            description="List all Gmail labels for the account.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    headers = _auth_headers()

    async with httpx.AsyncClient(timeout=15.0) as client:

        if name == "search_threads":
            params = {
                "q": arguments.get("query", ""),
                "maxResults": arguments.get("maxResults", 10),
            }
            resp = await client.get(f"{GMAIL_BASE}/threads", headers=headers, params=params)
            resp.raise_for_status()
            threads = resp.json().get("threads", [])

            results = []
            for t in threads[:10]:
                tr = await client.get(
                    f"{GMAIL_BASE}/threads/{t['id']}",
                    headers=headers,
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["Subject", "From", "Date"],
                    },
                )
                if tr.status_code == 200:
                    td = tr.json()
                    msg = td.get("messages", [{}])[0]
                    hmap = {
                        h["name"]: h["value"]
                        for h in msg.get("payload", {}).get("headers", [])
                    }
                    results.append({
                        "threadId": t["id"],
                        "subject": hmap.get("Subject", "(no subject)"),
                        "from": hmap.get("From", ""),
                        "date": hmap.get("Date", ""),
                        "snippet": td.get("snippet", ""),
                    })
            return [types.TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "get_thread":
            resp = await client.get(
                f"{GMAIL_BASE}/threads/{arguments['threadId']}",
                headers=headers,
                params={"format": "full"},
            )
            resp.raise_for_status()
            messages = []
            for msg in resp.json().get("messages", []):
                hmap = {
                    h["name"]: h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                body = _decode_body(msg.get("payload", {}))
                messages.append({
                    "messageId": msg["id"],
                    "from": hmap.get("From", ""),
                    "to": hmap.get("To", ""),
                    "subject": hmap.get("Subject", ""),
                    "date": hmap.get("Date", ""),
                    "body": body[:3000],
                })
            return [types.TextContent(type="text", text=json.dumps(messages, indent=2))]

        elif name in ("create_draft", "send_email"):
            raw = (
                f"To: {arguments['to']}\r\n"
                f"Subject: {arguments['subject']}\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
                f"{arguments['body']}"
            )
            encoded = base64.urlsafe_b64encode(raw.encode()).decode()
            payload = {"message": {"raw": encoded}}

            if name == "create_draft":
                resp = await client.post(
                    f"{GMAIL_BASE}/drafts",
                    headers={**headers, "Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"status": "draft_created", "draftId": resp.json().get("id")}),
                )]
            else:
                resp = await client.post(
                    f"{GMAIL_BASE}/messages/send",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"raw": encoded},
                )
                resp.raise_for_status()
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"status": "sent", "messageId": resp.json().get("id")}),
                )]

        elif name == "list_labels":
            resp = await client.get(f"{GMAIL_BASE}/labels", headers=headers)
            resp.raise_for_status()
            labels = [
                {"id": l["id"], "name": l["name"]}
                for l in resp.json().get("labels", [])
            ]
            return [types.TextContent(type="text", text=json.dumps(labels, indent=2))]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
