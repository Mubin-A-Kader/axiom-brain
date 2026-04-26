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

    # n8n integration
    n8n_url: str = "http://localhost:5678"
    n8n_user: str = "axiom@axiom.local"
    n8n_password: str = "axiom-n8n-password"

    # OAuth app credentials — registered once by the platform operator, never exposed to end users
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    salesforce_oauth_client_id: str = ""
    salesforce_oauth_client_secret: str = ""
    slack_oauth_client_id: str = ""
    slack_oauth_client_secret: str = ""

    # Public base URL — used for OAuth redirect URIs
    public_url: str = "http://localhost:8080"

    # App
    log_level: str = "info"


settings = Settings()
