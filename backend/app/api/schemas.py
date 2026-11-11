from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.db.models import TaskStatus
from app.parsing.contracts import Commodity, ConsumptionUnit, Provider, SourceKind


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: Provider
    commodity: Commodity
    period_start: date
    period_end: date
    consumption_value: Decimal
    consumption_unit: ConsumptionUnit
    total_amount_eur: Decimal
    supply_point_code: str | None
    supplier_name: str | None
    source_kind: SourceKind
    confidence: Decimal
    warnings: list[str]
    created_at: datetime


class ComparisonRequest(BaseModel):
    commodity: Commodity


class ComparisonTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    commodity: Commodity
    status: TaskStatus
    created_at: datetime
    finished_at: datetime | None
    error: str | None
    result: dict[str, Any] | None
