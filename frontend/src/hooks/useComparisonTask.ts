import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { TERMINAL_STATUSES, type Commodity, type ComparisonTask } from "../types/energy";

const BASE_DELAY_MS = 1500;
const MAX_DELAY_MS = 8000;
const TIMEOUT_MS = 3 * 60_000;

/**
 * Avvia un job di comparazione e fa polling con backoff esponenziale finché non termina.
 * Polling e non WebSocket/SSE: sul free tier le connessioni lunghe vengono chiuse e il polling
 * sopravvive anche a un cold start del backend.
 */
export function useComparisonTask() {
  const [task, setTask] = useState<ComparisonTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => abortRef.current?.abort(), []);
  useEffect(() => stop, [stop]); // cleanup all'unmount

  const poll = useCallback(async (initial: ComparisonTask, signal: AbortSignal) => {
    const startedAt = Date.now();
    let current = initial;
    let delay = BASE_DELAY_MS;
    while (!TERMINAL_STATUSES.has(current.status)) {
      if (Date.now() - startedAt > TIMEOUT_MS) throw new Error("La comparazione sta impiegando troppo, riprova");
      await new Promise((r) => setTimeout(r, delay));
      if (signal.aborted) return;
      current = await api.getComparison(current.id, signal);
      setTask(current);
      delay = Math.min(delay * 1.5, MAX_DELAY_MS);
    }
    if (current.status === "failed") setError(current.error ?? "Comparazione fallita");
  }, []);

  const start = useCallback(
    async (commodity: Commodity) => {
      stop();
      const controller = new AbortController();
      abortRef.current = controller;
      setError(null);
      try {
        const created = await api.startComparison(commodity);
        setTask(created);
        await poll(created, controller.signal);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e instanceof ApiError || e instanceof Error ? e.message : "Errore imprevisto");
      }
    },
    [poll, stop],
  );

  const isRunning = task !== null && !TERMINAL_STATUSES.has(task.status) && error === null;
  return { task, error, isRunning, start };
}
