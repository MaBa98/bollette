-- Difesa in profondità per Supabase.
-- Il backend si connette con un ruolo che bypassa RLS (owner/service), quindi il filtro per
-- tenant lo applicano i repository. Queste policy proteggono il caso in cui qualcuno interroghi
-- le tabelle direttamente via PostgREST con la anon key esposta nel frontend.

ALTER TABLE public.users            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoices         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.comparison_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_offers    ENABLE ROW LEVEL SECURITY;

CREATE POLICY users_self ON public.users
  FOR SELECT USING (id = auth.uid());

CREATE POLICY invoices_owner ON public.invoices
  FOR SELECT USING (user_id = auth.uid());

CREATE POLICY tasks_owner ON public.comparison_tasks
  FOR SELECT USING (user_id = auth.uid());

-- Catalogo pubblico: lettura per utenti autenticati, scrittura solo dal backend.
CREATE POLICY offers_read ON public.market_offers
  FOR SELECT TO authenticated USING (true);

-- Nessuna policy INSERT/UPDATE/DELETE: dal client tutte le scritture sono negate.
