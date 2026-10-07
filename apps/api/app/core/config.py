from pathlib import Path
from urllib.parse import urlparse
from pydantic import Field, model_validator

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to the api package root (apps/api/.env),
# not relative to whatever directory the process happens to start in.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    database_url: str = Field(repr=False)
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_model: str = "gemini-3.6-flash"
    github_token: str | None = Field(default=None, repr=False)
    ai_provider_mode: str = "hybrid"  # hybrid | cloud_only | ollama_only
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_timeout: int = 60

    auth_secret_key: str = Field(min_length=32, repr=False)
    auth_cookie_name: str = "codelens_session"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Google OAuth 2.0 / OpenID Connect
    # Set these via environment variables: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
    google_client_id: str | None = None
    google_client_secret: str | None = Field(default=None, repr=False)
    google_redirect_uri: str | None = None

    # Frontend origin – used for post-OAuth redirects.
    # Defaults to localhost:3000 for development.
    frontend_url: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        extra="ignore",
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_browser_security(self):
        origin = urlparse(self.frontend_url)
        if (origin.scheme not in ("http", "https") or not origin.hostname
                or origin.username or origin.password or origin.query or origin.fragment
                or origin.path or "*" in self.frontend_url):
            raise ValueError("FRONTEND_URL must be an exact HTTP(S) origin without a path")
        if self.auth_cookie_samesite not in ("lax", "strict", "none"):
            raise ValueError("AUTH_COOKIE_SAMESITE must be lax, strict or none")
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError("SameSite=None requires AUTH_COOKIE_SECURE")
        if origin.hostname not in ("localhost", "127.0.0.1", "::1"):
            if origin.scheme != "https" or not self.auth_cookie_secure:
                raise ValueError("Non-local browser deployments require HTTPS and secure cookies")
        return self

    @property
    def google_oauth_configured(self) -> bool:
        """True only when all three Google OAuth values are present."""
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_redirect_uri
        )


settings = Settings()
