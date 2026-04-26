from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    litellm_url: str = "http://localhost:8000"
    litellm_key: str = "sk-axiom-local"
    llm_model: str = "gemini-1.5-flash"
    llm_embed_model: str = "text-embedding-3-large"
    llm_temperature: float = 0.0

    # Database
    database_url: str = "postgresql://axiom:axiom@localhost:5432/axiomdb"

    # Infrastructure
    redis_url: str = "redis://localhost:6379"
    chroma_url: str = "http://localhost:8200"
    temporal_url: str = "localhost:7233"
    chroma_collection: str = "schema"
    chroma_token: str = "secret-chroma-token"
    notebook_executor_url: str = "http://localhost:8090"
    notebook_execution_timeout: int = 60
    artifact_root: str = "/tmp/axiom-artifacts"

    # Security
    lakera_api_key: str = ""
    supabase_jwt_secret: str = "super-secret-jwt-token-with-at-least-32-characters-long"
    supabase_jwks_url: str = "http://localhost:9999/.well-known/jwks.json"

    # Agent
    max_correction_attempts: int = 5
    max_schema_tokens: int = 4000

    # Data Lake fan-out
    lake_max_concurrent_workers: int = 5
    lake_worker_timeout_secs: int = 30

    # App connectors
    connector_master_key: str = "change-me-in-production-32-chars!!"

    # App
    log_level: str = "info"


settings = Settings()
