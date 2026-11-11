from datetime import date
from decimal import Decimal

from app.db.models import MarketOffer, OfferPriceType
from app.parsing.contracts import Commodity
from app.services.comparison import _rank_offers


def _offer(code: str, price: str, fee: str) -> MarketOffer:
    return MarketOffer(
        offer_code=code, supplier_name=code, offer_name=code, commodity=Commodity.ELECTRICITY,
        price_type=OfferPriceType.FIXED, energy_price_eur=Decimal(price),
        fixed_fee_eur_month=Decimal(fee), valid_from=date(2026, 11, 1),
    )


def test_savings_are_current_cost_minus_offer_cost_sorted_cheapest_first() -> None:
    offers = [_offer("CARA", "0.20", "10"), _offer("ECONOMICA", "0.10", "5")]
    # 2.700 kWh/anno: ECONOMICA = 270 + 60 = 330 €, CARA = 540 + 120 = 660 €
    ranked = _rank_offers(offers, Decimal("2700"), Decimal("500.00"), top_n=5)
    assert [r["offer_code"] for r in ranked] == ["ECONOMICA", "CARA"]
    assert ranked[0]["annual_saving_eur"] == "170.00"
    assert ranked[0]["saving_pct"] == "34.0"
    assert ranked[1]["annual_saving_eur"] == "-160.00"  # offerta più cara: risparmio negativo


def test_no_current_cost_means_no_savings() -> None:
    ranked = _rank_offers([_offer("X", "0.10", "5")], Decimal("2700"), None, top_n=5)
    assert ranked[0]["annual_saving_eur"] is None and ranked[0]["saving_pct"] is None
