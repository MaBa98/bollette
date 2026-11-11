"""Template concreti per fornitore. Ogni classe dichiara solo le differenze dal template base."""

from __future__ import annotations

import re
from typing import ClassVar

from app.parsing.contracts import ParsedInvoice, Provider
from app.parsing.templates.base import CURRENCY, IT_DATE, IT_NUMBER, BaseProviderTemplate


class EnelTemplate(BaseProviderTemplate):
    provider: ClassVar[Provider] = Provider.ENEL
    supplier_display_name: ClassVar[str | None] = "Enel Energia"
    detection_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(r"enel\s+energia", re.I),
        re.compile(r"\benel\.it\b", re.I),
    )
    # Enel tende a scrivere "Bolletta del periodo dal ... al ..." e "Totale da pagare € ...".
    period_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"periodo\s+(?:di\s+fatturazione\s+)?dal\s+({IT_DATE})\s+al\s+({IT_DATE})", re.I),
        *BaseProviderTemplate.period_patterns,
    )
    total_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"totale\s+da\s+pagare\s*{CURRENCY}\s*({IT_NUMBER})", re.I),
        *BaseProviderTemplate.total_patterns,
    )


class PlenitudeTemplate(BaseProviderTemplate):
    provider: ClassVar[Provider] = Provider.PLENITUDE
    supplier_display_name: ClassVar[str | None] = "Plenitude"
    detection_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(r"\bplenitude\b", re.I),
        re.compile(r"eni\s+plenitude", re.I),
    )
    total_patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(rf"importo\s+(?:totale\s+)?da\s+pagare\s*:?\s*{CURRENCY}\s*({IT_NUMBER})", re.I),
        *BaseProviderTemplate.total_patterns,
    )

    def post_process(self, invoice: ParsedInvoice, text: str) -> ParsedInvoice:
        # Esempio di hook: segnala le bollette di conguaglio, che falsano la media dei consumi.
        if re.search(r"\bconguaglio\b", text, re.I):
            return invoice.model_copy(
                update={"warnings": (*invoice.warnings, "Bolletta di conguaglio: consumi non mensili")}
            )
        return invoice


class GenericItalianTemplate(BaseProviderTemplate):
    """Fallback: usa solo i pattern generici. Confidence penalizzata (vedi score_confidence)."""

    provider: ClassVar[Provider] = Provider.UNKNOWN

    @classmethod
    def matches(cls, text: str) -> bool:
        return True
