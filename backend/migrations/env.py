"""Alembic async env. Genera le revisioni con: alembic revision --autogenerate -m "init"."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.models import Base

target_metadata = Base.metadata


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(str(get_settings().database_url), connect_args={"statement_cache_size": 0})
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(run_migrations_online())
