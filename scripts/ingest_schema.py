"""Ingest e-commerce schema into ChromaDB for RAG retrieval."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from axiom.rag.schema import SchemaRAG

SCHEMA = {
    "customers": {
        "ddl": "CREATE TABLE customers (id SERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())",
        "columns": ["id", "name", "email", "created_at"],
        "foreign_keys": [],
    },
    "products": {
        "ddl": "CREATE TABLE products (id SERIAL PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price NUMERIC(10,2) NOT NULL, stock_qty INTEGER NOT NULL DEFAULT 0)",
        "columns": ["id", "name", "category", "price", "stock_qty"],
        "foreign_keys": [],
    },
    "orders": {
        "ddl": "CREATE TABLE orders (id SERIAL PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), status TEXT NOT NULL DEFAULT 'pending', total NUMERIC(10,2) NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())",
        "columns": ["id", "customer_id", "status", "total", "created_at"],
        "foreign_keys": [{"column": "customer_id", "references": "customers"}],
    },
    "order_items": {
        "ddl": "CREATE TABLE order_items (id SERIAL PRIMARY KEY, order_id INTEGER REFERENCES orders(id), product_id INTEGER REFERENCES products(id), quantity INTEGER NOT NULL, unit_price NUMERIC(10,2) NOT NULL)",
        "columns": ["id", "order_id", "product_id", "quantity", "unit_price"],
        "foreign_keys": [
            {"column": "order_id", "references": "orders"},
            {"column": "product_id", "references": "products"},
        ],
    },
}

if __name__ == "__main__":
    rag = SchemaRAG()
    rag.ingest(SCHEMA)
    print("Schema ingested into ChromaDB.")
