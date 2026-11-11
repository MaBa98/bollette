# Energy SaaS: tracciamento consumi e comparazione tariffe

Monorepo multi-tenant: FastAPI asincrono + PostgreSQL (Supabase) + React/TS.
Stato: scheletro production-ready verificato (test dei template verdi, pipeline PDF→bolletta
eseguita end-to-end su un PDF generato, DDL compilato su dialetto Postgres, `tsc --strict` pulito).
Lo scraping è un mock, per scelta.

---

## 0. Scratchpad: contratti delle interfacce

```
DocumentInput(content: bytes, filename, declared_mime)      # solo RAM, mai un path
   │   .sniffed_mime  ← magic bytes, il Content-Type del client non è affidabile
   ▼
DocumentParser  (STRATEGY: bytes → testo)
   can_handle(doc) -> bool        economico, senza side effect
   extract(doc)    -> ExtractedText(pages, source_kind, ocr_confidence?)
   priority: int                  NativePDF=10 · OCR=50 (futuro)
   ├─ NativePDFParser             PyMuPDF sonda il layer di testo, pdfplumber estrae
   └─ OCRImageParser              FUTURE-PROOF, contratto già fissato, non registrato
ParserResolver.resolve(doc)       prima strategia che accetta, altrimenti 415
   ▼
BaseProviderTemplate  (TEMPLATE METHOD: testo → ParsedInvoice)
   parse()  @final  = normalize → detect_commodity → extract_period → extract_total
                      → extract_consumption → extract_supply_point → score_confidence → post_process
   sottoclassi ridefiniscono SOLO pattern regex e hook
   ├─ EnelTemplate · PlenitudeTemplate · GenericItalianTemplate (fallback, confidence penalizzata)
TemplateRegistry.detect(text)
   ▼
InvoiceExtractionPipeline.run(doc) -> ParsedInvoice     facade, sync/CPU-bound → threadpool
```

Perché due pattern distinti: la *forma* del documento (PDF nativo, scansione, foto) e il
*fornitore* sono assi indipendenti. Con l'OCR, ogni template esistente funziona senza modifiche
perché riceve sempre `ExtractedText`.

Multi-tenancy: `user_id` esce **solo** dal claim `sub` del JWT (`core/security.py`) ed entra
**solo** nel costruttore dei repository (`db/repositories.py`). Nessun metodo accetta un
`user_id` come argomento, quindi non si può dimenticare il filtro. RLS su Supabase come seconda linea.

---

## 1. Directory structure

```
energy-saas/
├── render.yaml                         # Blueprint Render (backend)
├── backend/
│   ├── Dockerfile · requirements.txt · alembic.ini · .env.example
│   ├── migrations/
│   │   ├── env.py                      # Alembic async
│   │   ├── versions/                   # generare con: alembic revision --autogenerate
│   │   └── rls.sql                     # Row Level Security Supabase
│   ├── app/
│   │   ├── main.py                     # app, CORS, lifespan (recupero job orfani), /health
│   │   ├── core/   config.py · security.py (JWT Supabase: JWKS o HS256 legacy)
│   │   ├── db/     models.py · session.py · repositories.py (tenant-scoped)
│   │   ├── api/    schemas.py · v1/invoices.py · v1/comparisons.py
│   │   ├── services/ comparison.py (BackgroundTask) · offer_sources.py (mock + Protocol)
│   │   └── parsing/
│   │       ├── contracts.py            # DTO, enum, errori
│   │       ├── pipeline.py             # facade
│   │       ├── strategies/ base.py · native_pdf.py · ocr_image.py · resolver.py
│   │       └── templates/  base.py · providers.py · registry.py
│   └── tests/test_templates.py
└── frontend/
    ├── package.json · tsconfig.json · vercel.json · .env.example
    └── src/
        ├── types/energy.ts             # specchio degli schema Pydantic
        ├── api/client.ts               # fetch + JWT Supabase, upload a body grezzo
        ├── hooks/useComparisonTask.ts  # avvio job + polling con backoff
        └── components/Dashboard.tsx    # grafico Recharts + trigger comparazione
```

## 2. Database schema — `backend/app/db/models.py`

| Tabella | Tenant | Note |
|---|---|---|
| `users` | è il tenant | `id` = `auth.users.id` di Supabase (claim `sub`) |
| `invoices` | `user_id` FK CASCADE | unique `(user_id, file_sha256)` → niente duplicati; check periodo; il PDF non è conservato |
| `comparison_tasks` | `user_id` FK CASCADE | stato persistente dei job in background |
| `market_offers` | **nessuno, voluto** | catalogo pubblico condiviso; unique `(offer_code, valid_from)` per upsert |

Importi in `Numeric`, mai `float`. I consumi stanno nella bolletta: una tabella `consumptions`
separata servirà solo con dati orari/quartorari (es. da Portale Consumi o smart meter).

## 3. Core backend

**Strategy + Template**: `app/parsing/` (vedi scratchpad). I commenti `FUTURE-PROOF` segnano
i 6 punti in cui si aggancia l'OCR: enum `SourceKind`, `ExtractedText.ocr_confidence`,
`NativePDFParser._has_text_layer`, `ocr_image.py`, `resolver.py`, `score_confidence`,
più `ACCEPTED_CONTENT_TYPES` nell'endpoint e l'`accept` nel frontend.

**Upload senza disco**: `app/api/v1/invoices.py`. Scelta non ovvia: niente `UploadFile`.
Starlette riversa su `/tmp` i file multipart oltre 1 MB (SpooledTemporaryFile), il che viola il
vincolo. Il client invia il PDF come body grezzo; il server legge lo stream a chunk con limite di
dimensione applicato durante la lettura, calcola lo SHA-256 per la deduplica e passa i byte alla
pipeline in un threadpool (il parsing è CPU-bound e bloccherebbe l'event loop).

**Scraping non bloccante**: `app/api/v1/comparisons.py` + `app/services/comparison.py`.
`POST /comparisons` → riga `pending` + commit → `202 {id}` → il job gira dopo la risposta con una
sessione DB propria. Idempotente (un job attivo per utente/commodity). Refresh del catalogo con TTL
e `pg_try_advisory_xact_lock`, così 100 utenti non lanciano 100 scraping.

Limite onesto di `BackgroundTasks`: vive nel processo. Se Render riavvia o addormenta il container
a metà job, il job muore. Mitigazione inclusa: `recover_stale_tasks()` all'avvio marca i job orfani
come falliti, così la UI non resta appesa. Upgrade naturale quando servirà: un worker separato su
`SELECT … FOR UPDATE SKIP LOCKED`, oppure ARQ + Redis.

## 4. Core frontend — `frontend/src/components/Dashboard.tsx`

- Grafico **normalizzato per giorno** (kWh/giorno): bollette mensili e bimestrali sono
  altrimenti incomparabili. Seconda serie: costo effettivo €/kWh tutto incluso.
- Trigger comparazione → hook di polling con backoff 1,5 s→8 s e timeout 3 min. Polling e non
  SSE/WebSocket perché sopravvive ai cold start del free tier.

## 5. Deployment guide

### Verdetto prima delle istruzioni

"Zero costi e H24" regge per un **MVP/beta privata**, non per un SaaS B2C a pagamento:

1. **Vercel Hobby vieta l'uso commerciale.** Le fair use guidelines lo limitano a uso personale non
   commerciale; ogni uso commerciale richiede Pro o Enterprise. Alternativa gratuita senza questo
   vincolo: **Render Static Site** (gratis e senza spin-down).
2. **Fly.io non è più gratis.** Dall'ottobre 2024 le nuove organizzazioni non hanno free tier,
   solo una prova di 2 ore macchina o 7 giorni. Per questo la guida usa Render.
3. **Render Free va in sleep** dopo 15 minuti senza traffico in ingresso, con circa un minuto di
   risveglio, e può essere riavviato in qualsiasi momento. Le 750 ore mensili coprono un servizio
   sempre acceso (~730 h), quindi il keep-alive è sostenibile, ma i riavvii restano.
4. **Supabase Free si mette in pausa** se il DB riceve poca attività in una settimana. Qualche
   richiesta al giorno di solito basta, senza garanzie. Il ping a `/health` esegue apposta una
   query su una tabella reale.
5. **Non usare Render Postgres Free**: scade dopo 30 giorni.

Percorso paid minimo quando arrivano utenti paganti: istanza Render a pagamento (da ~7 $/mese,
niente spin-down), Supabase Pro (niente pausa, backup), frontend su Vercel Pro o Render Static.

### Passo 1: Supabase (DB + Auth)

1. Crea il progetto, regione **Frankfurt (eu-central-1)** (dati personali → restare in UE).
2. *Authentication → Providers*: abilita Email (e Google se vuoi).
3. *Project Settings → Database → Connection string → Session pooler*: copia l'URI così com'è
   (il backend converte da solo `postgresql://` in `postgresql+asyncpg://`). Il Session pooler è IPv4 e non richiede di
   disattivare i prepared statement. Se usi il Transaction pooler (porta 6543) imposta
   `DB_BEHIND_PGBOUNCER=true`.
4. Annota `SUPABASE_URL` e la anon/publishable key (frontend). Se il progetto usa ancora il
   JWT secret legacy HS256, annotalo in `JWT_SECRET`; altrimenti lascialo vuoto (verifica via JWKS).

### Passo 2: schema (automatico)

Non serve fare nulla: all'avvio il container esegue `alembic upgrade head`, che crea le tabelle
e, se rileva Supabase, attiva anche la Row Level Security (`migrations/versions/0001_initial_schema.py`).
`migrations/rls.sql` resta solo come riferimento leggibile delle stesse policy.

Per lo sviluppo locale: `pip install -r requirements.txt && cp .env.example .env && pytest -q`.

### Passo 3: backend su Render

1. Push del monorepo su GitHub.
2. Render → *New → Blueprint* → seleziona il repo: legge `render.yaml` (Docker, piano free, Frankfurt).
3. Compila le env var con `sync: false`: `DATABASE_URL`, `SUPABASE_URL`, `JWT_SECRET` (o vuoto),
   `CORS_ORIGINS` (uno o più URL separati da virgola, es. `https://tuo-progetto.vercel.app`; va bene anche il formato JSON).
4. Il container esegue `alembic upgrade head` all'avvio, poi uvicorn con 1 worker (512 MB di RAM).
5. Verifica: `curl https://<servizio>.onrender.com/health` → `{"status":"ok"}`.

### Passo 4: keep-alive (Render + Supabase)

Un monitor esterno gratuito (UptimeRobot, cron-job.org o un workflow GitHub Actions con
`schedule`) chiama `GET /health` ogni **10 minuti**. Effetti: Render non scende mai sotto la
soglia dei 15 minuti, e ogni ping è anche una query reale su Supabase.

### Passo 5: frontend

**Vercel** (solo se il progetto resta non commerciale): *Add New → Project* → repo → Root Directory
`frontend` → preset Vite → env `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` → Deploy.
`vercel.json` gestisce il fallback SPA.

**Render Static Site** (alternativa commerciale gratuita): root `frontend`, build
`npm ci && npm run build`, publish dir `dist`, rewrite `/* → /index.html`.

Poi su Supabase, *Authentication → URL Configuration*: aggiungi il dominio del frontend ai
redirect consentiti, e aggiornalo anche in `CORS_ORIGINS` sul backend.

### Passo 5b: offerte reali (job giornaliero)

Il catalogo offerte arriva dagli open data del Portale Offerte, importati ogni giorno dal workflow
`.github/workflows/ingest-offers.yml` (`python -m app.market.ingest`).

1. GitHub → repo → *Settings → Secrets and variables → Actions → New repository secret*:
   nome `DATABASE_URL`, valore = la stessa stringa del Session pooler usata su Render.
2. *Actions → Importa offerte Portale Offerte → Run workflow* per la prima importazione.
3. Poi gira da solo due volte al giorno. Nota: GitHub sospende i workflow schedulati dopo 60
   giorni senza commit sul repo; in quel caso basta riattivarlo dalla scheda Actions.

### Passo 6: prima di aprire agli utenti

- **Template su bollette vere.** Le regex di Enel/Plenitude sono plausibili ma non calibrate:
  servono 5-10 PDF reali anonimizzati per fornitore in `tests/fixtures/` prima del lancio.
  Un layout cambiato in silenzio produce numeri sbagliati, non errori.
- **Fonte offerte.** Prima di scrivere Playwright: il Portale Offerte nasce per legge come
  piattaforma di pubblicazione open data e i venditori caricano le offerte in XML. Scaricare i
  dataset è più stabile e leggero di uno scraper headless (Chromium su 512 MB va in OOM).
  In alternativa, lo scraping può girare in un workflow GitHub Actions schedulato che scrive in
  `market_offers`, fuori dal backend.
- **Modello di costo.** La stima confronta solo la quota venditore (energia + quota fissa); per le
  offerte variabili serve un indice forward (PUN/PSV), come fa il Portale stesso.
- **GDPR.** Una bolletta contiene nome, indirizzo e POD/PDR: informativa privacy, DPA con
  Supabase e Render, regioni UE, endpoint di cancellazione account (le FK CASCADE sono già pronte).

### Fonti
- Render, Deploy for Free: https://render.com/docs/free
- Fly.io pricing dal 2024: https://temps.sh/blog/fly-io-pricing-2026
- Supabase, Project Pausing: https://supabase.com/docs/guides/platform/free-plan-pausing
- Vercel, Hobby Plan e fair use: https://vercel.com/docs/plans/hobby
- ARERA, Portale Offerte (delibera 51/18, allegato A): https://www.arera.it/allegati/docs/18/051-18all.pdf
