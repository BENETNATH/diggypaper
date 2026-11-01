# Diggypaper

A **sovereign, collaborative systematic literature review platform**.
You import a corpus from PubMed, Europe PMC, OpenAlex or HAL, retrieve open full texts, ask one
or more questions to the corpus, models (running on CPU, without the cloud) triage
and extract the answers, then human reviewers validate or correct them. The results
export to spreadsheet, RO-Crate and PRISMA diagram.

> Interface in French, English, German and Spanish. GDPR *privacy by design*.

---

## Features

### Building the corpus
- **Multi-source import** : PubMed, Europe PMC, OpenAlex (metadata) or HAL
  (theses, HDR, French repositories), by query, with the expected volume before import, an
  adjustable cap (`IMPORT_MAX_ARTICLES`), deduplication — by DOI, across sources — and the
  metadata (journal, date, authors, DOI).
- **RO-Crate import** : a zip exported by Diggypaper (Projects → « Import a RO-Crate »,
  up to 1 GB) recreates a project: question and items, articles, full texts, PDF, model answers
  (certainties, evidence excerpts) and final human answers. The crate contains only one
  search: it becomes the initial search. Reviews are attributed to the person who
  imports, without a relevance note; the gold, the other searches and the team are not in
  the crate. The zip is verified (structure, sizes, paths) before any reading.
- **PubMed query assistant** : you describe your search in free text, a model (chosen
  from those in your account) proposes a query, you refine it by discussing. Each proposal is
  verified by PubMed: number of results, unknown terms (an invented MeSH is flagged) and, if
  you give expected articles (DOI / PMID), how many the query retrieves. The model is
  bounded server-side: it can only serve this purpose (out-of-query outputs refused, conversation
  and rate limited).
- **Reference list** instead of a search: DOIs or PMIDs pasted (one per line), or
  BibTeX, RIS, CSL JSON or CSV export from Zotero / EndNote / Mendeley. Each entry is
  completed via PubMed (Europe PMC to find the PMID of a DOI), otherwise Crossref, otherwise the
  file data; duplicates are ignored and unresolved references are listed.
- **Citation enrichment** : from all or part of the corpus (no prior
  analysis), adds the references and the citing articles of the starting articles (iCite, NIH). The
  candidates most related to the starting articles come in first (threshold and cap adjustable,
  estimate before import); a search is created on the new articles so the
  model validates their inclusion. The origin of each article (PubMed, list, citation) is
  kept and appears in the PRISMA («identification by other methods»).
- **Open-access full texts** : Europe PMC (full-text XML), then open PDFs from Unpaywall,
  arXiv, OpenAlex and Crossref (publisher versions, accepted manuscripts, preprints), with
  Semantic Scholar as a last resort; for HAL records the deposited main file is re-requested.
  Articles without an open copy stay abstract-only. No pirate sources.
- **Manually deposited PDF** (institutional access): the text is extracted and sectioned; you
  verify that the PDF actually matches the article (title, DOI if absent) before adopting it.
- **Batch PDF deposit** : several PDFs at once, each attached to the article whose
  title (or DOI) it carries; ambiguous cases or without a match are reported.
- **Re-search per article** : re-reads the PubMed record (a published preprint changes its
  PMCID or DOI) then looks for the full text again.

### Asking questions to the corpus: the searches
A project contains several **searches**. A search is a **question** (its answer
items: yes/no, choice, free text) asked to a **batch of articles**, with its own results,
reviews, analyses and references. The batch is the whole corpus, or a subset chosen by
criteria — for example «articles where search 1 answered yes», with human review
or full text, within a relevance range, a random draw or a list. This way
you ask a second question only to the articles retained by the first, without re-analyzing anything.
- **Successive searches** : you schedule search 2 while search 1 is running: it starts
  on its own as soon as search 1 is done, on its results (with criteria: accepted by the AI, answer to an
  item…) or on the same batch. Only one analysis runs at a time in a project, the following
  ones wait their turn. The question of an already analyzed or reviewed search can no longer be modified:
  a new search is created on the same batch.
- Choice of **text parts** read by the models (abstract, methods, results…).
- **Generative models** (local Ollama, or any OpenAI-compatible API): structured
  answers, free text and evidence citations. The *Models* page reads the resources of the
  server (RAM, cores, AVX2, GPU) and recommends a suitable model.
- Fast **pre-filter** (small model on title + abstract) before the main model, **automatic triage** (accepted, rejected,
  to re-read) according to confidence thresholds, time estimate, **resume** of an interrupted analysis
  and **stop** of a running analysis (already processed articles are kept).

### Gold standard, blind sample and benchmark
- **Gold standard** : a reference comes from a **human review**. Any member of the
  project adds a review to the gold from the article card, or all reviews at once.
  Each reference is a frozen copy, with a link to its original card, its reviewer and
  its status (validated, corrected, blind); it is flagged «obsolete» if the review has
  changed since. The gold applies to all searches with the same question.
- **Blind sample**, before the full analysis: N articles drawn at random (N adjustable)
  to review **without seeing the AI's answers**, to build an unbiased gold.
- **Educate the models** : at each analysis, checkbox «use N gold examples in the prompt»
  (these examples are excluded from the scoring) and checkbox «validate the results with the gold» (recall,
  precision, F1 of the relevance and of each item).
- **Benchmark** (tuning): same models and same articles (a sub-batch), several
  **prompt formulations** in free entry, to find the best questions and the best
  output. The variants are ranked according to a **criterion defined on an item of the question** (by
  default the recall of the relevance), with the agreement between variants, the detail per item, the
  list of articles where they diverge and, if asked, the scoring against the gold.

### Reviewing
- Review card: text with **evidence highlighting**, model answers and certainty
  per item, **validation, correction or complement** by the reviewer, with recording of the
  status (validated, corrected, entered by hand) and of the person who reviewed.
- Review queue dedicated to uncertain articles, **finger** (swipe) navigation on mobile.
- Results list with **filters on the answers** (per item), AI status, review status;
  the displayed answers are those of the review when it exists. The queue of
  review follows the filters and sort of the list.

### Exporting and documenting
- **XLSX spreadsheet** (AI answers, reviews, certainties, links), **RIS and BibTeX references**.
- **RO-Crate archive** (articles, PDF, predictions, annotations, parameters of the analysis:
  everything needed to reproduce), generated in a background task, with its license (`CRATE_LICENSE_*`).
- **PRISMA 2020 flow diagram** (SVG and PDF) computed from the project data.

### Working together
- **Accounts** : the administrator invites **creators** of projects; those invite
  colleagues, simple **guests** whose account disappears when they no longer have a project.
- **Roles per project** : project lead (LEAD) and annotator. Invitations by e-mail in the
  language of the invited person.
- **Models** : registry of models, **sharing** of a model or of a key with people
  or a project, external models that are **personal** ; keys are encrypted (AES-256-GCM). An
  account can only use the models it owns or that have been shared with it.
- **Administration** : list of accounts (modify, deactivate, delete with or without their
  projects), pending invitations, all projects, SMTP / NCBI / LLM settings.

### Sovereignty and security
- **100% CPU, self-hosted** : no data is sent to a cloud service (except
  querying the public APIs PubMed, Europe PMC, Unpaywall, OpenAlex, Crossref,
  Semantic Scholar and HAL).
- **GDPR** : consent, anonymization of the annotations on the deletion of an account.
- Session by httpOnly revocable cookie, rate limiting, control of outbound URLs,
  neutralization of formulas in the exports, scripted backup and restore.

---

## Architecture

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  Frontend (Next.js 16)      │  HTTP  │  Backend (FastAPI)           │
│  App Router + Tailwind      │ ─────► │  /api/v1/*                   │
│  next-intl (fr/en/de/es)    │ same-  │  JWT cookie · i18n · rate-   │
│  react-swipeable            │ origin │  limiting · CORS (opt)       │
└─────────────────────────────┘  proxy └──────────────┬───────────────┘
        (dev: rewrite /api/v1)                        │
                                                       ▼
                               ┌────────────────────────────────────────┐
                               │  PostgreSQL (SQLAlchemy 2.0 async)     │
                               │  Redis (Celery broker + result)        │
                               │  LLM local (Ollama / llama.cpp)        │
                               │  NCBI E-utilities (PubMed/PMC)         │
                               └────────────────────────────────────────┘
```

- **Backend** : FastAPI + Pydantic v2, PostgreSQL + SQLAlchemy 2.0 async + Alembic,
  Celery + Redis (PubMed ingestion, batch inference), FastAPI-Mail.
- **Frontend** : Next.js 16 (App Router) + TypeScript + Tailwind + next-intl +
  react-swipeable. UI components on **design tokens** (OKLCH, light/dark).
- **Models** : generative models (OpenAI-compatible endpoint `/v1/chat/completions` :
  Ollama, external API).
  SMTP / NCBI / LLM settings managed in the database (encrypted) or, failing that, by environment variables.

---

## Prerequisites

- Python ≥ 3.12
- Node.js ≥ 20 (Next.js 16)
- PostgreSQL 14+
- Redis 7+
- (Optional) Ollama or llama.cpp for inference

---

## Quick start

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure the environment
cp .env.example .env          # then edit (secrets, DATABASE_URL, REDIS_URL)

# Migrations
alembic upgrade head

# Start the API
uvicorn app.main:app --factory --reload --port 8000

# Start the Celery worker (PubMed ingestion / inference)
celery -A app.celery_app.celery_app worker --loglevel=info
```

> In dev without a broker: `CELERY_EAGER=true` runs the tasks inline.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local    # NEXT_PUBLIC_API_ORIGIN=http://localhost:8000
npm run dev                   # http://localhost:3000
```

In dev, the frontend proxies `/api/v1/*` to `NEXT_PUBLIC_API_ORIGIN` (no CORS).
In prod, nginx does the same-origin proxy.

---

## Configuration

See [`.env.example`](.env.example) (root) for the full list.

**Backend** (`backend/.env`) : `DATABASE_URL`, `JWT_SECRET`, `ANON_SALT`,
`ENCRYPTION_KEY`, `REDIS_URL`, `CELERY_EAGER`, `APP_BASE_URL`, `SMTP_*`,
`CORS_ORIGINS`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`.

> **Bootstrap of the first admin** : at startup, if `ADMIN_EMAIL`/`ADMIN_PASSWORD`
> are defined and no admin exists, the account is created (idempotent). That is the
> mechanism to start the app in an empty state — without an admin, you can neither invite
> nor configure. Then, the admin invites the other users (email → `/register?token=…`).

**Frontend** (build env) : `NEXT_PUBLIC_API_ORIGIN`, `NEXT_PUBLIC_API_BASE`.

**Managed in the database (encrypted)** : `llm_api_base_url`, `llm_api_key`,
`ncbi_api_key`, `smtp_*` — via the settings API or the Settings UI.

> **Fail-fast** : the backend refuses to start if `JWT_SECRET`, `ANON_SALT` or
> `ENCRYPTION_KEY` are still the default dev values.

---

## Production (Docker)

Base stack (nginx, frontend, backend, worker, postgres, redis) + model services
**optional** (ollama : see «Models»). nginx is the **only exposed service**, everything
else stays on the internal Docker network.

```
reverse proxy TLS (establishment)            ← HTTPS, certificate, HSTS
  └── nginx (127.0.0.1:8080)
        ├── /api/v1/*  ──►  backend (FastAPI/uvicorn, :8000)
        └── /*         ──►  frontend (Next.js, :3000)

backend ──►  postgres (16)  ·  redis (7)  ◄──  worker (Celery, general tasks)
                                                     └──  worker-ingest (imports, 1 process)
backend / worker ──►  ollama (optional)  ·  external API of your choice
```

### Quick-start

```bash
# 1. Generate the environment: random secrets (JWT, ANON, AES encryption), port, admin
python3 scripts/setup_env.py --yes   # or without --yes: interactive (recommended)
#    then edit .env if needed (APP_BASE_URL, SMTP_*, ADMIN_*, …) — chmod 600 already set

# 2. Start the stack (build + up)
make up                       # or: docker compose up -d --build

# 3. Verify
make ps                       # service status
make logs                     # live logs
```

Do not copy `.env.example` as-is: its values are those of development and the
backend **refuses to start** with them (fail-fast on `JWT_SECRET`, `ANON_SALT`,
`ENCRYPTION_KEY` — see `backend/app/core/config.py`).

The Alembic migrations are applied at backend startup; the worker waits for it to
be ready. The services restart themselves after a reboot (`restart: unless-stopped`) and
the Docker logs are limited (5 × 10 MB per service).

> **Windows** : clone with LF line endings (the repo enforces them for the `*.sh` files via
> `.gitattributes`). An `entrypoint.sh` in CRLF fails the backend with
> «exec ./entrypoint.sh: no such file or directory».

### HTTPS: reverse proxy in front of nginx

Diggypaper does not handle TLS itself: place it behind your establishment's reverse proxy
(Apache, Traefik, Caddy, nginx…) which holds the certificate. Settings:

| Variable (`.env`) | Value |
|-------------------|--------|
| `APP_BASE_URL` | public **https** URL (`https://diggypaper.exemple.fr`) : links of the e-mails |
| `COOKIE_SECURE` | `true` (default) — the session cookie is sent over HTTPS only. `false` only for a local HTTP trial |
| `NGINX_BIND` | `127.0.0.1` (default) if the proxy runs on the **same machine** ; `0.0.0.0` (or the IP of an interface) if it is on another machine — filter the port by firewall then |
| `NGINX_PORT` | local port of nginx (default `8080`) |
| `TRUSTED_PROXY_CIDR` | address or network of the proxy, as seen from nginx (e.g. `172.16.0.0/12` if the proxy is on the same machine, via the Docker gateway). **Required** : without it, all requests appear to come from the proxy and the per-IP rate limiting is applied to everyone at once |

The proxy must forward `Host`, `X-Forwarded-For` and `X-Forwarded-Proto`, and set
`Strict-Transport-Security`. nginx (internal) adds the headers `X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` and masks its version;
it **never trusts** an `X-Forwarded-For` coming from a client (it replaces it with the real IP).
Example block for a front nginx:

```nginx
server {
    listen 443 ssl;
    server_name diggypaper.exemple.fr;
    # ssl_certificate … ; ssl_certificate_key …
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    client_max_body_size 30m;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
    }
}
```

### Updating an existing installation

After `git pull`, before `docker compose up -d --build` :

1. **Model services.** Laya and Kev (decision models) are removed: unreliable in
   zero-shot on literature reviews. The corresponding models are disabled in the database
   (predictions already produced are kept). Remove `laya` from `COMPOSE_PROFILES`,
   `LAYA_URL` and `KEV_URL` from your `.env` (`docker compose up -d --remove-orphans` stops their
   container). Ollama remains an optional profile:
    ```bash
    COMPOSE_PROFILES=ollama
    ```
    Without a local model (external API only), put nothing: no model service starts.
2. **Cookies.** `COOKIE_SECURE` is `true` by default: behind HTTPS nothing to do; for plain
   HTTP access, set `COOKIE_SECURE=false`.
3. **Port.** nginx now listens on `127.0.0.1` only; if the reverse proxy is on another machine,
   `NGINX_BIND=0.0.0.0`.
4. **Back up** (`scripts/backup.sh`): the migrations add the searches (each project
   receives an «Initial Search» that takes over its results, reviews and references).
5. The backend and worker containers now run without root; their volumes are repaired at
   startup.

### Accounts

- **Admin** : created at first startup (`ADMIN_EMAIL`/`ADMIN_PASSWORD`). The admin invites
  **creators** (page *Administration → Users*) : they can create projects and
  invite colleagues in them.
- **Guests** (invited to a project by a project creator): cannot create a project;
  their account is deleted automatically when they no longer have any project. A
  creator or an admin is never deleted automatically.

### Capacity and limits

| Variable | Role |
|----------|------|
| `WORKER_CONCURRENCY` | background tasks in parallel (analyses, exports, benchmarks) — default `3` ; imports run on `worker-ingest` (1 process, queue `ingest`) to stay under the NCBI cap |
| `IMPORT_MAX_ARTICLES` | cap on articles per import — default `5000` ; `0` = unlimited (beyond 10,000 results, the query is split by publication date, the most recent first) |
| `TASK_SOFT_TIME_LIMIT` | max duration of a task in seconds — `0` = none |
| `*_MEM_LIMIT` | `OLLAMA_`, `WORKER_`, `INGEST_`, `BACKEND_`, `FRONTEND_` : memory caps (defaults 24g, 8g, 2g, 2g, 1g) |
| `DISK_ALERT_PERCENT` / `DISK_ALERT_FREE_GB` | disk alert (e-mail to the admins, banner) — defaults `85`% / `5` GB |
| `PROJECT_RETENTION_DAYS` | days of inactivity before warning the leads of a project — `0` (default) = never automatic deletion |
| `PROJECT_RETENTION_GRACE_DAYS` | grace period before deletion, after the warning — default `14` |
| `PRIVACY_URL` | «privacy policy» link displayed at first login |

Article PDFs are stored in PostgreSQL: plan for space (up to 25 MB per
file). The *Settings* page (admin) shows the disk space, the weight of the PDFs and the projects
(size, inactivity); the worker (celery beat, every 6 h) sends an alert to the admins when
the disk exceeds the threshold. With `PROJECT_RETENTION_DAYS`, the leads of an inactive project
are warned by e-mail, then the project is deleted at the end of the grace period unless activity or
a click on «Keep this project». Only project leads can start an import, an RO-Crate export or replace the
full text of an article (spreadsheet, references and PRISMA exports are open to any member).

### Backup and restore

Everything that matters is in the PostgreSQL database (accounts, projects, articles, PDF,
annotations, models and encrypted keys).

```bash
scripts/backup.sh                              # a compressed pg_dump in ./backups (14 days kept)
scripts/restore.sh backups/diggypaper-….sql.gz # REPLACES the current database (confirmation required)
TARGET_DB=test scripts/restore.sh <file>      # verify a backup in a separate database
```

Scheduling: `0 2 * * * cd /path/to/diggypaper && scripts/backup.sh >> backups/backup.log 2>&1`.
**Copy `./backups` and your `.env` off the machine** : without `ENCRYPTION_KEY`, the
encrypted API keys of the database are unreadable. The RO-Crate export serves reproducibility,
not backup (it includes neither accounts, nor models, nor shares; it can be
re-imported, but the team and the gold stay behind). `docker compose down -v` **erases** the volumes.

### Outbound network

External models entered by a user and open PDF downloads
are refused towards internal addresses (checked at each call). The
DNS rewrite window between verification and connection is not closed: for an
instance exposed to untrusted users, also filter the outbound of the
`backend`/`worker` containers to your internal network (firewall / egress proxy).

### Models: what works, and what we deploy

The triage uses a **generative model** that answers in JSON (decision, items, citations). Two
ways, to choose in `.env` then `docker compose up -d --build` :

```bash
COMPOSE_PROFILES=ollama          # local models (otherwise: no model service)
# LLM_API_BASE_URL=http://ollama:11434/v1   # default of the compose
```

| Way | Deployment | Usage |
|------|-------------|-------|
| **Ollama** (Qwen, Gemma, Llama… in GGUF) | profile `ollama` | local and sovereign; speed depending on CPU/GPU |
| **External API** (OpenAI, Mistral, internal server…) | nothing: URL + key in *Models* | fast, no hardware; the articles leave the server |

- The *Models* page detects the resources of the server (cores, RAM, AVX2, GPU) and recommends a
  suitable model, installable in one click: less than 16 GB of RAM → very small model (or
  external API); 16–32 GB → ~4B; 32 GB and more → «mixture of experts» 30B (3B active); GPU → ~12B.
  The recommended models are *instruct* type (without «reflection» mode, which multiplies the
  time per article). Without AVX2 nor GPU, local inference is 2 to 3 times slower. The times
  displayed are orders of magnitude: always measure the precision on your gold (*Benchmarks*).
- Each account can register its own external models (encrypted key) and share them with
  accounts or projects; only the models an account owns or that have been shared with it
  are usable. A shared model spends the credits of its owner.
- Decision models without chat (Laya, Kev…) are no longer supported: the
  Hugging Face search flags them and refuses to install them.

**GPU on Windows (Docker Desktop)** : no passthrough of Intel iGPU in
Docker Desktop (NVIDIA only) — a containerized Ollama stays on CPU,
no flag changes that. To exploit the Arc 130T: Ollama **native
Windows** with `OLLAMA_IGPU_ENABLE=1`, `OLLAMA_VULKAN=1`, `OLLAMA_NUM_GPU=999`,
then `LLM_API_BASE_URL=http://host.docker.internal:11434/v1` in the `.env`
(the compose already supports the override).

### Makefile targets

| Target | Action |
|-------|--------|
| `make up` | Build + start of the stack |
| `make down` | Stop of the stack |
| `make build` | Build the images |
| `make logs` | Live logs |
| `make ps` | Service status |

---

## API (excerpts)

All routes are under `/api/v1`. Auth by JWT cookie (httpOnly, Secure by default,
SameSite=Strict, revoked on password change and on logout). Roles: `LEAD` / `ANNOTATOR` / `ADMIN`.

| Method | Route | Description |
|---------|-------|-------------|
| POST | `/auth/register` | Registration (invitation token) |
| POST | `/auth/login` | Login (JWT cookie) |
| GET | `/projects` | User's projects (+role, +article_count) |
| POST | `/projects` | Create a project |
| POST | `/projects/{id}/import` | Start the PubMed import |
| GET | `/projects/{id}/articles` | List of articles |
| GET | `/projects/{id}/articles/{aid}` | Article + methods text |
| POST | `/projects/{id}/articles/{aid}/annotation` | Submit an annotation |
| GET/POST | `/projects/{id}/searches` | Searches (question + batch of articles) ; `?search=<id>` on the results routes |
| POST | `/projects/{id}/searches/preview` | Number of articles of a selection |
| GET/POST | `/projects/{id}/gold` · `/gold/from-reviews` | References (gold) and addition from the reviews |
| GET/POST | `/projects/{id}/prompts` | Versioned prompts |
| GET/POST | `/projects/{id}/benchmarks` | Benchmarks (metrics) |
| GET/POST | `/projects/{id}/inference` | Inference (job) |
| GET/PUT | `/admin/settings` | Settings by scope (secrets masked) |
| POST | `/admin/settings/smtp/test` | Test email send |
| DELETE | `/users/me/anonymize` | GDPR anonymization |

---

## Tests

```bash
# Backend (100% offline: SQLite in-memory, SMTP/NCBI/LLM mocked)
cd backend && source .venv/bin/activate
pytest -q
ruff check . && ruff format .

# Continuous integration: .github/workflows/ci.yml (ruff, pytest, tsc, lint, jest, build)
# Python dependencies frozen in backend/constraints.txt (used by the image and the CI).

# Frontend
cd frontend
npx jest          # lib + component tests (search, benchmark, highlight, source, ModelRef…)
npm run build     # compile + type-check
npm run lint
```

---

## Structure

```
diggypaper/
├── .env.example              # env variables (backend + frontend)
├── backend/
│   ├── app/
│   │   ├── api/v1/           # routes (auth, projects, prompts, gold, …)
│   │   ├── core/             # config, crypto (AES-256-GCM), deps
│   │   ├── db/               # SQLAlchemy models + session
│   │   ├── pubmed/           # NCBI E-utilities client (throttle, clean errors)
│   │   ├── inference/        # OpenAI-compatible LLM service
│   │   ├── settings/         # settings and encrypted secrets
│   │   ├── tasks/            # Celery tasks (import, benchmark, inference)
│   │   ├── emails/           # FastAPI-Mail (DB > env)
│   │   └── main.py           # app factory (middleware + routers)
│   ├── alembic/              # migrations (0001 → 0033)
│   └── tests/                # 54 suites / 789 tests, offline
└── frontend/
    ├── src/app/              # routes (login, register, projects/[id]/*)
    ├── src/components/ui/    # design system (Button, Field, Card, Feedback)
    ├── src/lib/              # api.ts, auth.tsx, highlight.ts, swipe.ts
    ├── src/messages/         # i18n (fr, en, de, es)
    └── tailwind.config.ts    # semantic tokens
```

---

## Design system & UI quality

The interface is built according to production rules (not «vibe coding»):

- **Design tokens** (3 levels: OKLCH primitives → semantic → components).
  Never a raw hex value in a component. Light + dark, colors with hue
  (no pure gray/black).
- **Performance** (Vercel rules): parallel fetch (`Promise.all`), polling
  conditional (only while a job is active), loading skeletons,
  controlled re-renders.
- **WCAG 2.2 accessibility**: native labels, `role="alert"`/`status`, visible focus
  (≥ 2 px, contrast ≥ 3:1), errors identified by text + icon (not color
  alone), targets ≥ 32 px, `prefers-reduced-motion` respected.
- **Typography**: Geist (next/font), 8 px spacing scale, motion
  `cubic-bezier(0.16,1,0.3,1)` 120–180 ms.

---

## License

MIT — see [LICENSE](LICENSE).
