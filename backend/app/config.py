"""Application configuration, loaded from environment / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default so the project runs with zero local setup.
    # Swap to postgresql+psycopg2://... in .env for the production target.
    database_url: str = "sqlite:///./expense_intelligence.db"
    upload_dir: str = "./uploads"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    # Path to the built React app (frontend/dist). When set, the API also serves
    # the SPA from the same origin — no CORS/cookie cross-site issues in prod.
    static_dir: str = ""

    # --- Weekly digest / email ---
    # If SMTP isn't configured, digests are written to digest_outbox/ instead of
    # being emailed, so the feature is fully demoable without credentials.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    # Brevo HTTP API key (starts with "xkeysib-"). Preferred in production: many
    # hosts (Render, etc.) block outbound SMTP ports, but HTTPS (443) is always
    # open. When set, email is sent via the API instead of SMTP.
    brevo_api_key: str = ""
    digest_from: str = "digest@expense-intelligence.local"
    digest_from_name: str = "Expense Intelligence"
    digest_to: str = ""  # falls back to digest_from when empty
    digest_outbox: str = "./digest_outbox"
    # Scheduler cadence. By default the digest runs weekly (Monday 08:00). Set a
    # positive value to run every N minutes instead — handy for a live demo.
    digest_interval_minutes: int = 0

    # --- Auth (JWT + refresh cookies) ---
    # Override jwt_secret_key in production (e.g. `openssl rand -hex 32`).
    jwt_secret_key: str = "dev-insecure-change-me-in-production"
    jwt_algorithm: str = "HS256"
    # Short-lived access token; a refresh token (httpOnly cookie) mints new ones.
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    # Cookie attributes. Set cookie_secure=true behind HTTPS in production; use
    # samesite="none" (with secure) only if the API is on a different site.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    @property
    def using_default_secret(self) -> bool:
        return self.jwt_secret_key == "dev-insecure-change-me-in-production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
