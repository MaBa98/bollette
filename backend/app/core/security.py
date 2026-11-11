"""Estrazione dell'identità dal JWT di Supabase Auth. Unica fonte di verità per `user_id`."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=True)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    email: str | None


@lru_cache(maxsize=1)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    # Cache delle chiavi in memoria: niente fetch a ogni richiesta.
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def _decode(token: str, settings: Settings) -> dict[str, object]:
    common = {"audience": settings.jwt_audience, "issuer": settings.jwt_issuer, "options": {"require": ["sub", "exp"]}}
    if settings.jwt_secret:  # progetti Supabase legacy (HS256)
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], **common)
    signing_key = _jwks_client(settings.jwks_url).get_signing_key_from_jwt(token)
    return jwt.decode(token, signing_key.key, algorithms=["ES256", "RS256"], **common)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUser:
    try:
        # Il fetch JWKS è I/O sincrono: fuori dall'event loop.
        claims = await run_in_threadpool(_decode, creds.credentials, settings)
        return CurrentUser(id=UUID(str(claims["sub"])), email=claims.get("email"))  # type: ignore[arg-type]
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido o scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
