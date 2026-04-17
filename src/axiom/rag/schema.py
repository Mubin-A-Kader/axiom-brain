import networkx as nx
import chromadb

from axiom.config import settings


class SchemaRAG:
    def __init__(self) -> None:
        self._client = chromadb.HttpClient(host=settings.chroma_url.replace("http://", "").split(":")[0],
                                            port=int(settings.chroma_url.split(":")[-1]))
        self._collection = self._client.get_or_create_collection(settings.chroma_collection)
        self._graph: nx.Graph = nx.Graph()

    def ingest(self, tables: dict) -> None:
        """Load table DDL strings and foreign-key relationships into ChromaDB + NetworkX."""
        docs, ids, metas = [], [], []
        for table_name, meta in tables.items():
            self._graph.add_node(table_name, columns=meta.get("columns", []))
            for fk in meta.get("foreign_keys", []):
                self._graph.add_edge(table_name, fk["references"], via=fk["column"])
            docs.append(meta["ddl"])
            ids.append(table_name)
            metas.append({"table": table_name})

        if docs:
            self._collection.upsert(documents=docs, ids=ids, metadatas=metas)

    async def retrieve(self, question: str, n_results: int = 3) -> str:
        results = self._collection.query(query_texts=[question], n_results=n_results)
        seed_tables: list[str] = [m["table"] for m in results["metadatas"][0]]  # type: ignore[index]

        related: set[str] = set(seed_tables)
        for t in seed_tables:
            if t in self._graph:
                related.update(nx.neighbors(self._graph, t))

        lines: list[str] = []
        for table in related:
            node_data = self._graph.nodes.get(table, {})
            cols = ", ".join(node_data.get("columns", []))
            lines.append(f"TABLE {table} ({cols})")

        return "\n".join(lines) if lines else "No schema context found."
