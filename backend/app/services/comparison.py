"""Confronto con le offerte reali del mercato, eseguito con FastAPI BackgroundTasks.

Ciclo di vita:
  POST /comparisons → riga PENDING + 202 {task_id} → la risposta parte subito
  → BackgroundTasks esegue run_market_comparison() dopo la risposta
  → il frontend fa polling su GET /comparisons/{id} finché status ∈ {succeeded, failed}.

Il catalogo offerte NON viene scaricato qui: lo popola ogni giorno il job GitHub Actions
(app/market/ingest.py). Questo servizio legge il catalogo, filtra le offerte ammissibili per
l'utente e calcola il costo annuo di ciascuna sul suo consumo reale.

Limiti di BackgroundTasks (per questo lo stato vive nel DB): un riavvio del container a metà
job lo perde; `recover_stale_tasks` all'avvio marca questi job come falliti.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ComparisonTask, MarketOffer, MarketSnapshot, TaskStatus, User
from app.db.repositories import InvoiceRepository
from app.db.session import SessionFactory
from app.market import codes as C
from app.market.pricing import PricingInputs, eligible_for, estimate_annual_cost
from app.parsing.contracts import Commodity

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _set_status(session: AsyncSession, task_id: UUID, **values: Any) -> None:
    await session.execute(update(ComparisonTask).where(ComparisonTask.id == task_id).values(**values))
    await session.commit()


def _is_currently_valid(offer: dict[str, Any], now: datetime) -> bool:
    start, end = offer.get("valid_from"), offer.get("valid_to")
    if start and datetime.fromisoformat(start) > now:
        return False
    return not (end and datetime.fromisoformat(end) < now)


def build_ranking(
    offers: list[MarketOffer],
    inputs: PricingInputs,
    *,
    region: str | None,
    current_annual_cost: Decimal | None,
    top_n: int,
    now: datetime,
) -> dict[str, Any]:
    """Funzione pura: ammissibilità → stima → classifica. Testabile senza database."""
    excluded: Counter[str] = Counter()
    ranked: list[dict[str, Any]] = []
    for row in offers:
        offer = row.data
        if not _is_currently_valid(offer, now):
            excluded["scaduta o non ancora attiva"] += 1
            continue
        ok, reason = eligible_for(offer, region=region, annual_consumption=inputs.annual_consumption, power_kw=inputs.power_kw)
        if not ok:
            excluded[reason or "non ammissibile"] += 1
            continue
        estimate = estimate_annual_cost(offer, inputs)
        if not estimate.complete:
            # Una stima incompleta sottostima il costo e finirebbe in cima alla classifica: esclusa.
            excluded["stima incompleta"] += 1
            continue
        cost = Decimal(str(estimate.annual_cost)).quantize(Decimal("0.01"))
        saving = (current_annual_cost - cost).quantize(Decimal("0.01")) if current_annual_cost is not None else None
        ranked.append(
            {
                "offer_code": row.offer_code,
                "supplier_name": row.supplier_name or "Venditore non indicato",
                "offer_name": row.offer_name or row.offer_code,
                "price_kind": offer.get("price_kind"),
                "source": row.source,
                "url": offer.get("url"),
                "estimated_annual_supplier_cost_eur": str(cost),
                "annual_saving_eur": str(saving) if saving is not None else None,
                "saving_pct": (
                    str((saving / current_annual_cost * 100).quantize(Decimal("0.1")))
                    if saving is not None and current_annual_cost
                    else None
                ),
                "breakdown": {k: round(v, 2) for k, v in estimate.breakdown.items()},
                "notes": estimate.notes,
            }
        )
    ranked.sort(key=lambda r: Decimal(r["estimated_annual_supplier_cost_eur"]))
    return {"top_offers": ranked[:top_n], "offers_eligible": len(ranked), "excluded": dict(excluded)}


async def run_market_comparison(task_id: UUID, user_id: UUID, commodity: Commodity) -> None:
    settings = get_settings()
    async with SessionFactory() as session:
        try:
            await _set_status(session, task_id, status=TaskStatus.RUNNING, started_at=_now())

            baseline = await InvoiceRepository(session, user_id).supply_baseline(commodity)
            if baseline is None:
                raise ValueError("Servono bollette per almeno 2 mesi negli ultimi 12 per stimare il consumo annuo")

            snapshot = await session.scalar(select(MarketSnapshot).order_by(MarketSnapshot.published_on.desc()).limit(1))
            if snapshot is None:
                raise ValueError("Catalogo offerte non ancora disponibile: l'importazione giornaliera non è ancora stata eseguita")

            region = await session.scalar(select(User.region_code).where(User.id == user_id))
            offers = list(
                await session.scalars(
                    select(MarketOffer).where(
                        MarketOffer.published_on == snapshot.published_on,
                        MarketOffer.commodity == commodity.value,
                        MarketOffer.client_type == C.TIPO_CLIENTE_DOMESTICO,
                    )
                )
            )

            electricity = commodity is Commodity.ELECTRICITY
            indices = {name: v["last"] for name, v in snapshot.index_values.items() if v.get("last") is not None}
            inputs = PricingInputs(
                annual_consumption=float(baseline.annual_consumption),
                parameters=snapshot.parameters_electricity if electricity else snapshot.parameters_gas,
                index_values=indices,
                power_kw=settings.default_power_kw,
                regulated_dispatch_eur_kwh=settings.regulated_dispatch_eur_kwh if electricity else 0.0,
            )
            current_cost = baseline.annual_supply_cost_eur
            ranking = build_ranking(
                offers, inputs, region=region, current_annual_cost=current_cost,
                top_n=settings.comparison_top_n, now=datetime.now(),
            )
            top = ranking["top_offers"]
            reference_index = "PUN" if electricity else "PSV"
            index_info = snapshot.index_values.get(reference_index, {})
            age_days = (date.today() - snapshot.published_on).days

            result = {
                "annual_consumption": str(baseline.annual_consumption),
                "unit": "kWh" if electricity else "Smc",
                "billed_days": baseline.billed_days,
                "current_supplier": baseline.supplier_name,
                "current_annual_supplier_cost_eur": str(current_cost) if current_cost is not None else None,
                "best_annual_saving_eur": top[0]["annual_saving_eur"] if top else None,
                "savings_available": current_cost is not None,
                "region_code": region,
                "region_name": C.REGIONS.get(region or ""),
                "catalog_date": snapshot.published_on.isoformat(),
                "catalog_stale": age_days > settings.offers_max_age_days,
                "offers_evaluated": len(offers),
                "offers_eligible": ranking["offers_eligible"],
                "excluded": ranking["excluded"],
                "index_reference": (
                    {"name": reference_index, "value": index_info.get("last"), "month": index_info.get("last_month")}
                    if index_info else None
                ),
                "top_offers": top,
                "methodology": (
                    "Costo annuo della sola quota venditore (spesa per la vendita), IVA esclusa, sul tuo consumo "
                    "annuo stimato. Rete, oneri di sistema e accise sono uguali per tutti i venditori. "
                    "Offerte variabili stimate con l'ultimo valore mensile dell'indice; sconti condizionati esclusi."
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
