from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://expert:expert@localhost:5432/expert_listing"
    test_database_url: str | None = None
    app_env: Literal["local", "test", "production"] = "local"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    max_page_size: int = 100
    max_radius_km: int = 100
    # Comma-separated in the environment, e.g.
    # CORS_ORIGINS=https://app.expertlisting.ng,http://localhost:3000   ("*" allows any).
    # Empty (the default) disables CORS entirely.
    cors_origins: Annotated[list[str], NoDecode] = []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
