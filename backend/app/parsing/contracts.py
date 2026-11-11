"""Contratti condivisi del sottosistema di parsing.

Due livelli separati, con responsabilità distinte:

    bytes ──[DocumentParser: Strategy]──▶ ExtractedText ──[BaseProviderTemplate: Template Method]──▶ ParsedInvoice

* Le *strategie* sanno solo trasformare un documento in testo (PDF nativo oggi, OCR domani).
* I *template* sanno solo trasformare testo in una bolletta strutturata, per fornitore.

Nessun template conosce il formato sorgente: ecco perché aggiungere l'OCR non tocca i template.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceKind(StrEnum):
    NATIVE_PDF = "native_pdf"
    # FUTURE-PROOF: valori già previsti nello schema DB, così l'OCR non richiede migrazioni dell'enum.
    SCANNED_PDF = "scanned_pdf"
    IMAGE = "image"


class Commodity(StrEnum):
    ELECTRICITY = "electricity"
    GAS = "gas"


class ConsumptionUnit(StrEnum):
    KWH = "kWh"
    SMC = "Smc"


class Provider(StrEnum):
    ENEL = "enel"
    PLENITUDE = "plenitude"
    UNKNOWN = "unknown"


EXPECTED_UNIT: dict[Commodity, ConsumptionUnit] = {
    Commodity.ELECTRICITY: ConsumptionUnit.KWH,
    Commodity.GAS: ConsumptionUnit.SMC,
}


@dataclass(frozen=True, slots=True)
class DocumentInput:
    """Documento caricato, interamente in RAM. Non esiste mai un path su disco."""

    content: bytes
    filename: str
    declared_mime: str

    @property
    def sniffed_mime(self) -> str:
        """MIME ricavato dai magic bytes: il Content-Type del client non è affidabile."""
        head = self.content[:8]
        if head.startswith(b"%PDF-"):
            return "application/pdf"
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        return "application/octet-stream"


@dataclass(frozen=True, slots=True)
class ExtractedText:
    """Output di una strategia: testo per pagina + provenienza."""

    pages: tuple[str, ...]
    source_kind: SourceKind
    # FUTURE-PROOF: l'OCRImageParser popolerà questo campo (0..1) e la pipeline lo userà
    # per abbassare la confidence finale della bolletta.
    ocr_confidence: float | None = None

    @property
    def full_text(self) -> str:
        return "\n".join(self.pages)


class ParsedInvoice(BaseModel):
    """Bolletta strutturata, indipendente da fornitore e formato sorgente."""

    model_config = ConfigDict(frozen=True)

    provider: Provider
    commodity: Commodity
    period_start: date
    period_end: date
    consumption_value: Decimal = Field(ge=0)
    consumption_unit: ConsumptionUnit
    total_amount_eur: Decimal = Field(ge=0)
    supply_point_code: str | None = Field(default=None, description="POD (luce) o PDR (gas)")
    #: Nome commerciale leggibile (es. "A2A Energia"), anche quando non esiste un template dedicato.
    supplier_name: str | None = None
    #: "Spesa per la materia energia / gas naturale", IVA esclusa: la sola quota del venditore.
    #: È la base omogenea con cui confrontare le offerte. None se la voce non è stata trovata.
    supply_cost_eur: Decimal | None = Field(default=None, ge=0)
    source_kind: SourceKind
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_consistency(self) -> ParsedInvoice:
        if self.period_end < self.period_start:
            raise ValueError("period_end precede period_start")
        if EXPECTED_UNIT[self.commodity] is not self.consumption_unit:
            raise ValueError(f"unità {self.consumption_unit} incoerente con {self.commodity}")
        return self

    @property
    def billed_days(self) -> int:
        return (self.period_end - self.period_start).days + 1


class ParsingError(Exception):
    """Radice degli errori di parsing, mappati a 4xx dall'API."""


class UnsupportedDocumentError(ParsingError):
    """Nessuna strategia registrata sa leggere questo documento (→ HTTP 415)."""


class ExtractionError(ParsingError):
    """Documento leggibile ma campi obbligatori non trovati (→ HTTP 422)."""
