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
        "description": "Contains user accounts and basic customer profiles, including names and email addresses.",
    },
    "products": {
        "ddl": "CREATE TABLE products (id SERIAL PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price NUMERIC(10,2) NOT NULL, stock_qty INTEGER NOT NULL DEFAULT 0)",
        "columns": ["id", "name", "category", "price", "stock_qty"],
        "foreign_keys": [],
        "description": "Catalog of all items available for purchase, including their categories, pricing, and current inventory stock quantity.",
    },
    "orders": {
        "ddl": "CREATE TABLE orders (id SERIAL PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), status TEXT NOT NULL DEFAULT 'pending', total NUMERIC(10,2) NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())",
        "columns": ["id", "customer_id", "status", "total", "created_at"],
        "foreign_keys": [{"column": "customer_id", "references": "customers"}],
        "description": "High-level purchase transactions representing a shopping cart checkout. Contains the final total revenue, order status (e.g., completed, pending), and links to the customer.",
    },
    "order_items": {
        "ddl": "CREATE TABLE order_items (id SERIAL PRIMARY KEY, order_id INTEGER REFERENCES orders(id), product_id INTEGER REFERENCES products(id), quantity INTEGER NOT NULL, unit_price NUMERIC(10,2) NOT NULL)",
        "columns": ["id", "order_id", "product_id", "quantity", "unit_price"],
        "foreign_keys": [
            {"column": "order_id", "references": "orders"},
            {"column": "product_id", "references": "products"},
        ],
        "description": "Line items for each order mapping exactly which products were purchased, the quantity bought, and the individual unit price at the time of purchase.",
    },
}

if __name__ == "__main__":
    rag = SchemaRAG()
    tenant_id = "default_tenant"
    
    # Ingest schemas
    rag.ingest(tenant_id, SCHEMA)
    
    # Ingest few-shot examples
    examples = [
        {
            "question": "What is the total revenue from completed orders?",
            "sql": "SELECT SUM(total) FROM orders WHERE status = 'completed'"
        },
        {
            "question": "Which product category has the most stock?",
            "sql": "SELECT category FROM products GROUP BY category ORDER BY SUM(stock_qty) DESC LIMIT 1"
        },
        {
            "question": "Find the name of the customer who ordered a 'Wireless Mouse'",
            "sql": "SELECT c.name FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id WHERE p.name = 'Wireless Mouse'"
        }
    ]
    rag.ingest_examples(tenant_id, examples)
    
    print(f"Schema and examples ingested into ChromaDB for tenant '{tenant_id}'.")
