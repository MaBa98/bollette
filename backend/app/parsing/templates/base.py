"""Template Method Pattern: algoritmo di estrazione fisso, passi personalizzabili per fornitore."""

from __future__ import annotations

import calendar
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


#: Venditori più diffusi sul mercato domestico italiano: (pattern, nome visualizzato).
#: L'ordine conta: i marchi con nomi composti vanno prima di quelli più generici.
KNOWN_SUPPLIERS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.I), name)
    for pattern, name in (
        (r"servizio\s+elettrico\s+nazionale", "Servizio Elettrico Nazionale"),
        (r"enel\s+energia", "Enel Energia"),
        (r"\bplenitude\b", "Plenitude"),
        (r"\ba2a\s+energia\b", "A2A Energia"),  # NON "a2a" da solo: Unareti (gruppo A2A) è il distributore di Milano
        (r"acea\s+energia", "Acea Energia"),
        (r"edison\s+energia", "Edison Energia"),
        (r"hera\s+comm", "Hera Comm"),
        (r"iren\s+(?:mercato|luce)", "Iren"),
        (r"\bsorgenia\b", "Sorgenia"),
        (r"e\.on\s+energia|\be\.on\b", "E.ON Energia"),
        (r"\bengie\b", "Engie"),
        (r"\billumia\b", "Illumia"),
        (r"octopus\s+energy", "Octopus Energy"),
        (r"\bwekiwi\b", "Wekiwi"),
        (r"\bpulsee\b", "Pulsee"),
        (r"\biberdrola\b", "Iberdrola"),
        (r"dolomiti\s+energia", "Dolomiti Energia"),
        (r"\balperia\b", "Alperia"),
        (r"\bestra\b", "Estra"),
        (r"agsm\s*aim", "AGSM AIM"),
        (r"\baxpo\b", "Axpo"),
        (r"optima\s+italia", "Optima Italia"),
        (r"duferco\s+energia", "Duferco Energia"),
        (r"\bnen\b(?=.*energia)", "NeN"),
    )
)


_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
_MONTHS_RE = "|".join(_MONTHS)
_LINE_AMOUNT = re.compile(rf"(-?{IT_NUMBER})\s*€(?!\s*/)")


class BaseProviderTemplate(ABC):
    """Le sottoclassi sovrascrivono SOLO pattern e hook; `parse()` non va mai ridefinito.

    ATTENZIONE: le regex delle sottoclassi sono punti di partenza plausibili, non calibrati.
    I layout reali cambiano ogni 1-2 anni: ogni template va accompagnato da fixture di bollette
    reali anonimizzate in tests/fixtures/<provider>/ e da test di regressione.
    """

    provider: ClassVar[Provider]
    #: Nome da mostrare per i template dedicati; None = riconoscimento dal catalogo KNOWN_SUPPLIERS.
    supplier_display_name: ClassVar[str | None] = None
    #: Pattern che identificano il fornitore nel testo (basta uno).
    detection_patterns: ClassVar[tuple[re.Pattern[str], ...]] = ()

    period_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"(?:periodo|dal)\s*:?\s*({IT_DATE})\s*(?:al|-|a)\s*({IT_DATE})", re.I),
    )
    total_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        # "Totale bolletta" per primo: esclude il canone TV, che altrimenti gonfia il costo effettivo.
        re.compile(rf"totale\s+bolletta\s*:?\s*{CURRENCY}\s*({IT_NUMBER})", re.I),
        re.compile(rf"totale\s+(?:complessivo\s+)?(?:da\s+pagare|fattura)\s*:?\s*{CURRENCY}\s*({IT_NUMBER})", re.I),
    )
    # (?!annu): "Consumo annuo 216 kWh" è lo storico di 12 mesi, non il consumo della bolletta.
    electricity_consumption_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"consumo\s+(?!annu)(?:fatturato|totale|effettivo|stimato)?[^\n\d]{{0,40}}({IT_NUMBER})\s*kwh", re.I),
    )
    gas_consumption_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"consumo\s+(?!annu)(?:fatturato|totale|effettivo|stimato)?[^\n\d]{{0,40}}({IT_NUMBER})\s*smc", re.I),
    )
    #: Periodo espresso a mesi: "Periodo di fatturazione Settembre 2026", "Luglio - Agosto 2026",
    #: "Novembre 2025 - Gennaio 2026".
    period_month_pattern: ClassVar[re.Pattern[str]] = re.compile(
        rf"periodo\s+di\s+(?:fatturazione|riferimento)\s*:?\s*({_MONTHS_RE})(?:\s+(\d{{4}}))?"
        rf"(?:\s*[-–/]\s*({_MONTHS_RE}))?\s+(\d{{4}})",
        re.I,
    )
    #: Formato ARERA da luglio 2025: la quota venditore è scomposta in righe "di cui spesa per
    #: vendita ..." (quota consumi + quota fissa) da sommare. L'importo è l'ultimo "X €" della riga,
    #: escludendo i prezzi unitari ("0,130259 €/kWh").
    electricity_supply_cost_component: ClassVar[re.Pattern[str]] = re.compile(
        r"di\s+cui\s+spesa\s+per\s+(?:la\s+)?vendita\s+(?:di\s+|dell['’]\s*)?energia(?:\s+elettrica)?(?P<rest>[^\n]*)",
        re.I,
    )
    gas_supply_cost_component: ClassVar[re.Pattern[str]] = re.compile(
        r"di\s+cui\s+spesa\s+per\s+(?:la\s+)?vendita\s+(?:di\s+|del\s+)?gas(?:\s+naturale)?(?P<rest>[^\n]*)",
        re.I,
    )
    # Diciture standard della bolletta sintetica ARERA, uguali per tutti i venditori.
    # Etichette della quota venditore. Due formati ARERA convivono negli storici degli utenti:
    #   * dal luglio 2025 ("scontrino dell'energia"): "Spesa per la vendita di energia elettrica / gas naturale"
    #   * bolletta 2.0 precedente: "Spesa per la materia energia / gas naturale"
    electricity_supply_cost_labels: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(r"spesa\s+per\s+la\s+vendita\s+(?:di\s+|dell['’]\s*)energia(?:\s+elettrica)?", re.I),
        re.compile(r"spesa\s+per\s+(?:la\s+)?materia\s+energia", re.I),
    )
    gas_supply_cost_labels: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(r"spesa\s+per\s+la\s+vendita\s+(?:di\s+|del\s+)gas(?:\s+naturale)?", re.I),
        re.compile(r"spesa\s+per\s+(?:la\s+)?materia\s+gas(?:\s+naturale)?", re.I),
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
        supply_cost = self.extract_supply_cost(text, commodity)
        if supply_cost is None:
            warnings.append("Quota materia energia non trovata: risparmio non calcolabile")
        elif supply_cost > total:
            # Incoerenza: probabile match sulla riga sbagliata. Meglio nessun dato che un dato falso.
            warnings.append("Quota materia energia incoerente con il totale: ignorata")
            supply_cost = None

        supplier_name = self.detect_supplier_name(extracted)
        invoice = ParsedInvoice(
            provider=self.provider,
            commodity=commodity,
            period_start=period_start,
            period_end=period_end,
            consumption_value=consumption,
            consumption_unit=EXPECTED_UNIT[commodity],
            total_amount_eur=total,
            supply_point_code=supply_point,
            supplier_name=supplier_name,
            supply_cost_eur=supply_cost,
            source_kind=extracted.source_kind,
            confidence=self.score_confidence(extracted, supply_point is not None, supplier_name is not None),
            warnings=tuple(warnings),
        )
        return self.post_process(invoice, text)

    # -------------------------------------------------------------- hook / passi

    def normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        return re.sub(r"[ \t]+", " ", text)

    def detect_supplier_name(self, extracted: ExtractedText) -> str | None:
        if self.supplier_display_name:
            return self.supplier_display_name
        # Solo la prima pagina: l'intestazione contiene il venditore, mentre le pagine successive
        # citano spesso distributori o altri operatori (e-distribuzione, Italgas, ecc.).
        first_page = self.normalize(extracted.pages[0] if extracted.pages else "")
        for pattern, name in KNOWN_SUPPLIERS:
            if pattern.search(first_page):
                return name
        return None

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
        for pattern in self.period_patterns:
            if match := pattern.search(text):
                return parse_it_date(match.group(1)), parse_it_date(match.group(2))
        if match := self.period_month_pattern.search(text):
            start_month = _MONTHS[match.group(1).lower()]
            end_month = _MONTHS[(match.group(3) or match.group(1)).lower()]
            end_year = int(match.group(4))
            start_year = int(match.group(2)) if match.group(2) else end_year - (1 if start_month > end_month else 0)
            last_day = calendar.monthrange(end_year, end_month)[1]
            return date(start_year, start_month, 1), date(end_year, end_month, last_day)
        raise ExtractionError("Campo obbligatorio non trovato: periodo di fatturazione")

    def extract_total(self, text: str) -> Decimal:
        return parse_it_decimal(self._first_match(self.total_patterns, text, "totale").group(1))

    def extract_consumption(self, text: str, commodity: Commodity) -> Decimal:
        patterns = (
            self.electricity_consumption_patterns
            if commodity is Commodity.ELECTRICITY
            else self.gas_consumption_patterns
        )
        return parse_it_decimal(self._first_match(patterns, text, "consumo").group(1))

    def extract_supply_cost(self, text: str, commodity: Commodity) -> Decimal | None:
        """Facoltativo: se manca la bolletta resta valida, ma senza calcolo del risparmio."""
        component = (
            self.electricity_supply_cost_component
            if commodity is Commodity.ELECTRICITY
            else self.gas_supply_cost_component
        )
        parts: list[Decimal] = []
        for match in component.finditer(text):
            amounts = _LINE_AMOUNT.findall(match.group("rest"))
            if amounts:
                parts.append(parse_it_decimal(amounts[-1]))
        if parts:
            total_supply = sum(parts, Decimal("0"))
            return total_supply if total_supply > 0 else None

        labels = (
            self.electricity_supply_cost_labels
            if commodity is Commodity.ELECTRICITY
            else self.gas_supply_cost_labels
        )
        for label in labels:
            for match in label.finditer(text):
                if (amount := self._amount_after_label(text, match.end())) is not None:
                    return amount
        return None

    _SAME_LINE_AMOUNT: ClassVar[re.Pattern[str]] = re.compile(
        rf"^[^\n\d€]{{0,80}}?{CURRENCY}\s*(-?{IT_NUMBER})\s*(?:€|eur(?:o)?)?\s*$", re.I
    )
    _AMOUNT_ONLY_LINE: ClassVar[re.Pattern[str]] = re.compile(
        rf"^\s*{CURRENCY}\s*(-?{IT_NUMBER})\s*(?:€|eur(?:o)?)?\s*$", re.I
    )

    def _amount_after_label(self, text: str, pos: int) -> Decimal | None:
        """Importo della riga dell'etichetta, oppure di una riga successiva che contiene SOLO l'importo.

        Volutamente rigido: con il nuovo formato la voce può avere sotto-righe (quota consumi,
        quota fissa) e prendere il primo numero che capita darebbe un valore parziale. Un parziale
        non verrebbe scartato dal controllo "maggiore del totale", quindi è peggio di nessun valore.
        Il segno meno è accettato solo per riconoscere e scartare gli storni.
        """
        line_end = text.find("\n", pos)
        rest_of_line = text[pos : line_end if line_end != -1 else len(text)]
        match = self._SAME_LINE_AMOUNT.match(rest_of_line)
        if match is None and line_end != -1:
            next_end = text.find("\n", line_end + 1)
            next_line = text[line_end + 1 : next_end if next_end != -1 else len(text)]
            match = self._AMOUNT_ONLY_LINE.match(next_line)
        if match is None:
            return None
        amount = parse_it_decimal(match.group(1))
        return amount if amount > 0 else None

    def extract_supply_point(self, text: str, commodity: Commodity) -> str | None:
        pattern = self.pod_pattern if commodity is Commodity.ELECTRICITY else self.pdr_pattern
        match = pattern.search(text)
        return match.group(1).upper() if match else None

    def score_confidence(self, extracted: ExtractedText, has_supply_point: bool, supplier_known: bool = True) -> float:
        score = 0.95 if has_supply_point else 0.8
        # Penalità solo se non sappiamo nemmeno chi è il fornitore: con il formato ARERA 2025
        # le diciture sono standard, quindi il template generico è affidabile quanto uno dedicato.
        if self.provider is Provider.UNKNOWN and not supplier_known:
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
