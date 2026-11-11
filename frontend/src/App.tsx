import type { Session } from "@supabase/supabase-js";
import { useEffect, useState } from "react";
import { supabase } from "./api/client";
import { Dashboard } from "./components/Dashboard";
import { Login } from "./components/Login";

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // getSession legge anche il token di ritorno dal link magico presente nell'URL.
    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);

  if (!ready) return null;
  if (!session) return <Login />;

  return (
    <>
      <div className="flex items-center justify-end gap-3 px-4 pt-4 text-sm text-slate-500">
        <span>{session.user.email}</span>
        <button onClick={() => void supabase.auth.signOut()} className="underline hover:text-slate-800">
          Esci
        </button>
      </div>
      <Dashboard />
    </>
  );
}
