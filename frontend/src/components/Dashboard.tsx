import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import { useComparisonTask } from "../hooks/useComparisonTask";
import { REGIONS, type Commodity, type ComparisonResult, type Invoice, type Profile } from "../types/energy";

const DAY_MS = 86_400_000;
const shortDate = (iso: string) => new Date(iso).toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "2-digit" });
const eur = new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" });

interface ChartPoint {
  label: string;
  dailyConsumption: number; // normalizzato: bollette mensili e bimestrali restano confrontabili
  effectiveUnitCost: number; // €/kWh o €/Smc tutto incluso (tasse e oneri compresi)
  lowConfidence: boolean;
}

function toChartData(invoices: Invoice[]): ChartPoint[] {
  return [...invoices]
    .sort((a, b) => a.period_end.localeCompare(b.period_end))
    .map((inv) => {
      const days = (Date.parse(inv.period_end) - Date.parse(inv.period_start)) / DAY_MS + 1;
      const consumption = Number(inv.consumption_value);
      return {
        label: new Date(inv.period_end).toLocaleDateString("it-IT", { month: "short", year: "2-digit" }),
        dailyConsumption: Number((consumption / days).toFixed(2)),
        effectiveUnitCost: consumption > 0 ? Number((Number(inv.total_amount_eur) / consumption).toFixed(4)) : 0,
        lowConfidence: Number(inv.confidence) < 0.7,
      };
    });
}

export function Dashboard() {
  const [commodity, setCommodity] = useState<Commodity>("electricity");
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const comparison = useComparisonTask();
  const [profile, setProfile] = useState<Profile | null>(null);

  useEffect(() => {
    api.getProfile().then(setProfile).catch(() => setProfile(null));
  }, []);

  async function onRegionChange(code: string) {
    try {
      setProfile(await api.updateRegion(code));
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Impossibile salvare la regione");
    }
  }

  const unit = commodity === "electricity" ? "kWh" : "Smc";

  const refresh = useCallback(async () => {
    try {
      setInvoices(await api.listInvoices(commodity));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Errore di caricamento");
    }
  }, [commodity]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const chartData = useMemo(() => toChartData(invoices), [invoices]);
  const lastTotal = invoices[0]?.total_amount_eur;

  async function onDelete(invoice: Invoice) {
    const label = `${invoice.supplier_name ?? "Fornitore non riconosciuto"} (${shortDate(invoice.period_start)}–${shortDate(invoice.period_end)})`;
    if (!window.confirm(`Eliminare la bolletta ${label}? L'operazione non si può annullare.`)) return;
    try {
      await api.deleteInvoice(invoice.id);
      await refresh();
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Eliminazione fallita");
    }
  }

  async function onFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // permette di ricaricare lo stesso file dopo un errore
    if (!file) return;
    // FUTURE-PROOF: con l'OCR attivo, accettare anche image/jpeg e image/png (attributo accept
    // dell'input + ACCEPTED_CONTENT_TYPES nel backend) e mostrare lo stato "in elaborazione".
    if (file.type !== "application/pdf") {
      setLoadError("Per ora sono supportati solo PDF scaricati dall'area clienti del fornitore");
      return;
    }
    setUploading(true);
    try {
      await api.uploadInvoice(file);
      await refresh();
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Upload fallito");
    } finally {
      setUploading(false);
    }
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold text-slate-900">I tuoi consumi</h1>
        <div className="flex gap-2" role="tablist">
          {(["electricity", "gas"] as const).map((c) => (
            <button
              key={c}
              role="tab"
              aria-selected={commodity === c}
              onClick={() => setCommodity(c)}
              className={`rounded-full px-4 py-1.5 text-sm font-medium ${
                commodity === c ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              }`}
            >
              {c === "electricity" ? "Luce" : "Gas"}
            </button>
          ))}
        </div>
      </header>

      {loadError && (
        <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {loadError}
        </p>
      )}

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-medium text-slate-900">Consumo medio giornaliero per bolletta</h2>
          {lastTotal && <span className="text-sm text-slate-500">Ultima bolletta: {eur.format(Number(lastTotal))}</span>}
        </div>

        {chartData.length === 0 ? (
          <p className="py-16 text-center text-sm text-slate-500">Carica la tua prima bolletta PDF per vedere il grafico.</p>
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                <YAxis yAxisId="consumption" tick={{ fontSize: 12 }} unit={` ${unit}`} width={72} />
                <YAxis yAxisId="cost" orientation="right" tick={{ fontSize: 12 }} unit=" €" width={56} />
                <Tooltip
                  formatter={(value: number, name: string) =>
                    name === "dailyConsumption" ? [`${value} ${unit}/giorno`, "Consumo"] : [`${value} €/${unit}`, "Costo unitario"]
                  }
                />
                <Legend formatter={(v) => (v === "dailyConsumption" ? `Consumo (${unit}/giorno)` : `Costo effettivo (€/${unit})`)} />
                <Bar yAxisId="consumption" dataKey="dailyConsumption" fill="#0f766e" radius={[4, 4, 0, 0]} />
                <Line yAxisId="cost" dataKey="effectiveUnitCost" stroke="#b45309" strokeWidth={2} dot={{ r: 3 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-3">
          <input ref={fileInput} type="file" accept="application/pdf" hidden onChange={onFileSelected} />
          <button
            onClick={() => fileInput.current?.click()}
            disabled={uploading}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-50"
          >
            {uploading ? "Analisi in corso…" : "Carica bolletta PDF"}
          </button>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <span>Regione di fornitura</span>
            <select
              value={profile?.region_code ?? ""}
              onChange={(e) => void onRegionChange(e.target.value)}
              className="rounded-lg border border-slate-300 px-2 py-2 text-sm"
            >
              <option value="" disabled>
                Scegli…
              </option>
              {REGIONS.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => void comparison.start(commodity)}
            disabled={comparison.isRunning || invoices.length === 0 || !profile?.region_code}
            title={!profile?.region_code ? "Scegli prima la regione: molte offerte valgono solo in alcune zone" : undefined}
            className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-50"
          >
            {comparison.isRunning ? "Confronto offerte in corso…" : "Confronta con il mercato"}
          </button>
        </div>
      </section>

      {invoices.length > 0 && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="font-medium text-slate-900">Le tue bollette</h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-500">
                <tr>
                  <th className="py-2 pr-4 font-medium">Periodo</th>
                  <th className="py-2 pr-4 font-medium">Fornitore</th>
                  <th className="py-2 pr-4 text-right font-medium">Consumo</th>
                  <th className="py-2 pr-4 text-right font-medium" title="Spesa per la materia, IVA esclusa">
                    Quota energia
                  </th>
                  <th className="py-2 pr-4 text-right font-medium">Totale</th>
                  <th className="py-2 pr-4 font-medium">Lettura</th>
                  <th className="py-2" aria-label="Azioni" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {invoices.map((inv) => {
                  const confidence = Number(inv.confidence);
                  return (
                    <tr key={inv.id} className="align-top">
                      <td className="py-2 pr-4 tabular-nums whitespace-nowrap">
                        {shortDate(inv.period_start)} – {shortDate(inv.period_end)}
                      </td>
                      <td className="py-2 pr-4">
                        {inv.supplier_name ?? <span className="text-slate-400">Non riconosciuto</span>}
                        {inv.warnings.length > 0 && (
                          <div className="text-xs text-amber-700">{inv.warnings.join(" · ")}</div>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums whitespace-nowrap">
                        {Number(inv.consumption_value).toLocaleString("it-IT")} {inv.consumption_unit}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        {inv.supply_cost_eur !== null ? (
                          eur.format(Number(inv.supply_cost_eur))
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">{eur.format(Number(inv.total_amount_eur))}</td>
                      <td className="py-2 pr-4">
                        <span
                          title={`Affidabilità dell'estrazione: ${Math.round(confidence * 100)}%`}
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                            confidence >= 0.9
                              ? "bg-teal-50 text-teal-800"
                              : confidence >= 0.7
                                ? "bg-amber-50 text-amber-800"
                                : "bg-red-50 text-red-700"
                          }`}
                        >
                          {confidence >= 0.9 ? "Alta" : confidence >= 0.7 ? "Media" : "Da verificare"}
                        </span>
                      </td>
                      <td className="py-2 text-right">
                        <button
                          onClick={() => void onDelete(inv)}
                          className="text-xs text-slate-500 underline hover:text-red-700"
                        >
                          Elimina
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {comparison.error && (
        <p role="alert" className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {comparison.error}
        </p>
      )}

      {comparison.task?.status === "succeeded" && comparison.task.result && (
        <ComparisonResultView result={comparison.task.result} />
      )}
    </main>
  );
}

function SavingsSummary({ result }: { result: ComparisonResult }) {
  const supplier = result.current_supplier ?? "il tuo fornitore attuale";
  if (!result.savings_available || result.current_annual_supplier_cost_eur === null) {
    return (
      <p className="mt-3 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
        Non ho trovato la voce "Spesa per la materia energia" nelle tue bollette, quindi posso classificare le
        offerte ma non calcolare il risparmio rispetto a {supplier}.
      </p>
    );
  }
  const current = Number(result.current_annual_supplier_cost_eur);
  const best = result.best_annual_saving_eur !== null ? Number(result.best_annual_saving_eur) : null;
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl bg-slate-50 p-4">
        <div className="text-xs uppercase tracking-wide text-slate-500">Oggi con {supplier}</div>
        <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{eur.format(current)}/anno</div>
        <div className="text-xs text-slate-500">quota energia stimata, IVA esclusa</div>
      </div>
      <div className={`rounded-xl p-4 ${best !== null && best > 0 ? "bg-teal-50" : "bg-slate-50"}`}>
        <div className="text-xs uppercase tracking-wide text-slate-500">Miglior risparmio possibile</div>
        {best !== null && best > 0 ? (
          <>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-teal-800">{eur.format(best)}/anno</div>
            <div className="text-xs text-slate-500">passando alla prima offerta in classifica</div>
          </>
        ) : (
          <>
            <div className="mt-1 text-lg font-semibold text-slate-900">Sei già sotto il mercato</div>
            <div className="text-xs text-slate-500">nessuna offerta valutata costa meno della tua</div>
          </>
        )}
      </div>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = { fisso: "prezzo fisso", variabile: "prezzo variabile", flat: "flat", mista: "mista" };

function ComparisonResultView({ result }: { result: ComparisonResult }) {
  const unit = result.unit;
  const excludedTotal = Object.values(result.excluded).reduce((a, b) => a + b, 0);
  const catalogDate = new Date(result.catalog_date).toLocaleDateString("it-IT");
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5">
      <h2 className="font-medium text-slate-900">Quanto risparmieresti cambiando offerta</h2>
      {result.catalog_stale && (
        <p className="mt-3 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Catalogo offerte non aggiornato (ultimo del {catalogDate}): i prezzi potrebbero essere cambiati.
        </p>
      )}
      <SavingsSummary result={result} />
      <p className="mt-3 text-xs text-slate-500">
        Consumo annuo stimato {Number(result.annual_consumption).toLocaleString("it-IT")} {unit} su {result.billed_days}{" "}
        giorni di bollette · offerte del Portale Offerte ARERA del {catalogDate}
        {result.region_name ? ` valide in ${result.region_name}` : ""}: {result.offers_eligible} confrontabili su{" "}
        {result.offers_evaluated}
        {excludedTotal > 0 && (
          <>
            {" "}(escluse:{" "}
            {Object.entries(result.excluded)
              .map(([reason, n]) => `${n} ${reason}`)
              .join(", ")}
            )
          </>
        )}
        .
        {result.index_reference?.value != null && (
          <>
            {" "}Offerte variabili stimate con {result.index_reference.name} {result.index_reference.month} ={" "}
            {result.index_reference.value.toLocaleString("it-IT", { maximumFractionDigits: 4 })} €/{unit}.
          </>
        )}{" "}
        {result.methodology}
      </p>
      {result.top_offers.length === 0 ? (
        <p className="mt-4 text-sm text-slate-600">Nessuna offerta confrontabile trovata per il tuo profilo.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-slate-500">
              <tr>
                <th className="py-2 pr-4 font-medium">Offerta</th>
                <th className="py-2 pr-4 text-right font-medium">Costo annuo</th>
                <th className="py-2 text-right font-medium">Risparmio annuo</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {result.top_offers.map((o) => {
                const saving = o.annual_saving_eur !== null ? Number(o.annual_saving_eur) : null;
                return (
                  <tr key={`${o.source}-${o.offer_code}`} className="align-top">
                    <td className="py-2 pr-4">
                      <div className="font-medium text-slate-900">
                        {o.url ? (
                          <a href={o.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                            {o.offer_name}
                          </a>
                        ) : (
                          o.offer_name
                        )}
                      </div>
                      <div className="text-xs text-slate-500">
                        {o.supplier_name} · {KIND_LABEL[o.price_kind ?? ""] ?? o.price_kind ?? "—"}
                        {o.source === "placet" && " · PLACET"}
                      </div>
                      {o.notes.length > 0 && <div className="text-xs text-slate-400">{o.notes.join(" ")}</div>}
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      {eur.format(Number(o.estimated_annual_supplier_cost_eur))}
                    </td>
                    <td
                      className={`py-2 text-right font-medium tabular-nums ${
                        saving === null ? "text-slate-400" : saving > 0 ? "text-teal-700" : "text-red-700"
                      }`}
                    >
                      {saving === null ? "—" : `${saving > 0 ? "−" : "+"}${eur.format(Math.abs(saving))}`}
                      {o.saving_pct !== null && saving !== null && (
                        <div className="text-xs font-normal text-slate-500">
                          {saving > 0 ? `${o.saving_pct}% in meno` : "costa di più"}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
