from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to the api package root (apps/api/.env),
# not relative to whatever directory the process happens to start in.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    database_url: str
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    github_token: str | None = None
    ai_provider_mode: str = "hybrid"  # hybrid | cloud_only | ollama_only
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_timeout: int = 60

    auth_secret_key: str = "dev_secret_key_change_in_production"
    auth_cookie_name: str = "codelens_session"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        extra="ignore",
    )


settings = Settings()