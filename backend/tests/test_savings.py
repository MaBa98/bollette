"""Classifica e risparmio sul catalogo reale (funzione pura build_ranking)."""

from datetime import date, datetime
from decimal import Decimal

from app.db.models import MarketOffer
from app.market.pricing import PricingInputs
from app.services.comparison import build_ranking

NOW = datetime(2026, 11, 11, 12, 0)


def _row(code: str, *, mono: float, fixed_year: float, regions: list[str] | None = None,
         valid_to: str = "2027-01-31T23:59:59", indices: list | None = None) -> MarketOffer:
    data = {
        "source": "mercato_libero", "code": code, "commodity": "electricity", "client_type": "01",
        "price_kind": "fisso", "single": True, "tipologia_fasce": "01", "url": f"https://example.it/{code}",
        "valid_from": "2026-10-01T00:00:00", "valid_to": valid_to,
        "components": [
            {"name": "PREZZO", "macroarea": "04", "tipologia": "01",
             "prices": [{"fascia": "01", "price": mono, "unit": "03"}]},
            {"name": "FISSA", "macroarea": "01", "tipologia": "01", "prices": [{"price": fixed_year, "unit": "01"}]},
        ],
        "indices": indices or [], "dispatch": [], "regulated": [], "discounts": [],
        "zones": {"regions": regions or [], "provinces": [], "municipalities": []},
    }
    return MarketOffer(published_on=date(2026, 11, 11), source="mercato_libero", offer_code=code,
                       supplier_name=f"Venditore {code}", offer_name=code, commodity="electricity",
                       client_type="01", price_kind="fisso", data=data)


INPUTS = PricingInputs(annual_consumption=2000, parameters={}, index_values={}, regulated_dispatch_eur_kwh=0.015)


def test_ranking_savings_and_dispatch() -> None:
    offers = [_row("CARA", mono=0.20, fixed_year=120), _row("ECONOMICA", mono=0.10, fixed_year=60)]
    out = build_ranking(offers, INPUTS, region="03", current_annual_cost=Decimal("400.00"), top_n=5, now=NOW)
    best, worst = out["top_offers"]
    # ECONOMICA: 0,10×2000 + 60 + dispacciamento 0,015×2000 = 290 €
    assert best["offer_code"] == "ECONOMICA"
    assert best["estimated_annual_supplier_cost_eur"] == "290.00"
    assert best["annual_saving_eur"] == "110.00" and best["saving_pct"] == "27.5"
    assert worst["annual_saving_eur"] == "-150.00"
    assert best["url"] == "https://example.it/ECONOMICA"


def test_filters_region_expiry_and_incomplete() -> None:
    offers = [
        _row("NAZIONALE", mono=0.10, fixed_year=60),
        _row("ALTRA_REGIONE", mono=0.05, fixed_year=0, regions=["12"]),
        _row("SCADUTA", mono=0.05, fixed_year=0, valid_to="2026-11-01T00:00:00"),
        _row("INDICE_IGNOTO", mono=0.01, fixed_year=0, indices=[{"code": "02"}]),  # TTF non pubblicato
    ]
    out = build_ranking(offers, INPUTS, region="03", current_annual_cost=None, top_n=5, now=NOW)
    assert [o["offer_code"] for o in out["top_offers"]] == ["NAZIONALE"]
    assert out["excluded"] == {"fuori regione": 1, "scaduta o non ancora attiva": 1, "stima incompleta": 1}
    assert out["top_offers"][0]["annual_saving_eur"] is None


def test_without_region_regional_offers_are_excluded() -> None:
    out = build_ranking([_row("LOCALE", mono=0.05, fixed_year=0, regions=["03"])], INPUTS,
                        region=None, current_annual_cost=None, top_n=5, now=NOW)
    assert out["top_offers"] == [] and out["excluded"] == {"fuori regione": 1}
