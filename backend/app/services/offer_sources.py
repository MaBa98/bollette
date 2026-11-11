"""Sorgenti delle offerte di mercato. Il codice di scraping reale è volutamente un mock.

Raccomandazione (da verificare prima di scrivere Playwright): il Portale Offerte è per legge una
piattaforma di pubblicazione *open data*, e il tracciato con cui i venditori caricano le offerte
è XML. Scaricare i dataset aperti con `httpx` + `lxml` è più stabile, legale e leggero di uno
scraper headless: Chromium da solo occupa ~300 MB di RAM e su un'istanza free da 512 MB va in OOM.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from app.db.models import OfferPriceType
from app.parsing.contracts import Commodity


@dataclass(frozen=True, slots=True)
class OfferDTO:
    offer_code: str
    supplier_name: str
    offer_name: str
    commodity: Commodity
    price_type: OfferPriceType
    energy_price_eur: Decimal
    fixed_fee_eur_month: Decimal
    valid_from: date
    valid_to: date | None
    source_url: str | None


class MarketOfferSource(Protocol):
    """Porta d'ingresso per qualsiasi fonte: mock, open data XML, scraper Playwright."""

    async def fetch(self, commodity: Commodity) -> Sequence[OfferDTO]: ...


class MockPortaleOfferteSource:
    """Simula latenza e risultati di una fonte esterna lenta."""

    def __init__(self, latency_seconds: float = 4.0, seed: int | None = None) -> None:
        self._latency = latency_seconds
        self._rng = random.Random(seed)

    async def fetch(self, commodity: Commodity) -> Sequence[OfferDTO]:
        await asyncio.sleep(self._latency)  # I/O simulato: NON blocca l'event loop
        base = Decimal("0.11") if commodity is Commodity.ELECTRICITY else Decimal("0.42")
        suppliers = ("Alfa Energia", "Beta Luce&Gas", "Gamma Power", "Delta Green", "Epsilon Utility")
        today = date.today()
        return [
            OfferDTO(
                offer_code=f"MOCK-{commodity.value[:3].upper()}-{i:03d}",
                supplier_name=name,
                offer_name=f"{name} {'Fix' if i % 2 else 'Flex'} 12",
                commodity=commodity,
                price_type=OfferPriceType.FIXED if i % 2 else OfferPriceType.VARIABLE,
                energy_price_eur=(base * Decimal(str(self._rng.uniform(0.85, 1.25)))).quantize(Decimal("0.000001")),
                fixed_fee_eur_month=Decimal(str(round(self._rng.uniform(6, 14), 2))),
                valid_from=today,
                valid_to=today + timedelta(days=30),
                source_url=None,
            )
            for i, name in enumerate(suppliers)
        ]


# class AreraOpenDataSource:
#     """FUTURE: httpx.AsyncClient → download dataset → lxml.iterparse in streaming → OfferDTO."""
