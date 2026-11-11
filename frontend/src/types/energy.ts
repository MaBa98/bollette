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
  price_type: "fixed" | "variable";
  energy_price_eur: string;
  fixed_fee_eur_month: string;
  estimated_annual_supplier_cost_eur: string;
}

export interface ComparisonResult {
  annual_consumption: string;
  unit: "kWh" | "Smc";
  offers_evaluated: number;
  top_offers: RankedOffer[];
  methodology: string;
}

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
