import networkx as nx
import chromadb
import tiktoken

from axiom.config import settings


class SchemaRAG:
    def __init__(self) -> None:
        self._client = chromadb.HttpClient(
            host=settings.chroma_url.replace("http://", "").split(":")[0],
            port=int(settings.chroma_url.split(":")[-1]),
            headers={"X-Chroma-Token": settings.chroma_token}
        )
        self._collection = self._client.get_or_create_collection(settings.chroma_collection)
        # key: f"{tenant_id}_{source_id}"
        self._graphs: dict[str, nx.Graph] = {}
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def _ensure_graph_loaded(self, tenant_id: str, source_id: str) -> nx.Graph:
        graph_key = f"{tenant_id}_{source_id}"
        if graph_key in self._graphs:
            return self._graphs[graph_key]
        
        graph = nx.Graph()
        try:
            # Only load schema metadata for this specific tenant + source
            existing = self._collection.get(
                where={"$and": [
                    {"type": "schema"},
                    {"tenant_id": tenant_id},
                    {"source_id": source_id}
                ]}, 
                include=["metadatas"]
            )
            if existing and existing["metadatas"]:
                for table_id, meta in zip(existing["ids"], existing["metadatas"]):
                    table_name = meta.get("table", table_id)
                    cols = meta.get("columns", "").split(",") if meta.get("columns") else []
                    graph.add_node(table_name, columns=[c.strip() for c in cols if c.strip()])
                    # We might need to store edges in metadata too if we want full recovery from Chroma
        except Exception as e:
            logger.warning("Failed to load graph from Chroma for %s: %s", graph_key, e)
            
        self._graphs[graph_key] = graph
        return graph

    def ingest(self, tenant_id: str, source_id: str, tables: dict) -> None:
        """Load table DDL strings and foreign-key relationships into ChromaDB + NetworkX."""
        graph_key = f"{tenant_id}_{source_id}"
        graph = self._ensure_graph_loaded(tenant_id, source_id)
            
        docs, ids, metas = [], [], []
        summary_docs, summary_ids, summary_metas = [], [], []
        
        for table_name, meta in tables.items():
            graph.add_node(table_name, columns=meta.get("columns", []))
            for fk in meta.get("foreign_keys", []):
                graph.add_edge(table_name, fk["references"], via=fk["column"])
            
            # Full DDL for precise generation
            docs.append(meta["ddl"])
            ids.append(f"{tenant_id}_{source_id}_{table_name}")
            metas.append({
                "tenant_id": tenant_id,
                "source_id": source_id, 
                "type": "schema", 
                "table": table_name, 
                "columns": ",".join(meta.get("columns", []))
            })
            
            # High-level summary for the router
            description = meta.get("description", f"Table containing {table_name} data.")
            summary_docs.append(f"Table: {table_name} | Description: {description}")
            summary_ids.append(f"{tenant_id}_{source_id}_summary_{table_name}")
            summary_metas.append({
                "tenant_id": tenant_id,
                "source_id": source_id, 
                "type": "table_summary", 
                "table": table_name
            })

        if docs:
            self._collection.upsert(documents=docs, ids=ids, metadatas=metas)
        if summary_docs:
            self._collection.upsert(documents=summary_docs, ids=summary_ids, metadatas=summary_metas)
            
    async def ingest_example(self, tenant_id: str, source_id: str, question: str, sql: str) -> None:
        """Load a single successful query into ChromaDB for semantic caching and few-shot prompting."""
        import hashlib
        doc_id = f"{tenant_id}_{source_id}_ex_{hashlib.sha256(question.encode()).hexdigest()}"
        self._collection.upsert(
            documents=[question], 
            ids=[doc_id], 
            metadatas=[{"tenant_id": tenant_id, "source_id": source_id, "type": "example", "sql": sql}]
        )

    async def search_semantic_cache(self, tenant_id: str, source_id: str, question: str, threshold: float = 0.15) -> dict | None:
        """Vector search to find if a semantically identical query exists for this specific tenant."""
        try:
            results = self._collection.query(
                query_texts=[question], 
                n_results=1, 
                where={"$and": [
                    {"tenant_id": tenant_id},
                    {"source_id": source_id}, 
                    {"type": "example"}
                ]},
                include=["metadatas", "distances", "documents"]
            )
            if not results.get("distances") or not results["distances"][0]:
                return None
            
            distance = results["distances"][0][0]
            if distance <= threshold:
                meta = results["metadatas"][0][0]
                return {"sql": meta.get("sql", ""), "distance": distance}
            return None
        except Exception:
            return None

    def ingest_examples(self, tenant_id: str, source_id: str, examples: list[dict]) -> None:
        """Load few-shot SQL examples for a specific tenant and source."""
        docs, ids, metas = [], [], []
        for i, ex in enumerate(examples):
            docs.append(ex["question"])
            ids.append(f"{tenant_id}_{source_id}_ex_{i}")
            metas.append({
                "tenant_id": tenant_id,
                "source_id": source_id, 
                "type": "example", 
                "sql": ex["sql"]
            })
        if docs:
            self._collection.upsert(documents=docs, ids=ids, metadatas=metas)

    async def search_table_summaries(self, tenant_id: str, source_id: str, question: str, n_results: int = 10) -> list[dict]:
        """Vector search over high-level table summaries, restricted to the active tenant."""
        try:
            results = self._collection.query(
                query_texts=[question], 
                n_results=n_results, 
                where={"$and": [
                    {"tenant_id": tenant_id},
                    {"source_id": source_id}, 
                    {"type": "table_summary"}
                ]}
            )
            if not results.get("metadatas") or not results["metadatas"][0]:
                return []
            
            summaries = []
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                summaries.append({
                    "table": meta["table"],
                    "summary": doc
                })
            return summaries
        except Exception:
            return []

    async def retrieve(self, tenant_id: str, source_id: str, question: str, n_results: int = 5) -> str:
        """Vector search over DDLs, restricted to the active tenant."""
        try:
            results = self._collection.query(
                query_texts=[question], 
                n_results=n_results, 
                where={"$and": [
                    {"tenant_id": tenant_id},
                    {"source_id": source_id}, 
                    {"type": "schema"}
                ]}
            )
            if not results.get("documents") or not results["documents"][0]:
                return "No schema context found."
            
            docs = []
            total_tokens = 0
            for ddl in results["documents"][0]:
                tokens = self._count_tokens(ddl)
                if total_tokens + tokens > settings.max_schema_tokens:
                    break
                docs.append(ddl)
                total_tokens += tokens
                
            return "\n\n".join(docs) if docs else "No schema context found."
        except Exception:
            return "No schema context found."

    async def retrieve_exact(self, tenant_id: str, source_id: str, tables: list[str]) -> str:
        """Retrieve exact DDLs for this tenant using table names, plus neighbors within token limits."""
        if not tables:
            return "No schema context found."
            
        related: set[str] = set(tables)
        graph = self._ensure_graph_loaded(tenant_id, source_id)
        
        # Priority 1: Exact tables
        # Priority 2: Direct neighbors
        for t in tables:
            if t in graph:
                related.update(nx.neighbors(graph, t))

        lines: list[str] = []
        total_tokens = 0
        
        # Fetch actual DDLs from Chroma
        if related:
            # We want to process tables in 'related' such that the explicitly selected tables come first
            # to ensure they are included if we hit token limits.
            ordered_related = tables + [t for t in related if t not in tables]
            
            for table in ordered_related:
                table_id = f"{tenant_id}_{source_id}_{table}"
                ddl_results = self._collection.get(ids=[table_id], include=["documents"])
                
                if ddl_results and ddl_results["documents"] and ddl_results["documents"][0]:
                    ddl = ddl_results["documents"][0]
                    tokens = self._count_tokens(ddl)
                    if total_tokens + tokens > settings.max_schema_tokens:
                        continue
                    lines.append(ddl)
                    total_tokens += tokens
                else:
                    # Fallback if DDL missing from Chroma but in graph
                    if table in graph:
                        node_data = graph.nodes.get(table, {})
                        cols = ", ".join(node_data.get("columns", []))
                        ddl = f"TABLE {table} ({cols})"
                        tokens = self._count_tokens(ddl)
                        if total_tokens + tokens <= settings.max_schema_tokens:
                            lines.append(ddl)
                            total_tokens += tokens

        return "\n\n".join(lines) if lines else "No schema context found."

    async def retrieve_examples(self, tenant_id: str, source_id: str, question: str, n_results: int = 2) -> str:
        """Retrieve relevant past queries for this specific tenant."""
        try:
            results = self._collection.query(
                query_texts=[question], 
                n_results=n_results, 
                where={"$and": [
                    {"tenant_id": tenant_id},
                    {"source_id": source_id}, 
                    {"type": "example"}
                ]}
            )
            if not results.get("metadatas") or not results["metadatas"][0]:
                return ""
            
            lines = []
            for q, meta in zip(results["documents"][0], results["metadatas"][0]):
                lines.append(f"Q: {q}\nSQL: {meta.get('sql', '')}")
            return "\n\n".join(lines)
        except Exception:
            return ""
