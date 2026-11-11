"""Test dei template su testo sintetico. In produzione: fixture da bollette reali anonimizzate."""

from datetime import date
from decimal import Decimal

import pytest

from app.parsing.contracts import Commodity, ExtractedText, ExtractionError, Provider, SourceKind
from app.parsing.templates.registry import build_default_registry

ENEL_TEXT = """Enel Energia S.p.A. - www.enel.it
Bolletta luce - Periodo di fatturazione dal 01/09/2026 al 31/10/2026
POD IT001E12345678
Consumo fatturato nel periodo 412 kWh
Totale da pagare € 1.104,37
"""

PLENITUDE_GAS_TEXT = """Eni Plenitude - Bolletta gas (conguaglio)
Periodo: 01/08/2026 - 30/09/2026
PDR: 01234567890123
Consumo effettivo 58,4 Smc
Importo da pagare: € 96,10
"""


def _extract(text: str) -> ExtractedText:
    return ExtractedText(pages=(text,), source_kind=SourceKind.NATIVE_PDF)


def test_enel_electricity() -> None:
    template = build_default_registry().detect(ENEL_TEXT)
    invoice = template.parse(_extract(ENEL_TEXT))
    assert invoice.provider is Provider.ENEL
    assert invoice.commodity is Commodity.ELECTRICITY
    assert (invoice.period_start, invoice.period_end) == (date(2026, 9, 1), date(2026, 10, 31))
    assert invoice.consumption_value == Decimal("412")
    assert invoice.total_amount_eur == Decimal("1104.37")
    assert invoice.supply_point_code == "IT001E12345678"


def test_plenitude_gas_flags_conguaglio() -> None:
    invoice = build_default_registry().detect(PLENITUDE_GAS_TEXT).parse(_extract(PLENITUDE_GAS_TEXT))
    assert invoice.provider is Provider.PLENITUDE
    assert invoice.commodity is Commodity.GAS
    assert invoice.consumption_value == Decimal("58.4")
    assert invoice.total_amount_eur == Decimal("96.10")
    assert any("conguaglio" in w.lower() for w in invoice.warnings)


def test_unknown_provider_gets_lower_confidence() -> None:
    text = ENEL_TEXT.replace("Enel Energia S.p.A. - www.enel.it", "Fornitore Sconosciuto Srl")
    invoice = build_default_registry().detect(text).parse(_extract(text))
    assert invoice.provider is Provider.UNKNOWN
    assert invoice.confidence < 0.8


def test_missing_total_raises() -> None:
    text = ENEL_TEXT.replace("Totale da pagare € 1.104,37", "")
    with pytest.raises(ExtractionError):
        build_default_registry().detect(text).parse(_extract(text))
