"""Costo annuo della quota venditore di un'offerta, confrontabile con la "spesa per la vendita"
della bolletta (formato ARERA "scontrino dell'energia").

Logica adattata da PortaleOfferteEnergiaMCP, modulo `costi.py` (Marco Patti, licenza MIT,
https://github.com/marcopa84/PortaleOfferteEnergiaMCP), che segue le "Regole per il calcolo
della spesa annua stimata" del SII con queste approssimazioni dichiarate:
  * offerte variabili: valore storico dell'indice, non le quotazioni forward (non sono open data);
  * TTF assente dai prezzi storici pubblicati: offerte TTF marcate come stima incompleta;
  * Peak/OffPeak approssimati con F1 e F2+F3;
  * sconti condizionati (bolletta web, domiciliazione) esclusi.

Differenza rispetto al progetto di riferimento, necessaria per il confronto con la bolletta:
la "spesa per la vendita" in bolletta INCLUDE il dispacciamento regolato, che negli open data
non c'è. Lo aggiungiamo uguale a tutte le offerte luce (`regulated_dispatch_eur_kwh`): la
classifica non cambia, ma il risparmio in euro torna corretto invece di risultare gonfiato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.market import codes as C

Offer = dict[str, Any]


@dataclass(slots=True)
class Estimate:
    annual_cost: float = 0.0
    complete: bool = True
    breakdown: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, item: str, amount: float) -> None:
        self.breakdown[item] = self.breakdown.get(item, 0.0) + amount
        self.annual_cost += amount

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


@dataclass(frozen=True, slots=True)
class PricingInputs:
    annual_consumption: float          # kWh (luce) o Smc (gas)
    parameters: dict[str, float]       # CSV parametri del giorno
    index_values: dict[str, float]     # es. {"PUN": 0.13, "PSV": 0.45}
    power_kw: float = 3.0
    profile: dict[str, float] = field(default_factory=lambda: dict(C.PROFILE_DOMESTIC))
    regulated_dispatch_eur_kwh: float = 0.0


class _Calc:
    def __init__(self, offer: Offer, inputs: PricingInputs) -> None:
        self.offer = offer
        self.inp = inputs
        self.electricity = offer["commodity"] == "electricity"
        total = sum(inputs.profile.values()) or 1.0
        self.fasce = {f: inputs.annual_consumption * inputs.profile.get(f, 0) / total for f in ("F1", "F2", "F3")}
        self.est = Estimate()
        self.sales_discount_base = 0.0   # base per gli sconti percentuali sulla vendita
        self.tutela_amount = 0.0         # base per gli sconti "su tutela"

    def consumption_for(self, fascia: str | None) -> float:
        if not self.electricity or fascia is None:
            return self.inp.annual_consumption
        f1, f2, f3 = self.fasce["F1"], self.fasce["F2"], self.fasce["F3"]
        if fascia == C.FASCIA_F1_OR_MONO:
            mono = self.offer.get("tipologia_fasce") in (None, "01")
            return self.inp.annual_consumption if mono else f1
        values = {"02": f2, "03": f3, "91": f2 + f3, "92": f1 + f3, "93": f1 + f2}
        if fascia in values:
            return values[fascia]
        if fascia in ("07", "08"):
            self.est.note("Fasce Peak/OffPeak approssimate con F1 e F2+F3.")
            return f1 if fascia == "07" else f2 + f3
        self.est.note(f"Fascia {fascia} non stimabile.")
        self.est.complete = False
        return 0.0


def _tier_quantity(quantity: float, lo: int | None, hi: int | None) -> float:
    if lo is None and hi is None:
        return quantity
    lower = max((lo or 0) - 1, 0)
    upper = quantity if hi is None else min(quantity, hi)
    return max(0.0, upper - lower)


def _in_tier(consumption: float, lo: int | None, hi: int | None) -> bool:
    return (lo is None or consumption >= lo) and (hi is None or consumption <= hi)


def _period_weights(prices: list[dict[str, Any]]) -> list[float]:
    """Frazione del primo anno coperta da ciascun prezzo dello stesso gruppo (fascia)."""
    weights = [0.0] * len(prices)
    covered = 0
    with_duration = sorted(
        (i for i, p in enumerate(prices) if p.get("period", {}).get("duration_months")),
        key=lambda i: prices[i]["period"]["duration_months"],
    )
    for i in with_duration:
        end = min(prices[i]["period"]["duration_months"], 12)
        weights[i] = max(end - covered, 0) / 12
        covered = max(covered, end)
    for i, p in enumerate(prices):
        if months := p.get("period", {}).get("months"):
            weights[i] = len(months) / 12
            covered += len(months)
    rest = max(12 - covered, 0) / 12
    for i, p in enumerate(prices):
        per = p.get("period", {})
        if not per.get("duration_months") and not per.get("months"):
            weights[i] = rest
    return weights


def _components(c: _Calc) -> None:
    for comp in c.offer.get("components", []):
        name = comp.get("name") or "componente"
        if comp.get("macroarea") == C.MACROAREA_ENERGIA_VERDE and comp.get("tipologia") == C.TIPOLOGIA_COMPONENTE_OPZIONALE:
            c.est.note(f"Componente opzionale esclusa: {name}.")
            continue
        groups: dict[str | None, list[dict[str, Any]]] = {}
        for price in comp.get("prices", []):
            groups.setdefault(price.get("fascia"), []).append(price)
        for fascia, prices in groups.items():
            for price, weight in zip(prices, _period_weights(prices)):
                value, unit = price.get("price"), price.get("unit")
                if value is None:
                    continue
                if weight == 0 and unit != C.UNIT_EUR:
                    continue  # prezzo valido solo dopo il primo anno
                lo, hi = price.get("from"), price.get("to")
                if unit in (C.UNIT_EUR_KWH, C.UNIT_EUR_SMC):
                    amount = value * _tier_quantity(c.consumption_for(fascia), lo, hi) * weight
                    c.est.add("energia", amount)
                    if comp.get("macroarea") == C.MACROAREA_PREZZO_ENERGIA:
                        c.sales_discount_base += amount
                elif unit == C.UNIT_EUR_YEAR:
                    if _in_tier(c.inp.annual_consumption, lo, hi):
                        c.est.add("quota fissa", value * weight)
                elif unit == C.UNIT_EUR_KW:
                    c.est.add("quota potenza", value * c.inp.power_kw * weight)
                elif unit == C.UNIT_EUR:
                    c.est.add("una tantum", value)
                else:
                    c.est.note(f"'{name}': unità {C.UNIT_LABEL.get(unit or '', unit)} non gestita.")
                    c.est.complete = False


def _indices(c: _Calc) -> None:
    losses = 1 + c.inp.parameters.get("lambda", 0.10) if c.electricity else 1.0
    for index in c.offer.get("indices", []):
        name = C.INDEX_NAME.get(index.get("code") or "", "altro")
        if name == "altro":
            c.est.note("Indice non standard: quota indice non stimata.")
            c.est.complete = False
            continue
        value = c.inp.index_values.get(name)
        if value is None:
            c.est.note(f"Valore dell'indice {name} non pubblicato: quota indice non stimata.")
            c.est.complete = False
            continue
        amount = value * (index.get("coefficient") or 1.0) * c.consumption_for(index.get("fascia")) * losses
        if name in ("PE", "CMEM"):
            c.est.add("prezzo di tutela", amount)
            c.tutela_amount += amount
        else:
            c.est.add("indice", amount)
            c.sales_discount_base += amount


def _seller_dispatch(c: _Calc) -> None:
    for d in c.offer.get("dispatch", []):
        if d.get("type") == C.DISPATCH_SELLER_DEFINED and d.get("value") is not None:
            c.est.add("dispacciamento venditore", d["value"] * c.inp.annual_consumption)


def _regulated(c: _Calc) -> None:
    domestic = c.offer.get("client_type") != "02"
    params = c.inp.parameters
    for code in c.offer.get("regulated", []):
        name = C.REGULATED.get(code, code)
        key: str | None = None
        per_unit = False
        if c.electricity and code == "01":
            key = "pcv_c" if domestic else "pcv_a"
        elif not c.electricity and code == "09":
            key = "qvd_f_d" if domestic else "qvd_f_nd"
        elif not c.electricity and code in ("04", "05", "06", "07", "10"):
            key = {"04": "cpr", "05": "grad", "06": "qt", "07": "qt", "10": "qvd_v_d" if domestic else "qvd_v_nd"}[code]
            per_unit = True
        if key is not None and key in params:
            c.est.add("componenti regolate", params[key] * (c.inp.annual_consumption if per_unit else 1.0))
        else:
            c.est.note(f"Componente regolata {name} non stimata.")
            c.est.complete = False


def _discounts(c: _Calc) -> None:
    for d in c.offer.get("discounts", []):
        conditions = [x for x in d.get("conditions", []) if x]
        if any(x != C.CONDIZIONE_SCONTO_NESSUNA for x in conditions):
            continue  # condizionato a scelte del cliente: escluso per prudenza
        if d.get("validity") == C.VALIDITA_SCONTO_OLTRE_12_MESI:
            continue
        duration = d.get("period", {}).get("duration_months")
        weight = min(duration, 12) / 12 if duration else 1.0
        targets = [C.DISCOUNT_TARGET_TO_FASCIA[t] for t in d.get("targets", []) if t in C.DISCOUNT_TARGET_TO_FASCIA]
        for p in d.get("prices", []):
            value, unit = p.get("price"), p.get("unit")
            if value is None:
                continue
            if p.get("type") == C.TIPOLOGIA_SCONTO_SU_TUTELA:
                if unit == C.UNIT_PERCENT:
                    c.est.add("sconti", -c.tutela_amount * value / 100)
                elif unit in (C.UNIT_EUR_KWH, C.UNIT_EUR_SMC):
                    c.est.add("sconti", -value * c.inp.annual_consumption)
                continue
            lo, hi = p.get("from"), p.get("to")
            if unit in (C.UNIT_EUR_KWH, C.UNIT_EUR_SMC):
                qty = sum(c.consumption_for(f) for f in targets) if targets else c.inp.annual_consumption
                amount = value * _tier_quantity(qty, lo, hi) * weight
            elif unit == C.UNIT_EUR_YEAR:
                amount = value * weight if _in_tier(c.inp.annual_consumption, lo, hi) else 0.0
            elif unit == C.UNIT_EUR_KW:
                amount = value * c.inp.power_kw * weight
            elif unit == C.UNIT_EUR:
                amount = value
            elif unit == C.UNIT_PERCENT:
                amount = c.sales_discount_base * value / 100 * weight
            else:
                continue
            c.est.add("sconti", -abs(amount))


def _placet(c: _Calc) -> None:
    data = c.offer["placet"]
    if data.get("fixed_year") is not None:
        c.est.add("quota fissa", data["fixed_year"])
    prices = data.get("energy", {})
    if c.offer["price_kind"] == "fisso":
        if not c.electricity:
            c.est.add("energia", prices.get("gas", 0.0) * c.inp.annual_consumption)
        elif {"F1", "F2", "F3"} <= prices.keys():
            for f in ("F1", "F2", "F3"):
                c.est.add("energia", prices[f] * c.fasce[f])
        elif {"F1_biorario", "F23"} <= prices.keys():
            c.est.add("energia", prices["F1_biorario"] * c.fasce["F1"])
            c.est.add("energia", prices["F23"] * (c.fasce["F2"] + c.fasce["F3"]))
        elif "mono" in prices:
            c.est.add("energia", prices["mono"] * c.inp.annual_consumption)
        else:
            c.est.complete = False
        return
    name = "PUN" if c.electricity else "PSV"
    value = c.inp.index_values.get(name)
    if value is None:
        c.est.note(f"Valore dell'indice {name} non pubblicato.")
        c.est.complete = False
    else:
        losses = 1 + c.inp.parameters.get("lambda", 0.10) if c.electricity else 1.0
        c.est.add("indice", value * losses * c.inp.annual_consumption)
    if data.get("alpha") is not None:
        c.est.add("energia", data["alpha"] * c.inp.annual_consumption)


def estimate_annual_cost(offer: Offer, inputs: PricingInputs) -> Estimate:
    c = _Calc(offer, inputs)
    if offer.get("source") == "placet":
        _placet(c)
    else:
        _components(c)
        _indices(c)
        if c.electricity:
            _seller_dispatch(c)
        _regulated(c)
        _discounts(c)
    if c.electricity and inputs.regulated_dispatch_eur_kwh:
        c.est.add("dispacciamento regolato", inputs.regulated_dispatch_eur_kwh * inputs.annual_consumption)
    if offer.get("price_kind") == "flat":
        c.est.note("Offerta flat: il prezzo vale solo entro i consumi minimo e massimo indicati.")
    return c.est


def eligible_for(offer: Offer, *, region: str | None, annual_consumption: float, power_kw: float) -> tuple[bool, str | None]:
    """Filtri di ammissibilità. Restituisce (ammessa, motivo dell'esclusione)."""
    if offer.get("client_type") != C.TIPO_CLIENTE_DOMESTICO:
        return False, "non domestica"
    if not offer.get("single", True):
        return False, "solo congiunta"
    zones = offer.get("zones") or {}
    regions, provinces, municipalities = zones.get("regions") or [], zones.get("provinces") or [], zones.get("municipalities") or []
    if provinces or municipalities:
        # Con la sola regione non possiamo sapere se l'utente ricade nella provincia o nel comune
        return False, "zona più ristretta della regione"
    if regions and (region is None or region not in regions):
        return False, "fuori regione"
    lo, hi = offer.get("consumption_min"), offer.get("consumption_max")
    if (lo is not None and annual_consumption < lo) or (hi is not None and hi > 0 and annual_consumption > hi):
        return False, "fuori dai limiti di consumo"
    pmin, pmax = offer.get("power_min"), offer.get("power_max")
    if (pmin is not None and power_kw < pmin) or (pmax is not None and pmax > 0 and power_kw > pmax):
        return False, "fuori dai limiti di potenza"
    if offer.get("commodity") == "dual":
        return False, "dual"
    return True, None
