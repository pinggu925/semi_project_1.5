from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/squatcoach"
    redis_url: str = "redis://localhost:6379/0"
    thresholds_path: str = "config/thresholds.toml"
    session_ttl_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
