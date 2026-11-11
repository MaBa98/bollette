from __future__ import annotations

from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "dev"
    #: postgresql+asyncpg://user:pass@host:5432/postgres  (Supabase: usare il Session pooler)
    database_url: PostgresDsn
    #: True se si punta al Transaction pooler di Supabase (porta 6543) o al pooler Neon:
    #: disabilita i prepared statement di asyncpg, incompatibili con PgBouncer in transaction mode.
    db_behind_pgbouncer: bool = False

    # --- Auth (Supabase Auth) ---
    supabase_url: str = Field(description="https://<ref>.supabase.co")
    #: Progetti con le nuove chiavi asimmetriche: verifica via JWKS (consigliato).
    #: Progetti legacy HS256: valorizzare jwt_secret.
    jwt_secret: str | None = None
    jwt_audience: str = "authenticated"

    cors_origins: list[str] = ["http://localhost:5173"]
    max_upload_bytes: int = 10 * 1024 * 1024

    # --- Comparazione mercato ---
    offers_ttl_hours: int = 24
    task_stale_after_minutes: int = 15
    comparison_top_n: int = 5

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
