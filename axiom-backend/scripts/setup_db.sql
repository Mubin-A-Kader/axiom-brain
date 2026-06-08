-- TO setup db in local
CREATE USER axiom WITH PASSWORD 'axiom';
CREATE DATABASE axiom OWNER axiom;
GRANT ALL PRIVILEGES ON DATABASE axiom TO axiom;
\c axiom
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO axiom;
