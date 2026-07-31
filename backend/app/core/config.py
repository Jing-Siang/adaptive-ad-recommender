from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""

    pinecone_api_key: str = ""
    pinecone_index_name: str = "adaptive-ad-recommender"

    database_url: str = "postgresql+psycopg://ad_recommender:ad_recommender@localhost:5432/ad_recommender"
    redis_url: str = "redis://localhost:6379"
    kafka_bootstrap_servers: str = "localhost:9094"

    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"


settings = Settings()
