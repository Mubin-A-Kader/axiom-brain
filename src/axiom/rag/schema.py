import json
import logging
from typing import Any, Dict, List, Optional

import chromadb
import networkx as nx
import openai
import tiktoken

from axiom.config import settings

logger = logging.getLogger(__name__)


def _where(tenant_id: str, source_id: str, doc_type: str) -> dict:
    """ChromaDB $and filter — inside $and, shorthand equality is not supported; $eq is required."""
    return {"$and": [
        {"tenant_id": {"$eq": tenant_id}},
        {"source_id": {"$eq": source_id}},
        {"type": {"$eq": doc_type}},
    ]}


class SchemaRAG:
    """
    Schema RAG using direct ChromaDB + OpenAI SDK embeddings via LiteLLM proxy.
    Hybrid retrieval: vector similarity + BM25 keyword search.
    """

    _EMBED_BATCH = 100

    def __init__(self) -> None:
        self._client = chromadb.HttpClient(
            host=settings.chroma_url.replace("http://", "").split(":")[0],
            port=int(settings.chroma_url.split(":")[-1]),
            headers={"X-Chroma-Token": settings.chroma_token},
        )
        self._collection = self._client.get_or_create_collection(settings.chroma_collection)
        self._openai = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )
        self._graphs: dict[str, nx.Graph] = {}
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Batch-embed via LiteLLM proxy using the OpenAI SDK."""
        if not texts:
            return []
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), self._EMBED_BATCH):
            batch = texts[i : i + self._EMBED_BATCH]
            response = await self._openai.embeddings.create(
                model=settings.llm_embed_model, input=batch
            )
            all_embeddings.extend(item.embedding for item in response.data)
        return all_embeddings

    def _ensure_graph_loaded(self, tenant_id: str, source_id: str) -> nx.Graph:
        graph_key = f"{tenant_id}_{source_id}"
        if graph_key in self._graphs:
            return self._graphs[graph_key]

        graph = nx.Graph()
        try:
            existing = self._collection.get(
                where=_where(tenant_id, source_id, "schema"),
                include=["metadatas"],
            )
            if existing and existing["metadatas"]:
                for table_id, meta in zip(existing["ids"], existing["metadatas"]):
                    table_name = meta.get("table", table_id)
                    cols = meta.get("columns", "").split(",") if meta.get("columns") else []
                    graph.add_node(table_name, columns=[c.strip() for c in cols if c.strip()])
                    raw_fks = meta.get("foreign_keys", "")
                    if raw_fks:
                        try:
                            for fk in json.loads(raw_fks):
                                graph.add_edge(table_name, fk["to"], via=fk["via"])
                        except Exception:
                            pass
        except Exception:
            logger.exception("Failed to load graph from Chroma for %s/%s", tenant_id, source_id)

        self._graphs[graph_key] = graph
        return graph

    async def ingest(self, tenant_id: str, source_id: str, tables: dict) -> None:
        """Embed schema documents and upsert into ChromaDB."""
        graph_key = f"{tenant_id}_{source_id}"
        # Always rebuild graph on ingest — don't use stale cache
        self._graphs.pop(graph_key, None)
        graph = self._ensure_graph_loaded(tenant_id, source_id)

        ids: list[str] = []
        texts: list[str] = []
        metadatas: list[dict] = []

        for table_name, meta in tables.items():
            graph.add_node(table_name, columns=meta.get("columns", []))
            fk_list = []
            for fk in meta.get("foreign_keys", []):
                graph.add_edge(table_name, fk["references"], via=fk["column"])
                fk_list.append({"to": fk["references"], "via": fk["column"]})

            ids.append(f"{tenant_id}_{source_id}_{table_name}")
            texts.append(meta.get("ddl") or f"Table: {table_name}\nColumns: {', '.join(meta.get('columns', {}).keys())}")
            metadatas.append({
                "tenant_id": tenant_id,
                "source_id": source_id,
                "type": "schema",
                "table": table_name,
                "columns": ",".join(meta.get("columns", [])),
                "foreign_keys": json.dumps(fk_list) if fk_list else "",
            })

            description = meta.get("description", f"Table containing {table_name} data.")
            ids.append(f"{tenant_id}_{source_id}_summary_{table_name}")
            texts.append(f"Table: {table_name} | Description: {description}")
            metadatas.append({
                "tenant_id": tenant_id,
                "source_id": source_id,
                "type": "table_summary",
                "table": table_name,
            })

            samples = meta.get("sample_data", [])
            if samples:
                ids.append(f"{tenant_id}_{source_id}_sample_{table_name}")
                texts.append(f"Sample rows for table {table_name}:\n" + json.dumps(samples, indent=2))
                metadatas.append({
                    "tenant_id": tenant_id,
                    "source_id": source_id,
                    "type": "sample_data",
                    "table": table_name,
                })

        embeddings = await self._embed(texts)
        self._collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
        self._graphs[graph_key] = graph
        logger.info("Ingested %d documents for %s/%s", len(ids), tenant_id, source_id)

    async def retrieve(self, tenant_id: str, source_id: str, question: str, n_results: int = 5) -> str:
        """Pure vector similarity retrieval."""
        try:
            q_embedding = await self._embed([question])

            vector_results = self._collection.query(
                query_embeddings=q_embedding,
                n_results=n_results,
                where=_where(tenant_id, source_id, "schema"),
                include=["metadatas"],
            )

            ordered: list[str] = []
            seen: set[str] = set()
            if vector_results and vector_results["metadatas"] and vector_results["metadatas"][0]:
                for meta in vector_results["metadatas"][0]:
                    t = meta.get("table")
                    if t and t not in seen:
                        ordered.append(t)
                        seen.add(t)

            logger.info("Retrieve %s/%s: vector=%d tables=%s", tenant_id, source_id, len(ordered), ordered)

            final_lines: list[str] = []
            total_tokens = 0
            for table in ordered:
                res = self._collection.get(
                    ids=[f"{tenant_id}_{source_id}_{table}"], include=["documents"]
                )
                if not (res and res["documents"] and res["documents"][0]):
                    continue
                ddl = res["documents"][0]
                tokens = self._count_tokens(ddl)
                if total_tokens + tokens > settings.max_schema_tokens:
                    break
                final_lines.append(ddl)
                total_tokens += tokens

                s_res = self._collection.get(
                    ids=[f"{tenant_id}_{source_id}_sample_{table}"], include=["documents"]
                )
                if s_res and s_res["documents"] and s_res["documents"][0]:
                    sample_text = f"\n-- Sample rows for {table}:\n" + s_res["documents"][0]
                    s_tokens = self._count_tokens(sample_text)
                    if total_tokens + s_tokens <= settings.max_schema_tokens:
                        final_lines.append(sample_text)
                        total_tokens += s_tokens

            return "\n\n".join(final_lines) if final_lines else "No schema context found."

        except Exception:
            logger.exception("retrieve() failed for %s/%s", tenant_id, source_id)
            return "No schema context found."

    async def search_table_summaries(self, tenant_id: str, source_id: str, question: str, n_results: int = 10) -> list[dict]:
        """Vector search over table summaries for routing."""
        try:
            q_embedding = await self._embed([question])
            results = self._collection.query(
                query_embeddings=q_embedding,
                n_results=n_results,
                where=_where(tenant_id, source_id, "table_summary"),
                include=["documents", "metadatas"],
            )
            if not (results and results["metadatas"] and results["metadatas"][0]):
                logger.warning("search_table_summaries returned no results for %s/%s", tenant_id, source_id)
                return []
            return [
                {"table": meta["table"], "summary": doc}
                for meta, doc in zip(results["metadatas"][0], results["documents"][0])
            ]
        except Exception:
            logger.exception("search_table_summaries() failed for %s/%s", tenant_id, source_id)
            return []

    async def retrieve_exact(self, tenant_id: str, source_id: str, tables: list[str]) -> str:
        """Retrieve exact DDLs plus FK neighbors, including sample rows."""
        if not tables:
            return "No schema context found."

        graph = self._ensure_graph_loaded(tenant_id, source_id)
        resolved: list[str] = []
        for t in tables:
            if t in graph:
                resolved.append(t)
            else:
                match = next(
                    (n for n in graph.nodes if n.endswith(f".{t}") or n.lower() == t.lower()),
                    None,
                )
                resolved.append(match if match else t)

        related: set[str] = set(resolved)
        for t in resolved:
            if t in graph:
                related.update(nx.neighbors(graph, t))

        lines: list[str] = []
        total_tokens = 0
        for table in resolved + [t for t in related if t not in resolved]:
            res = self._collection.get(
                ids=[f"{tenant_id}_{source_id}_{table}"], include=["documents"]
            )
            if not (res and res["documents"] and res["documents"][0]):
                continue
            ddl = res["documents"][0]
            tokens = self._count_tokens(ddl)
            if total_tokens + tokens > settings.max_schema_tokens:
                continue
            lines.append(ddl)
            total_tokens += tokens

            s_res = self._collection.get(
                ids=[f"{tenant_id}_{source_id}_sample_{table}"], include=["documents"]
            )
            if s_res and s_res["documents"] and s_res["documents"][0]:
                sample_text = f"\n-- Sample rows for {table}:\n" + s_res["documents"][0]
                s_tokens = self._count_tokens(sample_text)
                if total_tokens + s_tokens <= settings.max_schema_tokens:
                    lines.append(sample_text)
                    total_tokens += s_tokens

        return "\n\n".join(lines) if lines else "No schema context found."

    async def ingest_example(self, tenant_id: str, source_id: str, question: str, sql: str) -> None:
        import hashlib
        doc_id = f"{tenant_id}_{source_id}_ex_{hashlib.sha256(question.encode()).hexdigest()}"
        embedding = await self._embed([question])
        self._collection.upsert(
            ids=[doc_id],
            documents=[question],
            embeddings=embedding,
            metadatas=[{"tenant_id": tenant_id, "source_id": source_id, "type": "example", "sql": sql}],
        )

    async def search_semantic_cache(self, tenant_id: str, source_id: str, question: str, threshold: float = 0.15) -> dict | None:
        try:
            q_embedding = await self._embed([question])
            results = self._collection.query(
                query_embeddings=q_embedding,
                n_results=1,
                where=_where(tenant_id, source_id, "example"),
                include=["metadatas", "distances"],
            )
            if not (results.get("distances") and results["distances"][0]):
                return None
            distance = results["distances"][0][0]
            if distance <= threshold:
                meta = results["metadatas"][0][0]
                return {"sql": meta.get("sql", ""), "distance": distance}
            return None
        except Exception:
            logger.exception("search_semantic_cache() failed for %s/%s", tenant_id, source_id)
            return None

    async def retrieve_examples(self, tenant_id: str, source_id: str, question: str, n_results: int = 2) -> str:
        try:
            q_embedding = await self._embed([question])
            results = self._collection.query(
                query_embeddings=q_embedding,
                n_results=n_results,
                where=_where(tenant_id, source_id, "example"),
                include=["documents", "metadatas"],
            )
            if not (results.get("metadatas") and results["metadatas"][0]):
                return ""
            lines = [
                f"Q: {q}\nSQL: {meta.get('sql', '')}"
                for q, meta in zip(results["documents"][0], results["metadatas"][0])
            ]
            return "\n\n".join(lines)
        except Exception:
            logger.exception("retrieve_examples() failed for %s/%s", tenant_id, source_id)
            return ""
