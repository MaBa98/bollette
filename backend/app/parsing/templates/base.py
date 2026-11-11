"""Template Method Pattern: algoritmo di estrazione fisso, passi personalizzabili per fornitore."""

from __future__ import annotations

import re
import unicodedata
from abc import ABC
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import ClassVar, final

from app.parsing.contracts import (
    EXPECTED_UNIT,
    Commodity,
    ExtractedText,
    ExtractionError,
    ParsedInvoice,
    Provider,
)

# Numero in formato italiano: 1.234,56 | 1234,56 | 1234
IT_NUMBER = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?"
IT_DATE = r"\d{2}[/.-]\d{2}[/.-]\d{4}"
CURRENCY = r"(?:€|eur(?:o)?)?"


def parse_it_decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(".", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ExtractionError(f"Numero non valido: {raw!r}") from exc


def parse_it_date(raw: str) -> date:
    normalized = re.sub(r"[.-]", "/", raw)
    return datetime.strptime(normalized, "%d/%m/%Y").date()


class BaseProviderTemplate(ABC):
    """Le sottoclassi sovrascrivono SOLO pattern e hook; `parse()` non va mai ridefinito.

    ATTENZIONE: le regex delle sottoclassi sono punti di partenza plausibili, non calibrati.
    I layout reali cambiano ogni 1-2 anni: ogni template va accompagnato da fixture di bollette
    reali anonimizzate in tests/fixtures/<provider>/ e da test di regressione.
    """

    provider: ClassVar[Provider]
    #: Pattern che identificano il fornitore nel testo (basta uno).
    detection_patterns: ClassVar[tuple[re.Pattern[str], ...]] = ()

    period_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"(?:periodo|dal)\s*:?\s*({IT_DATE})\s*(?:al|-|a)\s*({IT_DATE})", re.I),
    )
    total_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"totale\s+(?:da\s+pagare|bolletta|fattura)\s*:?\s*{CURRENCY}\s*({IT_NUMBER})", re.I),
    )
    electricity_consumption_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"consumo\s+(?:fatturato|totale|effettivo|stimato)?[^\n\d]{{0,40}}({IT_NUMBER})\s*kwh", re.I),
    )
    gas_consumption_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"consumo\s+(?:fatturato|totale|effettivo|stimato)?[^\n\d]{{0,40}}({IT_NUMBER})\s*smc", re.I),
    )
    pod_pattern: ClassVar[re.Pattern[str]] = re.compile(r"\b(IT\d{3}E\d{8,9})\b", re.I)
    pdr_pattern: ClassVar[re.Pattern[str]] = re.compile(r"\bPDR\s*:?\s*(\d{14})\b", re.I)

    # ----------------------------------------------------------------- detection

    @classmethod
    def matches(cls, text: str) -> bool:
        return any(p.search(text) for p in cls.detection_patterns)

    # ----------------------------------------------------------- template method

    @final
    def parse(self, extracted: ExtractedText) -> ParsedInvoice:
        text = self.normalize(extracted.full_text)
        warnings: list[str] = []

        commodity = self.detect_commodity(text)
        period_start, period_end = self.extract_period(text)
        total = self.extract_total(text)
        consumption = self.extract_consumption(text, commodity)
        supply_point = self.extract_supply_point(text, commodity)
        if supply_point is None:
            warnings.append("POD/PDR non trovato")

        invoice = ParsedInvoice(
            provider=self.provider,
            commodity=commodity,
            period_start=period_start,
            period_end=period_end,
            consumption_value=consumption,
            consumption_unit=EXPECTED_UNIT[commodity],
            total_amount_eur=total,
            supply_point_code=supply_point,
            source_kind=extracted.source_kind,
            confidence=self.score_confidence(extracted, supply_point is not None),
            warnings=tuple(warnings),
        )
        return self.post_process(invoice, text)

    # -------------------------------------------------------------- hook / passi

    def normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        return re.sub(r"[ \t]+", " ", text)

    def detect_commodity(self, text: str) -> Commodity:
        has_pdr = bool(self.pdr_pattern.search(text)) or bool(re.search(r"\bsmc\b", text, re.I))
        has_pod = bool(self.pod_pattern.search(text)) or bool(re.search(r"\bkwh\b", text, re.I))
        if has_pod and has_pdr:
            # Bollette dual fuel: per l'MVP rifiutiamo invece di indovinare.
            raise ExtractionError("Bolletta dual fuel (luce+gas) non ancora supportata")
        if has_pdr:
            return Commodity.GAS
        if has_pod:
            return Commodity.ELECTRICITY
        raise ExtractionError("Impossibile determinare se la bolletta è luce o gas")

    def extract_period(self, text: str) -> tuple[date, date]:
        match = self._first_match(self.period_patterns, text, "periodo di fatturazione")
        return parse_it_date(match.group(1)), parse_it_date(match.group(2))

    def extract_total(self, text: str) -> Decimal:
        return parse_it_decimal(self._first_match(self.total_patterns, text, "totale").group(1))

    def extract_consumption(self, text: str, commodity: Commodity) -> Decimal:
        patterns = (
            self.electricity_consumption_patterns
            if commodity is Commodity.ELECTRICITY
            else self.gas_consumption_patterns
        )
        return parse_it_decimal(self._first_match(patterns, text, "consumo").group(1))

    def extract_supply_point(self, text: str, commodity: Commodity) -> str | None:
        pattern = self.pod_pattern if commodity is Commodity.ELECTRICITY else self.pdr_pattern
        match = pattern.search(text)
        return match.group(1).upper() if match else None

    def score_confidence(self, extracted: ExtractedText, has_supply_point: bool) -> float:
        score = 0.95 if has_supply_point else 0.8
        if self.provider is Provider.UNKNOWN:
            score -= 0.25
        # FUTURE-PROOF: per i documenti OCR la confidence del motore OCR riduce il punteggio
        # finale; le bollette sotto 0.7 andranno in revisione manuale lato UI.
        if extracted.ocr_confidence is not None:
            score *= extracted.ocr_confidence
        return round(max(min(score, 1.0), 0.0), 2)

    def post_process(self, invoice: ParsedInvoice, text: str) -> ParsedInvoice:
        """Hook finale per correzioni specifiche del fornitore. Default: nessuna."""
        return invoice

    # ---------------------------------------------------------------- utilities

    @staticmethod
    def _first_match(patterns: tuple[re.Pattern[str], ...], text: str, field: str) -> re.Match[str]:
        for pattern in patterns:
            if match := pattern.search(text):
                return match
        raise ExtractionError(f"Campo obbligatorio non trovato: {field}")
