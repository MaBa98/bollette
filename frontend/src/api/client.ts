import { createClient } from "@supabase/supabase-js";
import type { Commodity, ComparisonTask, Invoice, Profile } from "../types/energy";

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY, // o la publishable key nei progetti recenti
);

const API_BASE = `${import.meta.env.VITE_API_URL}/api/v1`;

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

async function authHeader(): Promise<Record<string, string>> {
  // getSession() rinnova il token se scaduto: il JWT viaggia sempre aggiornato.
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError(401, "Sessione scaduta, effettua di nuovo il login");
  return { Authorization: `Bearer ${token}` };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(await authHeader()), ...init.headers },
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new ApiError(res.status, body.detail ?? `Errore ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listInvoices: (commodity?: Commodity) =>
    request<Invoice[]>(`/invoices${commodity ? `?commodity=${commodity}` : ""}`),

  // Body grezzo, non multipart: il backend legge lo stream in RAM senza spool su disco.
  uploadInvoice: (file: File) =>
    request<Invoice>("/invoices", {
      method: "POST",
      body: file,
      headers: {
        "Content-Type": file.type || "application/pdf",
        "X-Filename": encodeURIComponent(file.name),
      },
    }),

  getProfile: () => request<Profile>("/me"),

  updateRegion: (region_code: string) =>
    request<Profile>("/me", {
      method: "PUT",
      body: JSON.stringify({ region_code }),
      headers: { "Content-Type": "application/json" },
    }),

  deleteInvoice: (id: string) => request<void>(`/invoices/${id}`, { method: "DELETE" }),

  startComparison: (commodity: Commodity) =>
    request<ComparisonTask>("/comparisons", {
      method: "POST",
      body: JSON.stringify({ commodity }),
      headers: { "Content-Type": "application/json" },
    }),

  getComparison: (taskId: string, signal?: AbortSignal) =>
    request<ComparisonTask>(`/comparisons/${taskId}`, { signal }),
};
