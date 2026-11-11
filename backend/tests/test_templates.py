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
Spesa per la materia energia € 98,40
Spesa per il trasporto e la gestione del contatore € 22,10
Totale da pagare € 1.104,37
"""

PLENITUDE_GAS_TEXT = """Eni Plenitude - Bolletta gas (conguaglio)
Periodo: 01/08/2026 - 30/09/2026
PDR: 01234567890123
Consumo effettivo 58,4 Smc
Spesa per la materia gas naturale 41,25 euro
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


def test_generic_template_recognises_known_supplier() -> None:
    text = ENEL_TEXT.replace("Enel Energia S.p.A. - www.enel.it", "A2A Energia S.p.A. - Bolletta luce")
    invoice = build_default_registry().detect(text).parse(_extract(text))
    assert invoice.provider is Provider.UNKNOWN  # nessun template dedicato...
    assert invoice.supplier_name == "A2A Energia"  # ...ma il nome viene riconosciuto


def test_supplier_name_only_from_first_page() -> None:
    first = ENEL_TEXT.replace("Enel Energia S.p.A. - www.enel.it", "Fornitore Locale Srl")
    extracted = ExtractedText(pages=(first, "Distributore: Edison rete"), source_kind=SourceKind.NATIVE_PDF)
    invoice = build_default_registry().detect(extracted.full_text).parse(extracted)
    assert invoice.supplier_name is None


def test_dedicated_template_sets_display_name() -> None:
    invoice = build_default_registry().detect(ENEL_TEXT).parse(_extract(ENEL_TEXT))
    assert invoice.supplier_name == "Enel Energia"


def test_distributor_group_name_is_not_mistaken_for_supplier() -> None:
    first = ENEL_TEXT.replace("Enel Energia S.p.A. - www.enel.it", "Fornitore Locale Srl\nDistributore: Unareti S.p.A. (Gruppo A2A)")
    invoice = build_default_registry().detect(first).parse(_extract(first))
    assert invoice.supplier_name is None



def test_supply_cost_extracted_for_electricity_and_gas() -> None:
    reg = build_default_registry()
    assert reg.detect(ENEL_TEXT).parse(_extract(ENEL_TEXT)).supply_cost_eur == Decimal("98.40")
    gas = reg.detect(PLENITUDE_GAS_TEXT).parse(_extract(PLENITUDE_GAS_TEXT))
    assert gas.supply_cost_eur == Decimal("41.25")


def test_missing_supply_cost_keeps_invoice_but_warns() -> None:
    text = ENEL_TEXT.replace("Spesa per la materia energia € 98,40\n", "")
    invoice = build_default_registry().detect(text).parse(_extract(text))
    assert invoice.supply_cost_eur is None
    assert any("materia energia" in w for w in invoice.warnings)


def test_supply_cost_above_total_is_discarded() -> None:
    text = ENEL_TEXT.replace("€ 98,40", "€ 9.840,00")
    invoice = build_default_registry().detect(text).parse(_extract(text))
    assert invoice.supply_cost_eur is None



NEW_FORMAT_TEXT = ENEL_TEXT.replace(
    "Spesa per la materia energia € 98,40",
    "SCONTRINO DELL'ENERGIA\nSpesa per la vendita di energia elettrica\n€ 87,15\n"
    "Spesa per la rete e oneri generali di sistema € 30,02",
).replace("Totale da pagare € 1.104,37", "Totale complessivo da pagare € 1.104,37")


def test_new_2025_format_amount_on_next_line() -> None:
    invoice = build_default_registry().detect(NEW_FORMAT_TEXT).parse(_extract(NEW_FORMAT_TEXT))
    assert invoice.supply_cost_eur == Decimal("87.15")
    assert invoice.total_amount_eur == Decimal("1104.37")


def test_new_format_same_line_with_dots_leader() -> None:
    text = ENEL_TEXT.replace(
        "Spesa per la materia energia € 98,40", "Spesa per la vendita di energia elettrica ........ 87,15 €"
    )
    assert build_default_registry().detect(text).parse(_extract(text)).supply_cost_eur == Decimal("87.15")


def test_gas_new_format() -> None:
    text = PLENITUDE_GAS_TEXT.replace(
        "Spesa per la materia gas naturale 41,25 euro", "Spesa per la vendita di gas naturale € 39,80"
    )
    assert build_default_registry().detect(text).parse(_extract(text)).supply_cost_eur == Decimal("39.80")


def test_sub_lines_without_own_amount_are_not_guessed() -> None:
    # La voce ha solo sotto-righe: non si prende la prima (valore parziale), si rinuncia.
    text = ENEL_TEXT.replace(
        "Spesa per la materia energia € 98,40",
        "Spesa per la vendita di energia elettrica\nQuota consumi € 70,10\nQuota fissa € 17,05",
    )
    assert build_default_registry().detect(text).parse(_extract(text)).supply_cost_eur is None
