"""Runtime configuration, read from the environment.

Values are read once at import and cached. Anything environment-specific (ports,
CORS origins, the SUMO installation path) belongs here rather than scattered
through modules, so that a run's configuration can be captured verbatim in a
provenance record later.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend settings. Environment variables use the ``EMS_`` prefix."""

    model_config = SettingsConfigDict(
        env_prefix="EMS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "EMS Black Box API"
    environment: str = "development"
    log_level: str = "info"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Vite's dev server. Comma-separated in the environment.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Optional. Nothing depends on the database yet.
    database_url: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor, usable as a FastAPI dependency."""
    return Settings()
