"""Central application configuration (12-factor, env-driven)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DigiTwin API"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./digitwin.db"
    jwt_secret_key: str = "change-me-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    cors_origins: str = "http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174,http://localhost:19006"
    log_level: str = "INFO"
    # Debt cleanup: brute-force protection + cookie transport toggles.
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 60
    cookie_secure: bool = False  # True in production (HTTPS)
    cookie_samesite: str = "lax"  # lax|strict|none
    sentry_dsn: str = ""  # empty = error tracking disabled (dev default)
    # Password-reset email delivery (P0-2A). email_backend="log" (dev default)
    # records metadata only and never logs tokens; "smtp" delivers via SMTP.
    email_backend: str = "log"  # log|smtp
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@localhost"
    smtp_use_tls: bool = True  # STARTTLS on the submission port
    app_base_url: str = "http://localhost:5173"  # public web origin for reset links
    # Set 3 Mentor provider (optional). Default "fallback": deterministic,
    # grounded, no network, no key required. Set mentor_provider="http" plus
    # url/key/model to enable an OpenAI-compatible LLM; any failure degrades
    # to the fallback. Env-based so deployments can switch without clients.
    mentor_provider: str = "fallback"  # fallback|http
    mentor_api_url: str = ""
    mentor_api_key: str = ""
    mentor_model: str = ""
    mentor_timeout_seconds: float = 20.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def smtp_configured(self) -> bool:
        """True only when SMTP delivery is selected AND has a host to dial."""
        return self.email_backend == "smtp" and bool(self.smtp_host.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
