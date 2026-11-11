"""Codici del tracciato open data del Portale Offerte usati dal calcolo.

Fonte: SII / Acquirente Unico, "Trasmissione Offerte Mercato Retail" sez. 7.1 (tracciato in
vigore dal 01/04/2026). Tabelle riprese da PortaleOfferteEnergiaMCP (Marco Patti, licenza MIT,
https://github.com/marcopa84/PortaleOfferteEnergiaMCP).
"""

from __future__ import annotations

TIPO_MERCATO_ELECTRICITY = "01"
TIPO_MERCATO_GAS = "02"

TIPO_CLIENTE_DOMESTICO = "01"

TIPO_OFFERTA = {"01": "fisso", "02": "variabile", "03": "flat", "04": "mista"}

# MACROAREA delle componenti impresa
MACROAREA_PREZZO_ENERGIA = "04"
MACROAREA_ENERGIA_VERDE = "06"

# UNITA_MISURA
UNIT_EUR_YEAR = "01"
UNIT_EUR_KW = "02"
UNIT_EUR_KWH = "03"
UNIT_EUR_SMC = "04"
UNIT_EUR = "05"
UNIT_PERCENT = "06"
UNIT_LABEL = {"01": "€/anno", "02": "€/kW", "03": "€/kWh", "04": "€/Smc", "05": "€", "06": "%"}

TIPOLOGIA_COMPONENTE_OPZIONALE = "02"

# Indice di riferimento delle offerte variabili → nome della serie storica pubblicata
INDEX_NAME = {
    "01": "PUN", "02": "TTF", "03": "PSV", "04": "Psbil", "05": "PE", "06": "CMEM", "07": "Pfor",
    "08": "PUN", "09": "TTF", "10": "PSV", "11": "Psbil",
    "12": "PUN", "13": "TTF", "14": "PSV", "15": "Psbil",
    "99": "altro",
}

# TIPO_DISPACCIAMENTO: solo il 99 è definito dal venditore, gli altri sono regolati
DISPATCH_SELLER_DEFINED = "99"

# Componenti regolate (codice → nome); i valori arrivano dal CSV dei parametri
REGULATED = {
    "01": "PCV", "02": "PPE", "03": "CCR", "04": "CPR", "05": "GRAD",
    "06": "QTint", "07": "QTpsv", "09": "QVD fissa", "10": "QVD variabile",
}

CONDIZIONE_SCONTO_NESSUNA = "00"
VALIDITA_SCONTO_OLTRE_12_MESI = "03"
TIPOLOGIA_SCONTO_SU_TUTELA = "04"

# Codici fascia del tracciato → chiave del profilo di consumo
FASCIA_F1_OR_MONO = "01"
DISCOUNT_TARGET_TO_FASCIA = {"11": "01", "12": "02", "13": "03", "91": "91", "92": "92", "93": "93"}

# Profili di ripartizione standard F1/F2/F3 (uguali a quelli usati dal progetto di riferimento)
PROFILE_DOMESTIC = {"F1": 0.33, "F2": 0.31, "F3": 0.36}

REGIONS = {
    "01": "Piemonte", "02": "Valle d'Aosta", "03": "Lombardia", "04": "Trentino-Alto Adige",
    "05": "Veneto", "06": "Friuli-Venezia Giulia", "07": "Liguria", "08": "Emilia-Romagna",
    "09": "Toscana", "10": "Umbria", "11": "Marche", "12": "Lazio", "13": "Abruzzo",
    "14": "Molise", "15": "Campania", "16": "Puglia", "17": "Basilicata", "18": "Calabria",
    "19": "Sicilia", "20": "Sardegna",
}
