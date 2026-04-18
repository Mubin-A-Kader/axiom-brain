import networkx as nx
import chromadb

from axiom.config import settings


class SchemaRAG:
    def __init__(self) -> None:
        self._client = chromadb.HttpClient(host=settings.chroma_url.replace("http://", "").split(":")[0],
                                            port=int(settings.chroma_url.split(":")[-1]))
        self._collection = self._client.get_or_create_collection(settings.chroma_collection)
        self._graphs: dict[str, nx.Graph] = {}
        self._load_from_chroma()

    def _load_from_chroma(self) -> None:
        try:
            existing = self._collection.get(where={"type": "schema"}, include=["metadatas"])
            if not existing or not existing["metadatas"]:
                return
            for table_id, meta in zip(existing["ids"], existing["metadatas"]):
                tenant_id = meta.get("tenant_id", "default")
                table_name = meta.get("table", table_id)
                if tenant_id not in self._graphs:
                    self._graphs[tenant_id] = nx.Graph()
                cols = meta.get("columns", "").split(",") if meta.get("columns") else []
                self._graphs[tenant_id].add_node(table_name, columns=[c.strip() for c in cols if c.strip()])
        except Exception:
            pass

    def ingest(self, tenant_id: str, tables: dict) -> None:
        """Load table DDL strings and foreign-key relationships into ChromaDB + NetworkX."""
        if tenant_id not in self._graphs:
            self._graphs[tenant_id] = nx.Graph()
            
        docs, ids, metas = [], [], []
        summary_docs, summary_ids, summary_metas = [], [], []
        
        for table_name, meta in tables.items():
            self._graphs[tenant_id].add_node(table_name, columns=meta.get("columns", []))
            for fk in meta.get("foreign_keys", []):
                self._graphs[tenant_id].add_edge(table_name, fk["references"], via=fk["column"])
            
            # Full DDL for precise generation
            docs.append(meta["ddl"])
            ids.append(f"{tenant_id}_{table_name}")
            metas.append({"tenant_id": tenant_id, "type": "schema", "table": table_name, "columns": ",".join(meta.get("columns", []))})
            
            # High-level summary for the router
            description = meta.get("description", f"Table containing {table_name} data.")
            summary_docs.append(f"Table: {table_name} | Description: {description}")
            summary_ids.append(f"{tenant_id}_summary_{table_name}")
            summary_metas.append({"tenant_id": tenant_id, "type": "table_summary", "table": table_name})

        if docs:
            self._collection.upsert(documents=docs, ids=ids, metadatas=metas)
        if summary_docs:
            self._collection.upsert(documents=summary_docs, ids=summary_ids, metadatas=summary_metas)
            
    def ingest_examples(self, tenant_id: str, examples: list[dict]) -> None:
        """Load few-shot SQL examples for a tenant."""
        docs, ids, metas = [], [], []
        for i, ex in enumerate(examples):
            docs.append(ex["question"])
            ids.append(f"{tenant_id}_ex_{i}")
            metas.append({"tenant_id": tenant_id, "type": "example", "sql": ex["sql"]})
        if docs:
            self._collection.upsert(documents=docs, ids=ids, metadatas=metas)

    async def search_table_summaries(self, tenant_id: str, question: str, n_results: int = 10) -> list[dict]:
        """Vector search over high-level table summaries to find candidates."""
        try:
            results = self._collection.query(
                query_texts=[question], 
                n_results=n_results, 
                where={"$and": [{"tenant_id": tenant_id}, {"type": "table_summary"}]}
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

    async def retrieve_exact(self, tenant_id: str, tables: list[str]) -> str:
        """Retrieve exact DDLs and neighbor tables using exact table names."""
        if not tables:
            return "No schema context found."
            
        related: set[str] = set(tables)
        graph = self._graphs.get(tenant_id, nx.Graph())
        for t in tables:
            if t in graph:
                related.update(nx.neighbors(graph, t))

        lines: list[str] = []
        if related:
            related_ids = [f"{tenant_id}_{t}" for t in related]
            ddl_results = self._collection.get(ids=related_ids, include=["documents"])
            if ddl_results and ddl_results["documents"]:
                for ddl in ddl_results["documents"]:
                    if ddl:
                        lines.append(ddl)
        
        if not lines and related:
            for table in related:
                node_data = graph.nodes.get(table, {})
                cols = ", ".join(node_data.get("columns", []))
                lines.append(f"TABLE {table} ({cols})")

        return "\n\n".join(lines) if lines else "No schema context found."

    async def retrieve_examples(self, tenant_id: str, question: str, n_results: int = 2) -> str:
        """Retrieve the most relevant past successful queries to use as few-shot examples."""
        try:
            results = self._collection.query(
                query_texts=[question], 
                n_results=n_results, 
                where={"$and": [{"tenant_id": tenant_id}, {"type": "example"}]}
            )
            if not results.get("metadatas") or not results["metadatas"][0]:
                return ""
            
            lines = []
            for q, meta in zip(results["documents"][0], results["metadatas"][0]):
                lines.append(f"Q: {q}\nSQL: {meta.get('sql', '')}")
            return "\n\n".join(lines)
        except Exception:
            return ""
