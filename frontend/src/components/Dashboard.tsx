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
import type { Commodity, Invoice } from "../types/energy";

const DAY_MS = 86_400_000;
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
          <button
            onClick={() => void comparison.start(commodity)}
            disabled={comparison.isRunning || invoices.length === 0}
            className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-50"
          >
            {comparison.isRunning ? "Confronto offerte in corso…" : "Confronta con il mercato"}
          </button>
        </div>
      </section>

      {comparison.error && (
        <p role="alert" className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {comparison.error}
        </p>
      )}

      {comparison.task?.status === "succeeded" && comparison.task.result && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="font-medium text-slate-900">Offerte più convenienti per il tuo profilo</h2>
          <p className="mt-1 text-sm text-slate-500">
            Consumo annuo stimato: {Number(comparison.task.result.annual_consumption).toLocaleString("it-IT")} {unit} ·{" "}
            {comparison.task.result.offers_evaluated} offerte valutate. {comparison.task.result.methodology}
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-500">
                <tr>
                  <th className="py-2 pr-4 font-medium">Offerta</th>
                  <th className="py-2 pr-4 font-medium">Prezzo</th>
                  <th className="py-2 pr-4 font-medium">Quota fissa</th>
                  <th className="py-2 text-right font-medium">Stima annua (quota venditore)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {comparison.task.result.top_offers.map((o) => (
                  <tr key={o.offer_code}>
                    <td className="py-2 pr-4">
                      <div className="font-medium text-slate-900">{o.offer_name}</div>
                      <div className="text-xs text-slate-500">
                        {o.supplier_name} · {o.price_type === "fixed" ? "prezzo fisso" : "prezzo variabile"}
                      </div>
                    </td>
                    <td className="py-2 pr-4 tabular-nums">{Number(o.energy_price_eur).toFixed(4)} €/{unit}</td>
                    <td className="py-2 pr-4 tabular-nums">{eur.format(Number(o.fixed_fee_eur_month))}/mese</td>
                    <td className="py-2 text-right font-medium tabular-nums">
                      {eur.format(Number(o.estimated_annual_supplier_cost_eur))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}
