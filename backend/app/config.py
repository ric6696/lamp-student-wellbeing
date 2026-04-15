from pathlib import Path

from pydantic_settings import BaseSettings


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    postgres_db: str = "sensing_db"
    postgres_user: str = "postgres"
    postgres_password: str = "dev_password"
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    ingest_api_key: str = ""
    cors_origins: str = ""
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_model: str = "gpt-5-mini"
    llm_temperature: float = 0.1
    llm_top_p: float = 0.9
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: int = 60
    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_user_password: str = ""
    snowflake_role: str = ""
    snowflake_database: str = ""
    snowflake_schema: str = ""
    snowflake_warehouse: str = ""

    class Config:
        env_file = str(_ENV_FILE)
        extra = "ignore"


settings = Settings()


def get_cors_origins() -> list[str]:
    if not settings.cors_origins:
        return []
    return [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
