import { useState } from "react";
import { supabase } from "../api/client";

/** Login senza password via link magico: nessuna password da gestire né da far dimenticare. */
export function Login() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("sending");
    setError(null);
    const { error: err } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.origin },
    });
    if (err) {
      setError(err.message);
      setStatus("idle");
    } else {
      setStatus("sent");
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-4">
      <h1 className="text-2xl font-semibold text-slate-900">Bollette</h1>
      <p className="mt-1 text-sm text-slate-500">Monitora i consumi e confronta le offerte del mercato libero.</p>

      {status === "sent" ? (
        <p className="mt-6 rounded-lg bg-teal-50 px-4 py-3 text-sm text-teal-800">
          Ti abbiamo inviato un link di accesso a <strong>{email}</strong>. Aprilo da questo dispositivo.
        </p>
      ) : (
        <form onSubmit={onSubmit} className="mt-6 space-y-3">
          <label className="block text-sm font-medium text-slate-700" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-600 focus:outline-none"
          />
          <button
            type="submit"
            disabled={status === "sending"}
            className="w-full rounded-lg bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-50"
          >
            {status === "sending" ? "Invio in corso…" : "Ricevi link di accesso"}
          </button>
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        </form>
      )}
    </main>
  );
}
