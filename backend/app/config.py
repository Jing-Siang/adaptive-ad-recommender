from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    pinecone_api_key: str = ""
    pinecone_index_name: str = "adaptive-ad-recommender"
    pinecone_environment: str = ""

    database_url: str = "postgresql+psycopg://ad_recommender:ad_recommender@localhost:5432/ad_recommender"
    redis_url: str = "redis://localhost:6379"

    claude_model: str = "claude-sonnet-5"
    voyage_index_model: str = "voyage-4-large"
    voyage_query_model: str = "voyage-4-lite"

    slack_webhook_url: str = ""
    mcp_server_url: str = ""

    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"


settings = Settings()
