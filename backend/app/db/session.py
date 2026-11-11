from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

_settings = get_settings()

_engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
if _settings.db_behind_pgbouncer:
    # PgBouncer in transaction mode: il pooling lo fa lui e i prepared statement vanno spenti.
    _engine_kwargs |= {"poolclass": NullPool, "connect_args": {"statement_cache_size": 0}}
else:
    # Free tier: poche connessioni disponibili, pool piccolo e riciclato.
    _engine_kwargs |= {"pool_size": 5, "max_overflow": 2, "pool_recycle": 1800}

engine = create_async_engine(str(_settings.database_url), **_engine_kwargs)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
