// Specchio esatto di app/api/schemas.py. I Decimal arrivano come stringhe: si convertono
// solo per il grafico, mai per somme monetarie (evita errori di floating point).

export type Commodity = "electricity" | "gas";
export type Provider = "enel" | "plenitude" | "unknown";
export type SourceKind = "native_pdf" | "scanned_pdf" | "image";
export type TaskStatus = "pending" | "running" | "succeeded" | "failed";

export interface Invoice {
  id: string;
  provider: Provider;
  commodity: Commodity;
  period_start: string; // ISO date
  period_end: string;
  consumption_value: string;
  consumption_unit: "kWh" | "Smc";
  total_amount_eur: string;
  supply_cost_eur: string | null; // quota materia, IVA esclusa
  supply_point_code: string | null;
  supplier_name: string | null;
  source_kind: SourceKind;
  confidence: string;
  warnings: string[];
  created_at: string;
}

export interface RankedOffer {
  offer_code: string;
  supplier_name: string;
  offer_name: string;
  price_kind: "fisso" | "variabile" | "flat" | "mista" | string | null;
  source: "mercato_libero" | "placet";
  url: string | null;
  estimated_annual_supplier_cost_eur: string;
  annual_saving_eur: string | null; // negativo = l'offerta costa più di quella attuale
  saving_pct: string | null;
  breakdown: Record<string, number>;
  notes: string[];
}

export interface ComparisonResult {
  annual_consumption: string;
  unit: "kWh" | "Smc";
  billed_days: number;
  current_supplier: string | null;
  current_annual_supplier_cost_eur: string | null;
  best_annual_saving_eur: string | null;
  savings_available: boolean;
  region_code: string | null;
  region_name: string | null;
  catalog_date: string;
  catalog_stale: boolean;
  offers_evaluated: number;
  offers_eligible: number;
  excluded: Record<string, number>;
  index_reference: { name: string; value: number | null; month: string | null } | null;
  top_offers: RankedOffer[];
  methodology: string;
}

export interface Profile {
  email: string | null;
  region_code: string | null;
  region_name: string | null;
}

// Codici ISTAT delle regioni, come nel tracciato del Portale Offerte
export const REGIONS: ReadonlyArray<[code: string, name: string]> = [
  ["01", "Piemonte"], ["02", "Valle d'Aosta"], ["03", "Lombardia"], ["04", "Trentino-Alto Adige"],
  ["05", "Veneto"], ["06", "Friuli-Venezia Giulia"], ["07", "Liguria"], ["08", "Emilia-Romagna"],
  ["09", "Toscana"], ["10", "Umbria"], ["11", "Marche"], ["12", "Lazio"], ["13", "Abruzzo"],
  ["14", "Molise"], ["15", "Campania"], ["16", "Puglia"], ["17", "Basilicata"], ["18", "Calabria"],
  ["19", "Sicilia"], ["20", "Sardegna"],
];

export interface ComparisonTask {
  id: string;
  commodity: Commodity;
  status: TaskStatus;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  result: ComparisonResult | null;
}

export const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set(["succeeded", "failed"]);
