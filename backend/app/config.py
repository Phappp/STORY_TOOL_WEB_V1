from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Story Tool Web V1 API"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    mongodb_uri: str = "mongodb://127.0.0.1:27018/?replicaSet=storytool-dev"
    mongodb_db: str = "story_tool_web_dev"
    mongodb_timeout_ms: int = 750
    frontend_origin: str = "http://127.0.0.1:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
