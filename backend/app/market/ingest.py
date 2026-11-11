"""Ingestione giornaliera degli open data del Portale Offerte nel database.

Uso:  python -m app.market.ingest [--force]

Pensato per girare in GitHub Actions (vedi .github/workflows/ingest-offers.yml): è un processo
separato dal backend, così i ~40 MB di file e il parsing non pesano sull'istanza Render da 512 MB.
Idempotente: se la pubblicazione del giorno è già nel database non fa nulla (salvo --force).
Conserva solo l'ultima pubblicazione, in un'unica transazione: o tutto o niente.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.db.models import MarketOffer, MarketSnapshot
from app.market import codes as C
from app.market.portale import (
    BASE_URL,
    DAILY_FILES,
    OPEN_DATA_PAGE,
    Offer,
    OpenDataLinks,
    discover_links,
    index_values,
    iter_ml_offers,
    iter_placet_offers,
    read_historical_prices,
    read_parameters,
)

logger = logging.getLogger("ingest")
USER_AGENT = "bollette-saas-ingest/1.0 (+https://github.com/MaBa98/bollette)"
# Client dichiarato onestamente (nessuna finta identità da browser), con gli header standard
# che un client HTTP ben educato invia.
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xml,text/csv,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}
BATCH_SIZE = 500


class IngestError(RuntimeError):
    pass


def _download(client: httpx.Client, url: str) -> io.BytesIO:
    response = client.get(url)
    response.raise_for_status()
    logger.info("Scaricato %s (%.1f MB)", url.rsplit("/", 1)[-1], len(response.content) / 1_048_576)
    return io.BytesIO(response.content)


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    return host.removeprefix("www.") or None


def _row(offer: Offer, published_on: date, sellers: dict[str, str]) -> dict[str, Any]:
    piva = (offer.get("piva") or "").strip() or None
    supplier = offer.get("seller") or (sellers.get(piva) if piva else None) or _domain(offer.get("site"))
    valid_to = datetime.fromisoformat(offer["valid_to"]) if offer.get("valid_to") else None
    return {
        "id": uuid.uuid4(),
        "published_on": published_on,
        "source": offer["source"],
        "offer_code": (offer.get("code") or "")[:64],
        "piva": piva,
        "supplier_name": supplier[:200] if supplier else None,
        "offer_name": (offer.get("name") or "")[:300] or None,
        "commodity": offer["commodity"],
        "client_type": offer.get("client_type"),
        "price_kind": offer.get("price_kind"),
        "valid_to": valid_to,
        "data": offer,
    }


def _keep(offer: Offer) -> bool:
    """Solo offerte domestiche luce o gas: le altre non servono al prodotto."""
    return offer.get("client_type") == C.TIPO_CLIENTE_DOMESTICO and offer.get("commodity") in ("electricity", "gas")


def _dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str | None]] = set()
    out = []
    for r in rows:
        key = (r["source"], r["offer_code"], r["piva"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


async def _store(
    session: AsyncSession,
    published_on: date,
    rows: list[dict[str, Any]],
    params_e: dict[str, float],
    params_g: dict[str, float],
    indices: dict[str, Any],
) -> None:
    async with session.begin():
        await session.execute(delete(MarketSnapshot).where(MarketSnapshot.published_on == published_on))
        await session.execute(delete(MarketOffer))  # si tiene solo l'ultima pubblicazione
        for start in range(0, len(rows), BATCH_SIZE):
            await session.execute(insert(MarketOffer), rows[start : start + BATCH_SIZE])
        session.add(
            MarketSnapshot(
                published_on=published_on,
                parameters_electricity=params_e,
                parameters_gas=params_g,
                index_values=indices,
                offers_electricity=sum(r["commodity"] == "electricity" for r in rows),
                offers_gas=sum(r["commodity"] == "gas" for r in rows),
            )
        )


@dataclass(frozen=True, slots=True)
class Publication:
    published_on: date
    rows: list[dict[str, Any]]
    parameters_electricity: dict[str, float]
    parameters_gas: dict[str, float]
    index_values: dict[str, Any]


# Schema degli URL dei file giornalieri (osservato sul portale): il mese è senza zero iniziale.
_DIRECT_PATHS = {
    "ml_electricity": "offerteML/{y}_{m}/PO_Offerte_E_MLIBERO_{ymd}.xml",
    "ml_gas": "offerteML/{y}_{m}/PO_Offerte_G_MLIBERO_{ymd}.xml",
    "placet_electricity": "offerte/{y}_{m}/PO_Offerte_E_PLACET_{ymd}.csv",
    "placet_gas": "offerte/{y}_{m}/PO_Offerte_G_PLACET_{ymd}.csv",
    "params_electricity": "parametri/{y}_{m}/PO_Parametri_E_{ymd}.csv",
    "params_gas": "parametri/{y}_{m}/PO_Parametri_G_{ymd}.csv",
}
_DIRECT_BASE = f"{BASE_URL}/portaleOfferte/resources/opendata/csv/"
_BLOCKED = {401, 403, 429}


def discover_publication_date(client: httpx.Client) -> tuple[date, OpenDataLinks]:
    page = client.get(OPEN_DATA_PAGE)
    page.raise_for_status()
    links = discover_links(page.text)
    if missing := links.missing():
        raise IngestError(f"La pagina Open Data non elenca: {', '.join(sorted(missing))}. Il formato del portale è cambiato?")
    return datetime.strptime(links.publication_date, "%Y%m%d").date(), links


def direct_links(client: httpx.Client, today: date | None = None, lookback_days: int = 4) -> tuple[date, OpenDataLinks]:
    """Ripiego se la pagina Open Data è bloccata: prova gli URL dei file degli ultimi giorni.

    Lo storico degli indici ha un nome non prevedibile, quindi qui non è disponibile: il chiamante
    riusa i valori dell'ultima importazione riuscita.
    """
    today = today or date.today()
    statuses: list[str] = []
    for delta in range(lookback_days + 1):
        day = today - timedelta(days=delta)
        fmt = {"y": day.year, "m": day.month, "ymd": day.strftime("%Y%m%d")}
        urls = {name: _DIRECT_BASE + path.format(**fmt) for name, path in _DIRECT_PATHS.items()}
        probe = client.head(urls["ml_electricity"])
        statuses.append(f"{day.isoformat()}: {probe.status_code}")
        if probe.status_code in _BLOCKED:
            raise IngestError(
                "Anche i file del Portale rifiutano la richiesta da questo server "
                f"(HTTP {probe.status_code}). Esegui l'importazione da un computer con IP domestico "
                "oppure scarica i file dal browser e usa --from-dir."
            )
        if probe.status_code == 200:
            daily = {name: (url, fmt["ymd"]) for name, url in urls.items()}
            return day, OpenDataLinks(daily=daily, historical_prices=None)
    raise IngestError("Nessun file trovato negli ultimi giorni (" + "; ".join(statuses) + ")")


def build_publication(published_on: date, files: dict[str, IO[bytes]], months: list[dict[str, Any]]) -> Publication:
    """Normalizza i file del giorno, da qualunque fonte arrivino (rete o cartella locale)."""
    placet = [
        *iter_placet_offers(files["placet_electricity"], "electricity"),
        *iter_placet_offers(files["placet_gas"], "gas"),
    ]
    sellers = {o["piva"].strip(): o["seller"] for o in placet if o.get("piva") and o.get("seller")}
    offers = [o for o in (*iter_ml_offers(files["ml_electricity"]), *iter_ml_offers(files["ml_gas"]), *placet) if _keep(o)]
    rows = _dedupe(_row(o, published_on, sellers) for o in offers)
    if not rows:
        raise IngestError("Nessuna offerta domestica letta: file vuoti o formato cambiato")
    return Publication(
        published_on=published_on,
        rows=rows,
        parameters_electricity=read_parameters(files["params_electricity"]),
        parameters_gas=read_parameters(files["params_gas"]),
        index_values=index_values(months),
    )


def fetch_publication(client: httpx.Client, published_on: date, links: OpenDataLinks) -> Publication:
    files = {name: _download(client, url) for name, (url, _day) in links.daily.items()}
    months = read_historical_prices(_download(client, links.historical_prices)) if links.historical_prices else []
    return build_publication(published_on, files, months)


def publication_from_dir(folder: Path) -> Publication:
    """Importa file scaricati a mano dalla pagina Open Data (i nomi originali vanno mantenuti)."""
    if not folder.is_dir():
        raise IngestError(f"Cartella non trovata: {folder}")
    found: dict[str, tuple[Path, str]] = {}
    historical: Path | None = None
    for path in folder.iterdir():
        for name, pattern in DAILY_FILES.items():
            if (m := pattern.fullmatch(path.name)) and (name not in found or m.group(1) > found[name][1]):
                found[name] = (path, m.group(1))
        if path.suffix.lower() == ".csv" and historical is None:
            with path.open("rb") as fh:
                if fh.read(64).lstrip(b"\xef\xbb\xbf").startswith(b"AnnoMese"):
                    historical = path
    if missing := set(DAILY_FILES) - set(found):
        raise IngestError(f"Nella cartella mancano: {', '.join(sorted(missing))}")
    published_on = datetime.strptime(max(day for _, day in found.values()), "%Y%m%d").date()
    files = {name: io.BytesIO(path.read_bytes()) for name, (path, _day) in found.items()}
    months = read_historical_prices(io.BytesIO(historical.read_bytes())) if historical else []
    return build_publication(published_on, files, months)


async def run(force: bool = False, from_dir: Path | None = None) -> int:
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), connect_args={"statement_cache_size": 0})
    try:
        async with AsyncSession(engine) as session:
            if from_dir is not None:
                pub = publication_from_dir(from_dir)
            else:
                with httpx.Client(headers=_HEADERS, timeout=httpx.Timeout(30, read=300), follow_redirects=True) as client:
                    try:
                        published_on, links = discover_publication_date(client)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code not in _BLOCKED:
                            raise
                        logger.warning("Pagina Open Data bloccata (HTTP %s): provo gli URL diretti dei file", exc.response.status_code)
                        published_on, links = direct_links(client)
                    existing = await session.scalar(
                        select(MarketSnapshot.published_on).where(MarketSnapshot.published_on == published_on)
                    )
                    if existing and not force:
                        logger.info("Pubblicazione del %s già presente: niente da fare", published_on)
                        return 0
                    pub = fetch_publication(client, published_on, links)

            indices = pub.index_values
            if not indices:
                previous = await session.scalar(
                    select(MarketSnapshot.index_values).order_by(MarketSnapshot.published_on.desc()).limit(1)
                )
                indices = previous or {}
                logger.warning(
                    "Storico indici non disponibile: %s",
                    "riuso i valori dell'importazione precedente" if previous else "le offerte variabili risulteranno incomplete",
                )
            await _store(session, pub.published_on, pub.rows, pub.parameters_electricity, pub.parameters_gas, indices)
    finally:
        await engine.dispose()

    rows = pub.rows
    logger.info(
        "Pubblicazione %s salvata: %d offerte domestiche (%d luce, %d gas), %d senza nome venditore",
        pub.published_on, len(rows),
        sum(r["commodity"] == "electricity" for r in rows), sum(r["commodity"] == "gas" for r in rows),
        sum(1 for r in rows if not r["supplier_name"]),
    )
    return len(rows)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Importa le offerte del Portale Offerte")
    parser.add_argument("--force", action="store_true", help="reimporta anche se la pubblicazione è già presente")
    parser.add_argument("--from-dir", type=Path, help="importa file già scaricati dalla pagina Open Data")
    args = parser.parse_args()
    try:
        asyncio.run(run(force=args.force, from_dir=args.from_dir))
    except (IngestError, httpx.HTTPError) as exc:
        logger.error("Ingestione fallita: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
