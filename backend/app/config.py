from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_db: str = "sensing_db"
    postgres_user: str = "postgres"
    postgres_password: str = "dev_password"
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    ingest_api_key: str = ""
    cors_origins: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def get_cors_origins() -> list[str]:
    if not settings.cors_origins:
        return []
    return [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
