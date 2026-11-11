"""Repository tenant-scoped: l'unico modo in cui l'API accede ai dati dell'utente.

Il `user_id` viene iniettato UNA volta, dal JWT, nel costruttore: nessun metodo lo accetta come
parametro, quindi è impossibile dimenticare il filtro o passare l'id di un altro utente.
Difesa in profondità: le policy RLS in migrations/rls.sql bloccano comunque l'accesso diretto
via API PostgREST di Supabase.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUserDep
from app.db.models import ComparisonTask, Invoice, TaskStatus, User
from app.db.session import SessionDep
from app.parsing.contracts import Commodity, ParsedInvoice


MIN_BILLED_DAYS = 60  # sotto ~2 mesi di storico la stima annua è troppo rumorosa


@dataclass(frozen=True, slots=True)
class SupplyBaseline:
    """Profilo annuo dell'utente. Consumo e costo sono annualizzati sullo STESSO insieme di
    bollette (quelle con la quota materia), così il confronto con le offerte è omogeneo."""

    annual_consumption: Decimal
    annual_supply_cost_eur: Decimal | None
    supplier_name: str | None
    billed_days: int


class TenantRepository:
    def __init__(self, session: AsyncSession, user_id: UUID) -> None:
        self._session = session
        self._user_id = user_id

    async def ensure_user(self, email: str | None) -> None:
        """Upsert idempotente del profilo al primo accesso (in alternativa: trigger su auth.users)."""
        stmt = pg_insert(User).values(id=self._user_id, email=email).on_conflict_do_nothing(index_elements=[User.id])
        await self._session.execute(stmt)


class InvoiceRepository(TenantRepository):
    async def list(self, commodity: Commodity | None = None, limit: int = 36) -> Sequence[Invoice]:
        stmt = select(Invoice).where(Invoice.user_id == self._user_id)
        if commodity is not None:
            stmt = stmt.where(Invoice.commodity == commodity)
        stmt = stmt.order_by(Invoice.period_end.desc()).limit(limit)
        return (await self._session.scalars(stmt)).all()

    async def delete(self, invoice_id: UUID) -> bool:
        """Elimina solo se la bolletta appartiene all'utente corrente."""
        stmt = delete(Invoice).where(Invoice.id == invoice_id, Invoice.user_id == self._user_id)
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def exists_by_hash(self, file_sha256: str) -> bool:
        stmt = select(Invoice.id).where(Invoice.user_id == self._user_id, Invoice.file_sha256 == file_sha256)
        return (await self._session.scalar(stmt)) is not None

    async def create(self, parsed: ParsedInvoice, file_sha256: str) -> Invoice:
        invoice = Invoice(
            user_id=self._user_id,
            file_sha256=file_sha256,
            confidence=Decimal(str(parsed.confidence)),
            warnings=list(parsed.warnings),
            **parsed.model_dump(exclude={"confidence", "warnings"}),
        )
        self._session.add(invoice)
        await self._session.flush()
        return invoice

    async def supply_baseline(self, commodity: Commodity, today: date | None = None) -> SupplyBaseline | None:
        since = (today or date.today()) - timedelta(days=365)
        days = Invoice.period_end - Invoice.period_start + 1
        has_cost = Invoice.supply_cost_eur.is_not(None)
        stmt = select(
            func.sum(Invoice.consumption_value),
            func.sum(days),
            func.sum(Invoice.consumption_value).filter(has_cost),
            func.sum(days).filter(has_cost),
            func.sum(Invoice.supply_cost_eur).filter(has_cost),
        ).where(Invoice.user_id == self._user_id, Invoice.commodity == commodity, Invoice.period_end >= since)
        all_cons, all_days, cost_cons, cost_days, cost_sum = (await self._session.execute(stmt)).one()

        latest_supplier = await self._session.scalar(
            select(Invoice.supplier_name)
            .where(Invoice.user_id == self._user_id, Invoice.commodity == commodity)
            .order_by(Invoice.period_end.desc())
            .limit(1)
        )

        def annualize(value: Decimal, billed: int, q: str) -> Decimal:
            return (Decimal(value) / Decimal(billed) * 365).quantize(Decimal(q))

        if cost_days and cost_days >= MIN_BILLED_DAYS and cost_cons:
            return SupplyBaseline(
                annual_consumption=annualize(cost_cons, cost_days, "1"),
                annual_supply_cost_eur=annualize(cost_sum, cost_days, "0.01"),
                supplier_name=latest_supplier,
                billed_days=int(cost_days),
            )
        if all_days and all_days >= MIN_BILLED_DAYS and all_cons:
            return SupplyBaseline(
                annual_consumption=annualize(all_cons, all_days, "1"),
                annual_supply_cost_eur=None,
                supplier_name=latest_supplier,
                billed_days=int(all_days),
            )
        return None

    async def annualized_consumption(self, commodity: Commodity, today: date | None = None) -> Decimal | None:
        """Consumo annuo stimato dalle bollette degli ultimi 12 mesi, pesato sui giorni fatturati."""
        since = (today or date.today()) - timedelta(days=365)
        days = Invoice.period_end - Invoice.period_start + 1
        stmt = select(func.sum(Invoice.consumption_value), func.sum(days)).where(
            Invoice.user_id == self._user_id,
            Invoice.commodity == commodity,
            Invoice.period_end >= since,
        )
        total, billed_days = (await self._session.execute(stmt)).one()
        if not total or not billed_days or billed_days < 60:
            return None  # meno di ~2 mesi di storico: stima troppo rumorosa
        return (Decimal(total) / Decimal(billed_days) * 365).quantize(Decimal("1"))


class ComparisonTaskRepository(TenantRepository):
    async def get(self, task_id: UUID) -> ComparisonTask | None:
        stmt = select(ComparisonTask).where(ComparisonTask.id == task_id, ComparisonTask.user_id == self._user_id)
        return await self._session.scalar(stmt)

    async def find_active(self, commodity: Commodity) -> ComparisonTask | None:
        """Idempotenza: un solo job attivo per utente e commodity (anti doppio-click e anti abuso)."""
        fresh = datetime.now(timezone.utc) - timedelta(minutes=15)
        stmt = select(ComparisonTask).where(
            ComparisonTask.user_id == self._user_id,
            ComparisonTask.commodity == commodity,
            ComparisonTask.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
            ComparisonTask.created_at >= fresh,
        )
        return await self._session.scalar(stmt)

    async def create(self, commodity: Commodity) -> ComparisonTask:
        task = ComparisonTask(user_id=self._user_id, commodity=commodity, status=TaskStatus.PENDING)
        self._session.add(task)
        await self._session.flush()
        return task


def get_invoice_repo(session: SessionDep, user: CurrentUserDep) -> InvoiceRepository:
    return InvoiceRepository(session, user.id)


def get_task_repo(session: SessionDep, user: CurrentUserDep) -> ComparisonTaskRepository:
    return ComparisonTaskRepository(session, user.id)


InvoiceRepoDep = Annotated[InvoiceRepository, Depends(get_invoice_repo)]
TaskRepoDep = Annotated[ComparisonTaskRepository, Depends(get_task_repo)]
