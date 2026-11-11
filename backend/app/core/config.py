from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)  # niente segreti nei log

    environment: str = "dev"
    #: postgresql+asyncpg://user:pass@host:5432/postgres  (Supabase: usare il Session pooler)
    #: Accetta anche l'URI copiato così com'è da Supabase (postgresql://...): lo schema
    #: viene convertito in postgresql+asyncpg:// automaticamente.
    database_url: str
    #: True se si punta al Transaction pooler di Supabase (porta 6543) o al pooler Neon:
    #: disabilita i prepared statement di asyncpg, incompatibili con PgBouncer in transaction mode.
    db_behind_pgbouncer: bool = False

    # --- Auth (Supabase Auth) ---
    supabase_url: str = Field(description="https://<ref>.supabase.co")
    #: Progetti con le nuove chiavi asimmetriche: verifica via JWKS (consigliato).
    #: Progetti legacy HS256: valorizzare jwt_secret.
    jwt_secret: str | None = None
    jwt_audience: str = "authenticated"

    #: Accetta JSON (["https://a.app","https://b.app"]), URL separati da virgola o un URL singolo.
    #: NoDecode: disattiva il parsing JSON obbligatorio di pydantic-settings per i campi lista.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    max_upload_bytes: int = 10 * 1024 * 1024

    # --- Comparazione mercato ---
    #: Oltre questa età il catalogo è segnalato come non aggiornato (job giornaliero fermo?).
    offers_max_age_days: int = 3
    #: Dispacciamento regolato (€/kWh), incluso nella "spesa per la vendita" in bolletta ma assente
    #: dagli open data. Valore iniziale ricavato da bollette reali di ottobre 2026 (0,0146–0,0151):
    #: va aggiornato quando ARERA rivede il corrispettivo. Non cambia la classifica, solo i risparmi.
    regulated_dispatch_eur_kwh: float = 0.015
    #: Potenza impegnata ipotizzata quando non nota (3 kW è lo standard domestico).
    default_power_kw: float = 3.0
    task_stale_after_minutes: int = 15
    comparison_top_n: int = 5

    @field_validator("database_url", mode="before")
    @classmethod
    def _asyncpg_scheme(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            for prefix in ("postgresql://", "postgres://"):
                if value.startswith(prefix):
                    return "postgresql+asyncpg://" + value[len(prefix):]
            if not value.startswith("postgresql+asyncpg://"):
                hint = (
                    " Sembra il Project URL di Supabase: serve invece la stringa di connessione del "
                    "Session pooler (Connect → Connection string)."
                    if value.startswith(("http://", "https://"))
                    else ""
                )
                raise ValueError(
                    "DATABASE_URL deve iniziare con postgresql:// "
                    f"(ricevuto un valore che inizia con '{value.split('://')[0]}://').{hint}"
                )
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            return json.loads(raw)
        return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value: Any) -> Any:
        return value or None

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
