"""Lettura degli open data del Portale Offerte (ARERA / Acquirente Unico).

Formato e insidie, verificati sul progetto di riferimento PortaleOfferteEnergiaMCP (MIT):
  * la pagina Open Data elenca i file del giorno; i file restano online solo pochi giorni,
    quindi l'ingestione deve girare ogni giorno;
  * offerte mercato libero: XML con namespace SII, nessun nome venditore (solo P.IVA);
  * offerte PLACET: CSV UTF-8 con BOM, contiene la denominazione del venditore;
  * parametri regolati: CSV `nome_parametro,valore,descrizione`, senza unità di misura;
  * prezzi storici degli indici: CSV cp1252, separatore `;`, virgola decimale.

Il parsing XML è in streaming (`iterparse` + `clear`): il file luce supera i 10 MB e un albero
completo in memoria costerebbe 10-20 volte tanto.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Any

from app.market import codes as C

BASE_URL = "https://www.ilportaleofferte.it"
OPEN_DATA_PAGE = f"{BASE_URL}/portaleOfferte/it/open-data.page"
NS = "{http://www.acquirenteunico.it/schemas/SII_AU/OffertaRetail/01}"

DAILY_FILES = {
    "ml_electricity": re.compile(r"PO_Offerte_E_MLIBERO_(\d{8})\.xml"),
    "ml_gas": re.compile(r"PO_Offerte_G_MLIBERO_(\d{8})\.xml"),
    "placet_electricity": re.compile(r"PO_Offerte_E_PLACET_(\d{8})\.csv"),
    "placet_gas": re.compile(r"PO_Offerte_G_PLACET_(\d{8})\.csv"),
    "params_electricity": re.compile(r"PO_Parametri_E_(\d{8})\.csv"),
    "params_gas": re.compile(r"PO_Parametri_G_(\d{8})\.csv"),
}

Offer = dict[str, Any]  # modello normalizzato, serializzabile in JSONB


@dataclass(frozen=True, slots=True)
class OpenDataLinks:
    daily: dict[str, tuple[str, str]]  # nome logico → (url, AAAAMMGG)
    historical_prices: str | None

    @property
    def publication_date(self) -> str:
        return max(day for _, day in self.daily.values())

    def missing(self) -> set[str]:
        return set(DAILY_FILES) - set(self.daily)


def discover_links(html: str) -> OpenDataLinks:
    daily: dict[str, tuple[str, str]] = {}
    historical: str | None = None
    for href in re.findall(r'href="([^"]+)"', html):
        url = href if href.startswith("http") else BASE_URL + href
        filename = href.rsplit("/", 1)[-1]
        for name, pattern in DAILY_FILES.items():
            if match := pattern.fullmatch(filename):
                # Se la pagina elenca più giorni, tiene il più recente
                if name not in daily or match.group(1) > daily[name][1]:
                    daily[name] = (url, match.group(1))
        if historical is None and "/cms/documents/" in href and href.endswith(".csv"):
            historical = url
    return OpenDataLinks(daily=daily, historical_prices=historical)


# ---------------------------------------------------------------- XML mercato libero


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(NS + path.replace("/", "/" + NS))
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def _all(el: ET.Element, path: str) -> list[ET.Element]:
    return el.findall(NS + path.replace("/", "/" + NS))


def _texts(el: ET.Element, path: str) -> list[str]:
    return [e.text.strip() for e in _all(el, path) if e.text and e.text.strip()]


def _num(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value.replace(",", "."))


def _int(value: str | None) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _ml_datetime(value: str | None) -> str | None:
    """'13/10/2026_17:27:02' → '2026-10-13T17:27:02'."""
    if not value:
        return None
    return datetime.strptime(value, "%d/%m/%Y_%H:%M:%S").isoformat()


def _period(el: ET.Element | None) -> dict[str, Any]:
    if el is None:
        return {}
    period = {"duration_months": _int(_text(el, "DURATA")), "months": _texts(el, "MESE_VALIDITA")}
    return {k: v for k, v in period.items() if v}


def _ml_offer(o: ET.Element) -> Offer:
    detail = o.find(NS + "DettaglioOfferta")
    market = _text(detail, "TIPO_MERCATO")
    components = []
    for comp in _all(o, "ComponenteImpresa"):
        prices = []
        for interval in _all(comp, "IntervalloPrezzi"):
            price = {
                "fascia": _text(interval, "FASCIA_COMPONENTE"),
                "price": _num(_text(interval, "PREZZO")),
                "unit": _text(interval, "UNITA_MISURA"),
                "from": _int(_text(interval, "CONSUMO_DA")),
                "to": _int(_text(interval, "CONSUMO_A")),
                "period": _period(interval.find(NS + "PeriodoValidita")),
            }
            prices.append({k: v for k, v in price.items() if v not in (None, {})})
        components.append(
            {
                "name": _text(comp, "NOME"),
                "macroarea": _text(comp, "MACROAREA"),
                "tipologia": _text(comp, "TIPOLOGIA"),
                "prices": prices,
            }
        )
    discounts = []
    for d in _all(o, "Sconto"):
        discounts.append(
            {
                "name": _text(d, "NOME"),
                "targets": _texts(d, "CODICE_COMPONENTE_FASCIA"),
                "validity": _text(d, "VALIDITA"),
                "period": _period(d.find(NS + "PeriodoValidita")),
                "conditions": [_text(c, "CONDIZIONE_APPLICAZIONE") for c in _all(d, "Condizione")],
                "prices": [
                    {
                        "type": _text(p, "TIPOLOGIA"),
                        "from": _int(_text(p, "VALIDO_DA")),
                        "to": _int(_text(p, "VALIDO_FINO")),
                        "unit": _text(p, "UNITA_MISURA"),
                        "price": _num(_text(p, "PREZZO")),
                    }
                    for p in _all(d, "PrezziSconto")
                ],
            }
        )
    return {
        "source": "mercato_libero",
        "code": _text(o, "IdentificativiOfferta/COD_OFFERTA"),
        "piva": _text(o, "IdentificativiOfferta/PIVA_UTENTE"),
        "commodity": "electricity" if market == C.TIPO_MERCATO_ELECTRICITY else "gas" if market == C.TIPO_MERCATO_GAS else market,
        "name": _text(detail, "NOME_OFFERTA"),
        "client_type": _text(detail, "TIPO_CLIENTE"),
        "residence": _text(detail, "DOMESTICO_RESIDENTE"),
        "price_kind": C.TIPO_OFFERTA.get(_text(detail, "TIPO_OFFERTA") or "", "altro"),
        "single": _text(detail, "OFFERTA_SINGOLA") != "NO",
        "site": _text(detail, "Contatti/URL_SITO_VENDITORE"),
        "url": _text(detail, "Contatti/URL_OFFERTA"),
        "valid_from": _ml_datetime(_text(o, "ValiditaOfferta/DATA_INIZIO")),
        "valid_to": _ml_datetime(_text(o, "ValiditaOfferta/DATA_FINE")),
        "consumption_min": _int(_text(o, "CaratteristicheOfferta/CONSUMO_MIN")),
        "consumption_max": _int(_text(o, "CaratteristicheOfferta/CONSUMO_MAX")),
        "power_min": _num(_text(o, "CaratteristicheOfferta/POTENZA_MIN")),
        "power_max": _num(_text(o, "CaratteristicheOfferta/POTENZA_MAX")),
        "tipologia_fasce": _text(o, "TipoPrezzo/TIPOLOGIA_FASCE"),
        "indices": [
            {
                "code": _text(r, "IDX_PREZZO_ENERGIA"),
                "coefficient": _num(_text(r, "COEFFICIENTE")),
                "fascia": _text(r, "FASCIA_PREZZO"),
            }
            for r in _all(o, "RiferimentiPrezzoEnergia")
        ],
        "dispatch": [
            {"type": _text(d, "TIPO_DISPACCIAMENTO"), "value": _num(_text(d, "VALORE_DISP")), "name": _text(d, "NOME")}
            for d in _all(o, "Dispacciamento")
        ],
        "regulated": _texts(o, "ComponentiRegolate/CODICE"),
        "components": components,
        "discounts": discounts,
        "zones": {
            "regions": _texts(o, "ZoneOfferta/REGIONE"),
            "provinces": _texts(o, "ZoneOfferta/PROVINCIA"),
            "municipalities": _texts(o, "ZoneOfferta/COMUNE"),
        },
    }


def iter_ml_offers(source: IO[bytes]) -> Iterator[Offer]:
    """Streaming: ogni <offerta> viene letta, normalizzata e poi rimossa dalla memoria."""
    for _event, element in ET.iterparse(source, events=("end",)):
        if element.tag == NS + "offerta":
            yield _ml_offer(element)
            element.clear()


# ---------------------------------------------------------------------- CSV PLACET


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def _placet_date(value: str | None, end: bool) -> str | None:
    if not value:
        return None
    day = datetime.strptime(value.strip(), "%d/%m/%Y")
    if end:
        day = day.replace(hour=23, minute=59, second=59)
    return day.isoformat()


def iter_placet_offers(source: IO[bytes], commodity: str) -> Iterator[Offer]:
    for r in csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig")):
        fixed = (r.get("tipo_offerta") or "").strip() == "prezzo fisso"
        if commodity == "electricity":
            energy = {
                "F1": _num(r.get("p_vol_f1")), "F2": _num(r.get("p_vol_f2")), "F3": _num(r.get("p_vol_f3")),
                "F1_biorario": _num(r.get("p_vol_bf1")), "F23": _num(r.get("p_vol_bf23")),
                "mono": _num(r.get("p_vol_mono")),
            }
        else:
            energy = {"gas": _num(r.get("p_vol"))}
        yield {
            "source": "placet",
            "code": r.get("cod_offerta"),
            "piva": r.get("p_iva"),
            "seller": (r.get("denominazione") or "").strip() or None,
            "commodity": commodity,
            "name": r.get("nome_offerta"),
            "client_type": C.TIPO_CLIENTE_DOMESTICO if (r.get("tipo_cliente") or "").strip() == "domestico" else "02",
            "residence": None,
            "price_kind": "fisso" if fixed else "variabile",
            "single": True,
            "site": r.get("url_sito_venditore") or None,
            "url": r.get("url_offerta") or None,
            "valid_from": _placet_date(r.get("data_inizio"), end=False),
            "valid_to": _placet_date(r.get("data_fine"), end=True),
            "placet": {
                "fixed_year": _num(r.get("p_fix_f") if fixed else r.get("p_fix_v")),
                # Nel CSV le strutture di prezzo non usate valgono 0 invece di essere vuote
                "energy": {k: v for k, v in energy.items() if v},
                "alpha": _num(r.get("alpha")),
            },
            "zones": {
                "regions": _list(r.get("regione")),
                "provinces": _list(r.get("provincia")),
                "municipalities": _list(r.get("comune")),
            },
        }


def read_parameters(source: IO[bytes]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig")):
        value = _num((r.get("valore") or "").strip())
        if r.get("nome_parametro") and value is not None:
            out[r["nome_parametro"].strip()] = value
    return out


def read_historical_prices(source: IO[bytes]) -> list[dict[str, Any]]:
    """Righe mensili {"month": "AAAA-MM", "PUN": 0.12, "PSV": 0.45, ...}."""
    rows = csv.reader(io.TextIOWrapper(source, encoding="cp1252"), delimiter=";")
    header = next(rows)
    names = [col.partition(" (")[0].strip() for col in header[1:]]
    months = []
    for row in rows:
        if not row or not row[0].strip():
            continue
        month = row[0].strip()
        entry: dict[str, Any] = {"month": f"{month[:4]}-{month[4:6]}"}
        for name, value in zip(names, row[1:]):
            entry[name] = _num(value.strip())
        months.append(entry)
    return months


def index_values(months: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per ogni indice: ultimo valore disponibile e media degli ultimi 12 mesi pubblicati."""
    out: dict[str, dict[str, Any]] = {}
    names = {k for m in months for k in m if k != "month"}
    for name in names:
        series = [(m["month"], m[name]) for m in months if m.get(name) is not None]
        if not series:
            continue
        series.sort()
        last_12 = [v for _, v in series[-12:]]
        out[name] = {"last": series[-1][1], "last_month": series[-1][0], "avg_12m": sum(last_12) / len(last_12)}
    return out
