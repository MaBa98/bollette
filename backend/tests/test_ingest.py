"""Ingestione end-to-end senza rete né database: il portale è simulato con httpx.MockTransport."""

import httpx

from app.market.ingest import discover_publication_date, fetch_publication

NS = "http://www.acquirenteunico.it/schemas/SII_AU/OffertaRetail/01"
BASE = "https://www.ilportaleofferte.it/portaleOfferte/resources/opendata/csv"

PAGE = f"""
<a href="/portaleOfferte/resources/cms/documents/storici.csv">storici</a>
<a href="{BASE}/offerteML/2026_11/PO_Offerte_E_MLIBERO_20261111.xml">e</a>
<a href="{BASE}/offerteML/2026_11/PO_Offerte_G_MLIBERO_20261111.xml">g</a>
<a href="{BASE}/offerte/2026_11/PO_Offerte_E_PLACET_20261111.csv">ep</a>
<a href="{BASE}/offerte/2026_11/PO_Offerte_G_PLACET_20261111.csv">gp</a>
<a href="{BASE}/parametri/2026_11/PO_Parametri_E_20261111.csv">pe</a>
<a href="{BASE}/parametri/2026_11/PO_Parametri_G_20261111.csv">pg</a>
"""


def _ml(market: str, code: str, client: str = "01") -> str:
    return (
        f'<?xml version="1.0"?><ListaOfferteMercatoLibero xmlns="{NS}"><offerta>'
        f"<IdentificativiOfferta><PIVA_UTENTE>01234567890</PIVA_UTENTE><COD_OFFERTA>{code}</COD_OFFERTA></IdentificativiOfferta>"
        f"<DettaglioOfferta><TIPO_MERCATO>{market}</TIPO_MERCATO><TIPO_CLIENTE>{client}</TIPO_CLIENTE>"
        "<TIPO_OFFERTA>01</TIPO_OFFERTA><NOME_OFFERTA>Prova</NOME_OFFERTA>"
        "<Contatti><URL_SITO_VENDITORE>www.prova-energia.it</URL_SITO_VENDITORE></Contatti></DettaglioOfferta>"
        "</offerta></ListaOfferteMercatoLibero>"
    )


PLACET_HEADER = (
    "denominazione,codice_fiscale,p_iva,url_sito_venditore,telefono,nome_offerta,cod_offerta,url_offerta,"
    "modalita_attivazione,modalita_pagamento,data_inizio,data_fine,tipo_cliente,tipo_offerta,p_fix_f,p_fix_v,"
    "p_vol_f1,p_vol_f2,p_vol_f3,p_vol_bf1,p_vol_bf23,p_vol_mono,p_vol,alpha,regione,provincia,comune\n"
)
PLACET_ROW = (
    "PROVA ENERGIA SPA,0123,01234567890,https://prova.it,,PLACET,P1,,x,y,01/11/2026,31/12/2026,domestico,"
    "prezzo fisso,85,0,0,0,0,0,0,0.12,0.5,,,,\n"
)

ROUTES = {
    "open-data.page": PAGE,
    "storici.csv": "AnnoMese;PUN (€/kWh);PSV (€/Smc)\r\n202610;0,26;0,90\r\n",
    "PO_Offerte_E_MLIBERO_20261111.xml": _ml("01", "LUCE1") ,
    "PO_Offerte_G_MLIBERO_20261111.xml": _ml("02", "GAS1"),
    "PO_Offerte_E_PLACET_20261111.csv": PLACET_HEADER + PLACET_ROW,
    "PO_Offerte_G_PLACET_20261111.csv": PLACET_HEADER,
    "PO_Parametri_E_20261111.csv": "nome_parametro,valore,descrizione\nlambda,0.10,perdite\n",
    "PO_Parametri_G_20261111.csv": "nome_parametro,valore,descrizione\nqvd_f_d,60,QVD\n",
}


def _handler(request: httpx.Request) -> httpx.Response:
    name = request.url.path.rsplit("/", 1)[-1]
    body = ROUTES.get(name)
    if body is None:
        return httpx.Response(404)
    return httpx.Response(200, content=body.encode("cp1252" if name == "storici.csv" else "utf-8"))


def test_fetch_publication_end_to_end() -> None:
    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        published_on, links = discover_publication_date(client)
        pub = fetch_publication(client, published_on, links)
    assert published_on.isoformat() == "2026-11-11"
    codes = {(r["source"], r["offer_code"]) for r in pub.rows}
    assert codes == {("mercato_libero", "LUCE1"), ("mercato_libero", "GAS1"), ("placet", "P1")}
    luce = next(r for r in pub.rows if r["offer_code"] == "LUCE1")
    # Nome venditore ricavato dalla P.IVA incrociando il file PLACET
    assert luce["supplier_name"] == "PROVA ENERGIA SPA"
    assert pub.parameters_electricity == {"lambda": 0.10}
    assert pub.index_values["PUN"]["last"] == 0.26


def test_non_domestic_offers_are_dropped() -> None:
    routes = dict(ROUTES, **{"PO_Offerte_E_MLIBERO_20261111.xml": _ml("01", "BUSINESS", client="02")})
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        body = routes.get(name)
        return httpx.Response(200, content=body.encode("cp1252" if name == "storici.csv" else "utf-8")) if body else httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        published_on, links = discover_publication_date(client)
        pub = fetch_publication(client, published_on, links)
    assert "BUSINESS" not in {r["offer_code"] for r in pub.rows}


def test_direct_links_fallback_finds_latest_available_day() -> None:
    from datetime import date

    from app.market.ingest import direct_links

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        # Pubblicazione di oggi non ancora uscita: c'è solo quella di ieri
        return httpx.Response(200) if name == "PO_Offerte_E_MLIBERO_20261110.xml" else httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        day, links = direct_links(client, today=date(2026, 11, 11))
    assert day == date(2026, 11, 10)
    assert links.daily["ml_electricity"][0].endswith("/offerteML/2026_11/PO_Offerte_E_MLIBERO_20261110.xml")
    assert links.daily["params_gas"][0].endswith("/parametri/2026_11/PO_Parametri_G_20261110.csv")
    assert links.historical_prices is None


def test_direct_links_reports_block_clearly() -> None:
    import pytest

    from app.market.ingest import IngestError, direct_links

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client:
        with pytest.raises(IngestError, match="IP domestico"):
            direct_links(client)


def test_publication_from_local_folder(tmp_path) -> None:
    from app.market.ingest import publication_from_dir

    for name, body in ROUTES.items():
        if name == "open-data.page":
            continue
        target = "prezzi_storici.csv" if name == "storici.csv" else name
        (tmp_path / target).write_bytes(body.encode("cp1252" if name == "storici.csv" else "utf-8"))
    pub = publication_from_dir(tmp_path)
    assert pub.published_on.isoformat() == "2026-11-11"
    assert {r["offer_code"] for r in pub.rows} == {"LUCE1", "GAS1", "P1"}
    assert pub.index_values["PSV"]["last"] == 0.90
