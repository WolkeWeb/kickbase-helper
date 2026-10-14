from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration from environment variables (in Docker via `env_file: .env`).

    Secrets that must never live in the environment (Kickbase credentials) are read from
    files in `secrets_dir`; see `app.secret_files`.
    """

    # hide_input_in_errors: validation errors must never echo values from .env into logs.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    app_password_hash: str = Field(min_length=1)
    session_secret: str = Field(min_length=32)
    session_max_age_days: int = 30
    # Secure cookie flag; only disabled in tests that talk plain HTTP.
    session_https_only: bool = True

    database_url: str = "postgresql+psycopg://kickbase:kickbase@localhost:5432/kickbase"
    secrets_dir: Path = Path("/run/secrets")

    kickbase_league_name: str = "Meine Liga"
    # Telegram notifications (worker): chat that receives them; the bot token is a Docker secret.
    telegram_chat_id: str | None = None
    # Links in notifications point to the configured installation URL.
    public_url: str = "https://kickbase.example.com"
    # Language model (OpenAI-compatible chat completions); the key is the Docker secret
    # `llm_api_key`, without it everything runs without AI. Limits: config/ai.yaml.
    llm_base_url: str = "https://api.mistral.ai/v1"
    llm_model: str = "ministral-14b-latest"
    llm_fallback_model: str | None = "ministral-8b-latest"
    # Claude im Telegram-Chat: Bridge auf der VM (deploy/claude-bridge) und Port der
    # Kickbase-Werkzeuge im Worker; aktiv nur mit dem Secret `claude_bridge_token`.
    claude_bridge_url: str = "http://host.docker.internal:8767"
    claude_mcp_port: int = 8768
    # Gentle API access: minimum gap between requests and cache lifetimes in seconds. Each
    # process sends one request at a time (app + worker together at most 2, see factory).
    kickbase_min_interval: float = Field(default=0.25, ge=0)
    # Bulk jobs of the worker go slower: one request per second.
    worker_min_interval: float = Field(default=1.0, ge=0.25)
    cache_ttl_live: int = Field(default=600, ge=60)
    cache_ttl_player: int = Field(default=1800, ge=60)
    cache_ttl_static: int = Field(default=86400, ge=60)
    tz: str = "Europe/Berlin"
    log_level: str = "INFO"

    @field_validator("app_password_hash")
    @classmethod
    def _require_argon2_hash(cls, value: str) -> str:
        """Fail at startup instead of rejecting every login with "wrong password"."""
        value = value.strip()
        try:
            PasswordHasher().verify(value, "")
        except VerifyMismatchError:
            return value  # well-formed hash, just not the empty password
        except (VerificationError, InvalidHashError) as exc:
            raise ValueError(
                f"APP_PASSWORD_HASH ist kein gültiger Argon2-Hash ({type(exc).__name__}: {exc}); "
                "neu erzeugen mit scripts/hash_password.py"
            ) from None
        raise ValueError("APP_PASSWORD_HASH darf kein leeres Passwort erlauben")


@lru_cache
def get_settings() -> Settings:
    return Settings()
