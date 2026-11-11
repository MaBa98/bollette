"""Regressione su layout reali (formato ARERA 'scontrino dell'energia', da luglio 2025).
I valori attesi coincidono con quelli stampati sulle bollette originali."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsing.contracts import ExtractedText, SourceKind
from app.parsing.templates.registry import build_default_registry

FIXTURES = Path(__file__).parent / "fixtures"


def _parse(name: str):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    extracted = ExtractedText(pages=(text,), source_kind=SourceKind.NATIVE_PDF)
    return build_default_registry().detect(text).parse(extracted)


@pytest.mark.parametrize(
    ("fixture", "supplier", "period", "consumption", "total", "supply_cost"),
    [
        # Periodo a mesi, quota venditore = 9,04 + 8,64 = 17,68 (= box offerta)
        ("scontrino_mese_sorgenia_like.txt", "Sorgenia", (date(2026, 9, 1), date(2026, 9, 30)), "69.4", "38.89", "17.68"),
        # Canone TV escluso dal totale, quota venditore = 4,99 + 6,00 = 10,99 (= box offerta)
        ("scontrino_date_canone_octopus_like.txt", "Octopus Energy", (date(2026, 10, 1), date(2026, 10, 31)), "18", "21.67", "10.99"),
    ],
)
def test_real_layout(fixture, supplier, period, consumption, total, supply_cost) -> None:
    inv = _parse(fixture)
    assert inv.supplier_name == supplier
    assert (inv.period_start, inv.period_end) == period
    assert inv.consumption_value == Decimal(consumption)
    assert inv.total_amount_eur == Decimal(total)
    assert inv.supply_cost_eur == Decimal(supply_cost)
    assert inv.confidence >= 0.9  # fornitore riconosciuto e tutti i campi trovati
    assert inv.warnings == ()


def test_bimonthly_period_across_year_end() -> None:
    from app.parsing.templates.providers import GenericItalianTemplate

    match = GenericItalianTemplate().extract_period("Periodo di fatturazione Dicembre - Gennaio 2026")
    assert match == (date(2025, 12, 1), date(2026, 1, 31))
