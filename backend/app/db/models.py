"""Schema multi-tenant (SQLAlchemy 2.0, tipizzato).

Regola: ogni tabella con dati dell'utente eredita `TenantMixin` (user_id NOT NULL + indice + FK
con ON DELETE CASCADE, utile anche per il diritto all'oblio GDPR).

Eccezione consapevole: `market_offers` è un catalogo pubblico condiviso (offerte del mercato
libero, uguali per tutti). Non ha user_id; i risultati personali stanno in `comparison_tasks`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

from app.parsing.contracts import Commodity, ConsumptionUnit, Provider, SourceKind


def _pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantMixin:
    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
        )


class User(TimestampMixin, Base):
    """Profilo applicativo. `id` coincide con `auth.users.id` di Supabase (claim `sub` del JWT)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    #: Codice ISTAT a 2 cifre della regione di fornitura, scelto dall'utente (filtra le offerte locali).
    region_code: Mapped[str | None] = mapped_column(String(2))

    invoices: Mapped[list[Invoice]] = relationship(back_populates="user", passive_deletes=True)


class Invoice(TenantMixin, TimestampMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        # Stesso PDF caricato due volte dallo stesso utente → 409, non un duplicato.
        UniqueConstraint("user_id", "file_sha256", name="uq_invoice_user_file"),
        CheckConstraint("period_end >= period_start", name="ck_invoice_period"),
        Index("ix_invoice_user_period", "user_id", "period_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[Provider] = mapped_column(_pg_enum(Provider, "provider"))
    commodity: Mapped[Commodity] = mapped_column(_pg_enum(Commodity, "commodity"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    consumption_value: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    consumption_unit: Mapped[ConsumptionUnit] = mapped_column(_pg_enum(ConsumptionUnit, "consumption_unit"))
    total_amount_eur: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    supply_point_code: Mapped[str | None] = mapped_column(String(32))
    supplier_name: Mapped[str | None] = mapped_column(String(120))
    supply_cost_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    source_kind: Mapped[SourceKind] = mapped_column(_pg_enum(SourceKind, "source_kind"))
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    warnings: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    file_sha256: Mapped[str] = mapped_column(String(64))
    # Il PDF originale NON viene conservato (minimizzazione GDPR). Se servirà, qui andrà il path
    # dell'oggetto su Supabase Storage (bucket privato, prefisso = user_id).
    storage_path: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[User] = relationship(back_populates="invoices")


class MarketOffer(Base):
    """Catalogo condiviso delle offerte del giorno (fonte: open data del Portale Offerte).

    Tabella pubblica, senza user_id: le offerte sono uguali per tutti. `data` contiene l'offerta
    normalizzata (componenti, fasce, indici, sconti, zone), da cui il costo si calcola al momento
    del confronto sul consumo del singolo utente. Si conserva solo l'ultima pubblicazione.
    """

    __tablename__ = "market_offers"
    __table_args__ = (
        UniqueConstraint("published_on", "source", "offer_code", "piva", name="uq_offer_publication"),
        Index("ix_offer_lookup", "published_on", "commodity", "client_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    published_on: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20))  # mercato_libero | placet
    offer_code: Mapped[str] = mapped_column(String(64))
    piva: Mapped[str | None] = mapped_column(String(16))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    offer_name: Mapped[str | None] = mapped_column(String(300))
    commodity: Mapped[str] = mapped_column(String(16))  # electricity | gas
    client_type: Mapped[str | None] = mapped_column(String(2))
    price_kind: Mapped[str | None] = mapped_column(String(16))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class MarketSnapshot(Base):
    """Una riga per pubblicazione: parametri regolati e valori degli indici del giorno."""

    __tablename__ = "market_snapshots"

    published_on: Mapped[date] = mapped_column(Date, primary_key=True)
    parameters_electricity: Mapped[dict[str, Any]] = mapped_column(JSONB)
    parameters_gas: Mapped[dict[str, Any]] = mapped_column(JSONB)
    index_values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    offers_electricity: Mapped[int] = mapped_column(default=0)
    offers_gas: Mapped[int] = mapped_column(default=0)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ComparisonTask(TenantMixin, TimestampMixin, Base):
    """Stato persistente di un job in background: sopravvive alla risposta HTTP, non al processo
    (vedi recover_stale_tasks in services/comparison.py)."""

    __tablename__ = "comparison_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    commodity: Mapped[Commodity] = mapped_column(_pg_enum(Commodity, "commodity"))
    status: Mapped[TaskStatus] = mapped_column(_pg_enum(TaskStatus, "task_status"), default=TaskStatus.PENDING)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(1000))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
