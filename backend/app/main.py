from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import comparisons, invoices
from app.core.config import get_settings
from app.db.session import SessionFactory, engine
from app.services.comparison import recover_stale_tasks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    recovered = await recover_stale_tasks()
    if recovered:
        logger.warning("Marcati come falliti %d job orfani di un processo precedente", recovered)
    yield
    await engine.dispose()


settings = get_settings()
app = FastAPI(title="Energy SaaS API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Filename"],
)
app.include_router(invoices.router, prefix="/api/v1")
app.include_router(comparisons.router, prefix="/api/v1")


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Pingato dal cron esterno: tiene sveglio Render E genera attività DB su Supabase."""
    async with SessionFactory() as session:
        await session.execute(text("SELECT 1 FROM users LIMIT 1"))  # query reale: conta come attività
    return {"status": "ok"}
