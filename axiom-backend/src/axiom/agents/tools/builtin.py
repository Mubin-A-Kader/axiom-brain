"""Built-in agent tools.

These ship out of the box. Adding your own tool is a single decorator
call — see ``knowledge_search`` for the pattern.
"""

from __future__ import annotations

import ast
import operator as op
from typing import Any

import httpx

from axiom.agents.tools import ToolContext, tool
from axiom.db.session import get_sessionmaker
from axiom.rag.pipeline import answer as rag_answer


# --- knowledge_search --------------------------------------------------------


@tool(
    name="knowledge_search",
    description=(
        "Search the internal knowledge base for information relevant to a "
        "natural-language query. Returns a synthesized answer plus citations."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The question to answer."},
        },
        "required": ["query"],
    },
)
async def knowledge_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"]
    async with get_sessionmaker()() as session:
        result = await rag_answer(session, query)
    return {
        "answer": result.answer,
        "citations": [
            {"index": c.index, "document": c.document_title, "score": c.score}
            for c in result.citations
        ],
    }


# --- calculator --------------------------------------------------------------

_BIN_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.Mod: op.mod, ast.Pow: op.pow, ast.FloorDiv: op.floordiv,
}
_UNARY_OPS = {ast.UAdd: op.pos, ast.USub: op.neg}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("Unsupported expression.")


@tool(
    name="calculator",
    description="Evaluate a basic arithmetic expression. Only +, -, *, /, **, %, // are allowed.",
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
)
async def calculator(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    expr = args["expression"]
    tree = ast.parse(expr, mode="eval")
    return {"result": _safe_eval(tree.body)}


# --- http_get ----------------------------------------------------------------


@tool(
    name="http_get",
    description=(
        "Perform a GET request to a publicly accessible HTTPS URL and return "
        "the response body (truncated to 8 KB). Use sparingly."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "format": "uri"}},
        "required": ["url"],
    },
)
async def http_get(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    url = args["url"]
    if not url.startswith("https://"):
        return {"error": "Only https:// URLs are allowed."}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
    return {"status": resp.status_code, "body": resp.text[:8192]}
