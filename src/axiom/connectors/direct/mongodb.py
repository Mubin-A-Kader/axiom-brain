import json
import logging
from typing import Any, Dict, Optional

from axiom.connectors.base import BaseConnector, QueryMode

logger = logging.getLogger(__name__)

# Max documents sampled per collection during schema introspection
_SAMPLE_SIZE = 50
# Max collections introspected per database (safety for very wide schemas)
_MAX_COLLECTIONS = 60


def _infer_type(value: Any) -> str:
    """Map a Python/BSON value to a human-readable type name for the LLM."""
    from bson import ObjectId, Decimal128
    from datetime import datetime
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int):
        return "Int"
    if isinstance(value, float):
        return "Float"
    if isinstance(value, str):
        return "String"
    if isinstance(value, datetime):
        return "Date"
    if isinstance(value, ObjectId):
        return "ObjectId"
    if isinstance(value, Decimal128):
        return "Decimal"
    if isinstance(value, list):
        return "Array"
    if isinstance(value, dict):
        return "Object"
    return "Mixed"


def _merge_field_types(existing: str, new_type: str) -> str:
    """Track when a field has mixed types across documents."""
    if existing == new_type or existing == "Mixed":
        return existing
    return "Mixed"


def _extract_fields(doc: dict, prefix: str = "") -> Dict[str, str]:
    """Flatten one document level into {dotted.path: type} pairs."""
    fields: Dict[str, str] = {}
    for k, v in doc.items():
        path = f"{prefix}.{k}" if prefix else k
        fields[path] = _infer_type(v)
        # One level of nested object expansion (avoid infinite depth)
        if isinstance(v, dict) and not prefix:
            for nk, nv in v.items():
                fields[f"{k}.{nk}"] = _infer_type(nv)
    return fields


def _serialize(value: Any) -> Any:
    """Make BSON types JSON-serialisable."""
    from bson import ObjectId, Decimal128
    from datetime import datetime, date
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal128):
        return float(str(value))
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(i) for i in value]
    return value


class MongoDBConnector(BaseConnector):
    """
    Async MongoDB connector using Motor.

    query_mode = PIPELINE: the LLM generates MongoDB aggregation pipeline JSON,
    not SQL SELECT. The lake worker uses dialect instructions to produce the correct
    format and does not apply SQL SELECT validation.

    `execute_query` accepts a JSON string produced by the LLM:
        {"collection": "appointments", "pipeline": [...aggregation stages...]}

    `get_schema` samples documents from every collection and infers a
    field-type map that the LLM can use to write aggregation pipelines.
    """

    query_mode = QueryMode.PIPELINE

    def __init__(self, source_id: str, db_url: str, config: Optional[Dict[str, Any]] = None):
        super().__init__(source_id, db_url, config)
        self._client = None
        self._db = None

    @property
    def dialect_name(self) -> str:
        return "mongodb"

    @property
    def llm_prompt_instructions(self) -> str:
        return """
MONGODB QUERY RULES — you MUST follow these exactly:

1. Output a single JSON object, nothing else:
   {"collection": "<collection_name>", "pipeline": [<aggregation stages>]}

2. Use standard MongoDB aggregation stages: $match, $group, $sort, $limit,
   $project, $lookup, $unwind, $count, $addFields, $facet.

3. Date comparisons require ISODate-compatible strings:
   {"$match": {"date": {"$gte": {"$date": "2024-01-01T00:00:00Z"}}}}

4. To count documents: [{"$count": "total"}]

5. Nested field access uses dot notation: "patient.name", "address.city"

6. NEVER output SQL. NEVER wrap the JSON in markdown code fences.
   Output ONLY the raw JSON object.
        """.strip()

    def build_query_prompt(self, question: str, schema_context: str, custom_rules: str, few_shot_examples: str, history_context: str) -> str:
        return (
            f"You are a MongoDB aggregation pipeline expert.\n"
            f"Target database: MONGODB\n\n"
            f"### SCHEMA:\n{schema_context}\n\n"
            f"### BUSINESS GLOSSARY:\n{custom_rules or 'None'}\n\n"
            f"### EXAMPLES:\n{few_shot_examples or 'None'}\n\n"
            f"### HISTORY:\n{history_context or 'None'}\n\n"
            f"### DIALECT RULES:\n{self.llm_prompt_instructions}\n\n"
            f"Generate a MongoDB aggregation pipeline to answer: {question}\n"
        )

    def extract_query(self, llm_content: str) -> Optional[str]:
        import re, json
        content = llm_content.replace("```json", "").replace("```", "").strip()
        try:
            json.loads(content)
            return content
        except json.JSONDecodeError:
            match = re.search(r'(\{.*?"collection".*?\})', content, re.DOTALL)
            return match.group(1).strip() if match else None

    def is_read_only_query(self, query: str) -> bool:
        import json
        try:
            parsed = json.loads(query)
            pipeline = parsed.get("pipeline", [])
            write_stages = {"$out", "$merge"}
            return not any(stage.keys() & write_stages for stage in pipeline if isinstance(stage, dict))
        except Exception:
            return False

    async def connect(self) -> None:
        if self._client:
            return
        import motor.motor_asyncio as motor
        db_name = self.config.get("database") or self._parse_db_name()
        # Cap pool size: 100 sites × 5 connections = 500 max — within Atlas M10 limits.
        # Raise maxPoolSize if a single site needs high concurrency.
        # SECLEVEL=1 is set globally in /etc/ssl/openssl.cnf (Dockerfile).
        # Only inject tls=True if the URI doesn't already carry ssl=/tls=.
        tls_kwargs = {}
        if ".mongodb.net" in self.db_url and "tls=" not in self.db_url and "ssl=" not in self.db_url:
            tls_kwargs["tls"] = True
        self._client = motor.AsyncIOMotorClient(
            self.db_url,
            maxPoolSize=self.config.get("max_pool_size", 5),
            minPoolSize=0,
            serverSelectionTimeoutMS=10000,
            **tls_kwargs,
        )
        self._db = self._client[db_name]
        await self._client.admin.command("ping")
        logger.info("MongoDB connected: source=%s db=%s", self.source_id, db_name)

    def _parse_db_name(self) -> str:
        """Extract database name from the connection URL path segment."""
        from axiom.core.cleansing import safe_db_urlparse
        from urllib.parse import unquote
        parsed = safe_db_urlparse(self.db_url)
        db = parsed["path"].lstrip("/").split("?")[0]
        return unquote(db) or "test"

    async def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
            logger.info("MongoDB disconnected: source=%s", self.source_id)

    async def execute_query(self, query: str) -> Dict[str, Any]:
        """
        Execute a MongoDB aggregation pipeline.
        `query` must be a JSON string: {"collection": "...", "pipeline": [...]}
        """
        if self._db is None:
            await self.connect()

        try:
            parsed = json.loads(query)
        except json.JSONDecodeError as exc:
            raise ValueError(f"MongoDB query must be valid JSON. Got: {query[:200]}") from exc

        collection_name = parsed.get("collection")
        pipeline = parsed.get("pipeline", [])

        if not collection_name:
            raise ValueError("MongoDB query JSON must include a 'collection' key.")

        collection = self._db[collection_name]
        cursor = collection.aggregate(pipeline)
        docs = await cursor.to_list(length=500)

        if not docs:
            return {"columns": [], "rows": []}

        # Collect all keys across docs for consistent column ordering
        all_keys: list[str] = []
        seen: set[str] = set()
        for doc in docs:
            for k in doc.keys():
                if k not in seen:
                    all_keys.append(k)
                    seen.add(k)

        rows = [[_serialize(doc.get(k)) for k in all_keys] for doc in docs]
        return {"columns": all_keys, "rows": rows}

    async def get_schema(self) -> Dict[str, Any]:
        """
        Sample documents from every collection and infer a field-type map.
        Returns a dict keyed by collection name, values are {field: type} maps.
        """
        if self._db is None:
            await self.connect()

        schema: Dict[str, Any] = {}
        collection_names = await self._db.list_collection_names()
        collection_names = collection_names[:_MAX_COLLECTIONS]

        logger.info(
            "MongoDB schema introspection: source=%s collections=%d",
            self.source_id, len(collection_names),
        )

        for cname in collection_names:
            try:
                collection = self._db[cname]
                docs = await collection.find({}, {"_id": 1}).limit(_SAMPLE_SIZE).to_list(length=_SAMPLE_SIZE)
                # Re-fetch without projection to get all fields
                ids = [d["_id"] for d in docs]
                docs = await collection.find({"_id": {"$in": ids}}).to_list(length=_SAMPLE_SIZE)

                field_types: Dict[str, str] = {}
                for doc in docs:
                    for field, ftype in _extract_fields(doc).items():
                        if field in field_types:
                            field_types[field] = _merge_field_types(field_types[field], ftype)
                        else:
                            field_types[field] = ftype

                count = await collection.estimated_document_count()
                # Build pseudo-DDL so the onboarding pipeline (which expects a
                # "ddl" key on every table entry) works without modification.
                field_lines = "\n".join(
                    f"  {fname}: {ftype}" for fname, ftype in sorted(field_types.items())
                )
                pseudo_ddl = (
                    f"Collection: {cname}\n"
                    f"Approximate document count: {count}\n"
                    f"Fields:\n{field_lines}"
                )
                schema[cname] = {
                    "fields": field_types,
                    "approx_count": count,
                    "ddl": pseudo_ddl,
                }
            except Exception as exc:
                logger.warning("Schema introspection failed for collection '%s': %s", cname, exc)

        return schema
