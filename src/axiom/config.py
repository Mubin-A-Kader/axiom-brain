from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    litellm_url: str = "http://localhost:8000"
    litellm_key: str = "sk-axiom-local"
    llm_model: str = "gemini-2.0-flash"
    llm_temperature: float = 0.0

    # Database
    database_url: str = "postgresql://axiom:axiom@localhost:5432/axiomdb"

    # Infrastructure
    redis_url: str = "redis://localhost:6379"
    chroma_url: str = "http://localhost:8200"
    chroma_collection: str = "schema"

    # Security
    lakera_api_key: str = ""

    # Agent
    max_correction_attempts: int = 3

    # App
    log_level: str = "info"


settings = Settings()
