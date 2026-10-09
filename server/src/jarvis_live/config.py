from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_LIVE_")

    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis_live"
    log_level: str = "info"


@lru_cache
def get_settings() -> Settings:
    return Settings()
