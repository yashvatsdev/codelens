from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to the api package root (apps/api/.env),
# not relative to whatever directory the process happens to start in.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    database_url: str
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    github_token: str | None = None

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        extra="ignore",
    )


settings = Settings()