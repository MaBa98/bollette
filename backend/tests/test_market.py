"""Test del lettore open data e del calcolo delle offerte.
XML di esempio adattato dai test di PortaleOfferteEnergiaMCP (MIT), che riproducono il tracciato reale."""

import io

import pytest

from app.market.portale import (
    discover_links,
    index_values,
    iter_ml_offers,
    iter_placet_offers,
    read_historical_prices,
    read_parameters,
)
from app.market.pricing import PricingInputs, eligible_for, estimate_annual_cost

NS = "http://www.acquirenteunico.it/schemas/SII_AU/OffertaRetail/01"


def _xml(offers: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?><ListaOfferteMercatoLibero xmlns="{NS}">{offers}</ListaOfferteMercatoLibero>'.encode()


VARIABLE_OFFER = """
<offerta>
  <IdentificativiOfferta><PIVA_UTENTE>01234567890</PIVA_UTENTE><COD_OFFERTA>VAR001</COD_OFFERTA></IdentificativiOfferta>
  <DettaglioOfferta>
    <TIPO_MERCATO>01</TIPO_MERCATO><OFFERTA_SINGOLA>SI</OFFERTA_SINGOLA><TIPO_CLIENTE>01</TIPO_CLIENTE>
    <DOMESTICO_RESIDENTE>03</DOMESTICO_RESIDENTE><TIPO_OFFERTA>02</TIPO_OFFERTA><NOME_OFFERTA>Prova Variabile</NOME_OFFERTA>
    <Contatti><URL_SITO_VENDITORE>www.prova.it</URL_SITO_VENDITORE><URL_OFFERTA>https://www.prova.it/offerta</URL_OFFERTA></Contatti>
  </DettaglioOfferta>
  <RiferimentiPrezzoEnergia><IDX_PREZZO_ENERGIA>12</IDX_PREZZO_ENERGIA></RiferimentiPrezzoEnergia>
  <ValiditaOfferta><DATA_INIZIO>13/10/2026_17:27:02</DATA_INIZIO><DATA_FINE>10/01/2027_23:59:59</DATA_FINE></ValiditaOfferta>
  <TipoPrezzo><TIPOLOGIA_FASCE>01</TIPOLOGIA_FASCE></TipoPrezzo>
  <ComponenteImpresa>
    <NOME>SPREAD</NOME><TIPOLOGIA>01</TIPOLOGIA><MACROAREA>04</MACROAREA>
    <IntervalloPrezzi><FASCIA_COMPONENTE>01</FASCIA_COMPONENTE><PREZZO>0.0200</PREZZO><UNITA_MISURA>03</UNITA_MISURA></IntervalloPrezzi>
  </ComponenteImpresa>
  <ComponenteImpresa>
    <NOME>COMMERCIALIZZAZIONE</NOME><TIPOLOGIA>01</TIPOLOGIA><MACROAREA>01</MACROAREA>
    <IntervalloPrezzi><PREZZO>72</PREZZO><UNITA_MISURA>01</UNITA_MISURA></IntervalloPrezzi>
  </ComponenteImpresa>
  <Sconto>
    <NOME>Benvenuto</NOME><VALIDITA>01</VALIDITA>
    <Condizione><CONDIZIONE_APPLICAZIONE>00</CONDIZIONE_APPLICAZIONE></Condizione>
    <PrezziSconto><TIPOLOGIA>01</TIPOLOGIA><UNITA_MISURA>05</UNITA_MISURA><PREZZO>30</PREZZO></PrezziSconto>
  </Sconto>
  <Sconto>
    <NOME>Bolletta web</NOME><VALIDITA>01</VALIDITA>
    <Condizione><CONDIZIONE_APPLICAZIONE>01</CONDIZIONE_APPLICAZIONE></Condizione>
    <PrezziSconto><TIPOLOGIA>01</TIPOLOGIA><UNITA_MISURA>05</UNITA_MISURA><PREZZO>24</PREZZO></PrezziSconto>
  </Sconto>
</offerta>
"""

FIXED_REGIONAL_OFFER = """
<offerta>
  <IdentificativiOfferta><PIVA_UTENTE>09876543210</PIVA_UTENTE><COD_OFFERTA>FIX001</COD_OFFERTA></IdentificativiOfferta>
  <DettaglioOfferta>
    <TIPO_MERCATO>01</TIPO_MERCATO><TIPO_CLIENTE>01</TIPO_CLIENTE><TIPO_OFFERTA>01</TIPO_OFFERTA>
    <NOME_OFFERTA>Fissa Lombardia</NOME_OFFERTA>
  </DettaglioOfferta>
  <TipoPrezzo><TIPOLOGIA_FASCE>03</TIPOLOGIA_FASCE></TipoPrezzo>
  <ComponenteImpresa>
    <NOME>PREZZO</NOME><TIPOLOGIA>01</TIPOLOGIA><MACROAREA>04</MACROAREA>
    <IntervalloPrezzi><FASCIA_COMPONENTE>01</FASCIA_COMPONENTE><PREZZO>0.12</PREZZO><UNITA_MISURA>03</UNITA_MISURA></IntervalloPrezzi>
    <IntervalloPrezzi><FASCIA_COMPONENTE>02</FASCIA_COMPONENTE><PREZZO>0.11</PREZZO><UNITA_MISURA>03</UNITA_MISURA></IntervalloPrezzi>
    <IntervalloPrezzi><FASCIA_COMPONENTE>03</FASCIA_COMPONENTE><PREZZO>0.10</PREZZO><UNITA_MISURA>03</UNITA_MISURA></IntervalloPrezzi>
  </ComponenteImpresa>
  <ZoneOfferta><REGIONE>03</REGIONE></ZoneOfferta>
</offerta>
"""


def _offers() -> list[dict]:
    return list(iter_ml_offers(io.BytesIO(_xml(VARIABLE_OFFER + FIXED_REGIONAL_OFFER))))


def test_xml_streaming_parse() -> None:
    var, fix = _offers()
    assert var["code"] == "VAR001" and var["commodity"] == "electricity" and var["price_kind"] == "variabile"
    assert var["valid_to"] == "2027-01-10T23:59:59"
    assert var["url"] == "https://www.prova.it/offerta"
    assert var["indices"][0]["code"] == "12"
    assert fix["zones"]["regions"] == ["03"]
    assert [p["fascia"] for p in fix["components"][0]["prices"]] == ["01", "02", "03"]


def test_variable_offer_cost_with_losses_and_unconditional_discount_only() -> None:
    var, _ = _offers()
    inputs = PricingInputs(annual_consumption=2700, parameters={"lambda": 0.10}, index_values={"PUN": 0.12})
    est = estimate_annual_cost(var, inputs)
    # spread 0,02×2700=54 + quota fissa 72 + PUN 0,12×2700×1,10=356,40 − 30 benvenuto (bolletta web esclusa)
    assert est.annual_cost == pytest.approx(54 + 72 + 356.40 - 30)
    assert est.complete


def test_fixed_multiorario_uses_profile() -> None:
    _, fix = _offers()
    est = estimate_annual_cost(fix, PricingInputs(annual_consumption=1000, parameters={}, index_values={}))
    assert est.annual_cost == pytest.approx(0.12 * 330 + 0.11 * 310 + 0.10 * 360)


def test_regulated_dispatch_added_equally() -> None:
    _, fix = _offers()
    base = estimate_annual_cost(fix, PricingInputs(annual_consumption=1000, parameters={}, index_values={}))
    with_disp = estimate_annual_cost(
        fix, PricingInputs(annual_consumption=1000, parameters={}, index_values={}, regulated_dispatch_eur_kwh=0.015)
    )
    assert with_disp.annual_cost - base.annual_cost == pytest.approx(15.0)


def test_missing_index_marks_estimate_incomplete() -> None:
    var, _ = _offers()
    est = estimate_annual_cost(var, PricingInputs(annual_consumption=2700, parameters={}, index_values={}))
    assert not est.complete


def test_regional_eligibility() -> None:
    var, fix = _offers()
    assert eligible_for(fix, region="03", annual_consumption=2000, power_kw=3)[0]
    assert eligible_for(fix, region="01", annual_consumption=2000, power_kw=3) == (False, "fuori regione")
    assert eligible_for(var, region="01", annual_consumption=2000, power_kw=3)[0]  # nazionale


def test_placet_names_and_cost() -> None:
    csv_text = (
        "denominazione,codice_fiscale,p_iva,url_sito_venditore,telefono,nome_offerta,cod_offerta,url_offerta,"
        "modalita_attivazione,modalita_pagamento,data_inizio,data_fine,tipo_cliente,tipo_offerta,p_fix_f,p_fix_v,"
        "p_vol_f1,p_vol_f2,p_vol_f3,p_vol_bf1,p_vol_bf23,p_vol_mono,alpha,regione,provincia,comune\n"
        "PROVA SRL,0123,0123,https://prova.it,,PLACET Fissa,COD1,https://prova.it/placet,x,y,"
        "05/09/2026,31/12/2026,domestico,prezzo fisso,85.000000,0.000000,0.000000,0.000000,0.000000,"
        "0.000000,0.000000,0.125000,0.000000,,,\n"
    )
    [offer] = iter_placet_offers(io.BytesIO(csv_text.encode()), "electricity")
    assert offer["seller"] == "PROVA SRL" and offer["placet"]["energy"] == {"mono": 0.125}
    est = estimate_annual_cost(offer, PricingInputs(annual_consumption=2000, parameters={}, index_values={}))
    assert est.annual_cost == pytest.approx(85 + 0.125 * 2000)


def test_parameters_and_historical_prices() -> None:
    params = read_parameters(io.BytesIO("nome_parametro,valore,descrizione\nlambda,0.102,perdite\npcv_c,41.18,PCV\n".encode()))
    assert params == {"lambda": 0.102, "pcv_c": 41.18}
    months = read_historical_prices(
        io.BytesIO("AnnoMese;PUN (€/kWh);PSV (€/Smc)\r\n202608;0,18;0,69\r\n202609;0,20;0,84\r\n".encode("cp1252"))
    )
    values = index_values(months)
    assert values["PUN"]["last"] == 0.20 and values["PUN"]["last_month"] == "2026-09"
    assert values["PUN"]["avg_12m"] == pytest.approx(0.19)


def test_discover_links_keeps_latest_day() -> None:
    html = """
    <a href="/portaleOfferte/resources/cms/documents/abc.csv">storici</a>
    <a href="/portaleOfferte/resources/opendata/csv/offerteML/2026_11/PO_Offerte_E_MLIBERO_20261110.xml">x</a>
    <a href="/portaleOfferte/resources/opendata/csv/offerteML/2026_11/PO_Offerte_E_MLIBERO_20261111.xml">x</a>
    """
    links = discover_links(html)
    assert links.daily["ml_electricity"][1] == "20261111"
    assert links.historical_prices.endswith("/abc.csv")
    assert "ml_gas" in links.missing()
