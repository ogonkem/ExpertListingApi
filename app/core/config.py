from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://expert:expert@localhost:5432/expert_listing"
    test_database_url: str | None = None
    app_env: Literal["local", "test", "production"] = "local"
    log_level: str = "INFO"
    max_page_size: int = 100
    max_radius_km: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
