import json
import logging
from typing import List, Optional, Dict, Any

from mcp.server import Server
from mcp.types import Tool, TextContent
from axiom.rag.schema import SchemaRAG

logger = logging.getLogger("mcp-knowledge-server")

class KnowledgeMCPServer:
    def __init__(self, rag: SchemaRAG):
        self.rag = rag
        self.server = Server("axiom-knowledge-retrieval")
        self._setup_handlers()

    def _setup_handlers(self):
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            return [
                Tool(
                    name="retrieve_schema",
                    description="Search and retrieve relevant table DDLs for a given natural language question.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tenant_id": {"type": "string"},
                            "source_id": {"type": "string"},
                            "question": {"type": "string"},
                            "n_results": {"type": "integer", "default": 5}
                        },
                        "required": ["tenant_id", "source_id", "question"],
                    },
                ),
                Tool(
                    name="retrieve_examples",
                    description="Retrieve semantically similar past successful Q&A pairs (few-shot examples).",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tenant_id": {"type": "string"},
                            "source_id": {"type": "string"},
                            "question": {"type": "string"},
                            "n_results": {"type": "integer", "default": 2}
                        },
                        "required": ["tenant_id", "source_id", "question"],
                    },
                ),
                Tool(
                    name="search_semantic_cache",
                    description="Check if an identical question has been asked and answered before.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tenant_id": {"type": "string"},
                            "source_id": {"type": "string"},
                            "question": {"type": "string"}
                        },
                        "required": ["tenant_id", "source_id", "question"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> List[TextContent]:
            from axiom.security.trust.tagging import LLMTagging
            try:
                if name == "retrieve_schema":
                    res = await self.rag.retrieve(
                        arguments["tenant_id"],
                        arguments["source_id"],
                        arguments["question"],
                        arguments.get("n_results", 5)
                    )
                    return [TextContent(type="text", text=LLMTagging.wrap_schema(res))]
                
                elif name == "retrieve_examples":
                    res = await self.rag.retrieve_examples(
                        arguments["tenant_id"],
                        arguments["source_id"],
                        arguments["question"],
                        arguments.get("n_results", 2)
                    )
                    return [TextContent(type="text", text=LLMTagging.wrap_untrusted(res, source="few_shot_examples"))]
                
                elif name == "search_semantic_cache":
                    res = await self.rag.search_semantic_cache(
                        arguments["tenant_id"],
                        arguments["source_id"],
                        arguments["question"]
                    )
                    serialized = json.dumps(res) if res else "null"
                    return [TextContent(type="text", text=LLMTagging.wrap_untrusted(serialized, source="semantic_cache"))]
                
                return [TextContent(type="text", text=f"ERROR: Unknown tool '{name}'")]
            except Exception as e:
                logger.exception(f"Knowledge MCP tool {name} failed")
                return [TextContent(type="text", text=f"ERROR: {str(e)}")]

    def get_server(self) -> Server:
        return self.server
