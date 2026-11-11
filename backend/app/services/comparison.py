"""Job di comparazione eseguito con FastAPI BackgroundTasks.

Ciclo di vita:
  POST /comparisons → riga PENDING + 202 {task_id} → la risposta parte subito
  → BackgroundTasks esegue run_market_comparison() nello stesso processo, DOPO la risposta
  → il frontend fa polling su GET /comparisons/{id} finché status ∈ {succeeded, failed}.

Limiti reali di BackgroundTasks (per questo lo stato vive nel DB e non in memoria):
  * se il container viene riavviato o va in sleep a metà job, il job si perde in silenzio;
    `recover_stale_tasks` all'avvio marca questi job come FAILED, così la UI non resta appesa;
  * nessun retry, nessuna coda distribuita. Quando servirà: ARQ/Celery + Redis, oppure un
    worker separato che fa `SELECT ... FOR UPDATE SKIP LOCKED` su comparison_tasks.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ComparisonTask, MarketOffer, TaskStatus
from app.db.repositories import InvoiceRepository
from app.db.session import SessionFactory
from app.parsing.contracts import Commodity
from app.services.offer_sources import MarketOfferSource, MockPortaleOfferteSource

logger = logging.getLogger(__name__)

# Lock applicativo Postgres: evita che N utenti scatenino N scraping simultanei della stessa fonte.
_SCRAPE_LOCK_KEY = {Commodity.ELECTRICITY: 910_001, Commodity.GAS: 910_002}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_offer_source() -> MarketOfferSource:
    return MockPortaleOfferteSource()


async def _set_status(session: AsyncSession, task_id: UUID, **values: Any) -> None:
    await session.execute(update(ComparisonTask).where(ComparisonTask.id == task_id).values(**values))
    await session.commit()


async def _refresh_offers_if_stale(session: AsyncSession, commodity: Commodity, source: MarketOfferSource) -> None:
    settings = get_settings()
    last = await session.scalar(select(func.max(MarketOffer.scraped_at)).where(MarketOffer.commodity == commodity))
    if last and _now() - last < timedelta(hours=settings.offers_ttl_hours):
        return

    got_lock = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _SCRAPE_LOCK_KEY[commodity]})
    if not got_lock:
        logger.info("Refresh offerte %s già in corso in un altro job: uso il catalogo esistente", commodity)
        return

    offers = await source.fetch(commodity)
    if offers:
        rows = [asdict(o) | {"scraped_at": _now()} for o in offers]
        stmt = pg_insert(MarketOffer).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_offer_code_validity",
            set_={c: stmt.excluded[c] for c in ("energy_price_eur", "fixed_fee_eur_month", "valid_to", "scraped_at")},
        )
        await session.execute(stmt)
    await session.commit()  # rilascia anche l'advisory lock (xact-scoped)


def _offer_annual_cost(offer: MarketOffer, annual_consumption: Decimal) -> Decimal:
    return (offer.energy_price_eur * annual_consumption + offer.fixed_fee_eur_month * 12).quantize(Decimal("0.01"))


def _rank_offers(
    offers: list[MarketOffer],
    annual_consumption: Decimal,
    current_annual_cost: Decimal | None,
    top_n: int,
) -> list[dict[str, Any]]:
    """Ordina le offerte per costo annuo della sola quota venditore (IVA esclusa).

    Trasporto, oneri di sistema e accise sono regolati e uguali per tutti i venditori, quindi
    si elidono: il risparmio sulla quota venditore è anche il risparmio sulla bolletta (al netto
    dell'IVA, che lo amplifica in proporzione).
    """
    ranked: list[dict[str, Any]] = []
    for o in offers:
        cost = _offer_annual_cost(o, annual_consumption)
        saving = (current_annual_cost - cost).quantize(Decimal("0.01")) if current_annual_cost is not None else None
        ranked.append(
            {
                "offer_code": o.offer_code,
                "supplier_name": o.supplier_name,
                "offer_name": o.offer_name,
                "price_type": o.price_type.value,
                "energy_price_eur": str(o.energy_price_eur),
                "fixed_fee_eur_month": str(o.fixed_fee_eur_month),
                "estimated_annual_supplier_cost_eur": str(cost),
                "annual_saving_eur": str(saving) if saving is not None else None,
                "saving_pct": (
                    str((saving / current_annual_cost * 100).quantize(Decimal("0.1")))
                    if saving is not None and current_annual_cost
                    else None
                ),
            }
        )
    ranked.sort(key=lambda r: Decimal(r["estimated_annual_supplier_cost_eur"]))
    return ranked[:top_n]


async def run_market_comparison(task_id: UUID, user_id: UUID, commodity: Commodity) -> None:
    """Entry point del BackgroundTask. Apre una sessione propria: quella della richiesta è già chiusa."""
    settings = get_settings()
    async with SessionFactory() as session:
        try:
            await _set_status(session, task_id, status=TaskStatus.RUNNING, started_at=_now())

            baseline = await InvoiceRepository(session, user_id).supply_baseline(commodity)
            if baseline is None:
                raise ValueError("Servono bollette per almeno 2 mesi negli ultimi 12 per stimare il consumo annuo")

            await _refresh_offers_if_stale(session, commodity, get_offer_source())

            today = _now().date()
            offers = list(
                await session.scalars(
                    select(MarketOffer).where(
                        MarketOffer.commodity == commodity,
                        (MarketOffer.valid_to.is_(None)) | (MarketOffer.valid_to >= today),
                    )
                )
            )
            current_cost = baseline.annual_supply_cost_eur
            top = _rank_offers(offers, baseline.annual_consumption, current_cost, settings.comparison_top_n)
            best_saving = top[0]["annual_saving_eur"] if top else None
            result = {
                "annual_consumption": str(baseline.annual_consumption),
                "unit": "kWh" if commodity is Commodity.ELECTRICITY else "Smc",
                "billed_days": baseline.billed_days,
                "current_supplier": baseline.supplier_name,
                "current_annual_supplier_cost_eur": str(current_cost) if current_cost is not None else None,
                "best_annual_saving_eur": best_saving,
                "savings_available": current_cost is not None,
                "offers_evaluated": len(offers),
                "top_offers": top,
                "methodology": (
                    "Confronto sulla sola quota venditore (spesa per la materia), IVA esclusa: "
                    "trasporto, oneri di sistema e accise sono uguali per tutti i venditori."
                ),
            }
            await _set_status(session, task_id, status=TaskStatus.SUCCEEDED, finished_at=_now(), result=result)
        except Exception as exc:  # il job non deve mai morire senza lasciare traccia nel DB
            logger.exception("Comparazione %s fallita", task_id)
            await session.rollback()
            await _set_status(session, task_id, status=TaskStatus.FAILED, finished_at=_now(), error=str(exc)[:1000])


async def recover_stale_tasks() -> int:
    """Da chiamare all'avvio: i job PENDING/RUNNING orfani appartengono a un processo morto."""
    cutoff = _now() - timedelta(minutes=get_settings().task_stale_after_minutes)
    async with SessionFactory() as session:
        res = await session.execute(
            update(ComparisonTask)
            .where(ComparisonTask.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]), ComparisonTask.created_at < cutoff)
            .values(status=TaskStatus.FAILED, finished_at=_now(), error="Interrotto da un riavvio del server, riprova")
        )
        await session.commit()
        return res.rowcount or 0
