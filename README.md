# Diggypaper

SaaS **souverain** de revue systématique de littérature (PubMed/PMC), **100 % CPU**.
L'utilisateur importe une requête PubMed, définit un *gold standard*, versionne des
prompts, les évalue (benchmark), lance l'inférence sur les articles, puis les
annoter — le tout sans dépendre d'un LLM cloud.

> FR-first, i18n `fr / en / de / es`. RGPD *privacy by design*.

---

## Modules

| # | Module | Statut |
|---|--------|--------|
| 1 | Auth (JWT cookie httpOnly), invitation, consentement RGPD, anonymisation | ✅ livré |
| 2 | Import PubMed (esearch/esummary), freeze du projet, dédoublonnage | ✅ livré |
| 3 | Gold standard, prompts versionnés, benchmark (précision/rappel/F1) | ✅ livré |
| 4 | Inférence (LLM OpenAI-compatible), triage auto (accept/reject/review) | ✅ livré |
| 5 | Viewer d'annotation (surlignage de preuve, swipe, formulaire) | ✅ livré |
| 6 | Export RO-Crate + diagramme PRISMA 2020 (SVG/PDF) | ⏳ à faire |
| 7 | System Settings & API keys (secrets chiffrés AES-256-GCM) | ✅ livré |

---

## Architecture

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  Frontend (Next.js 14)      │  HTTP  │  Backend (FastAPI)           │
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
  Celery + Redis (ingestion PubMed, inférence batch), FastAPI-Mail.
- **Frontend** : Next.js 14 (App Router) + TypeScript + Tailwind + next-intl +
  react-swipeable. Composants UI sur **design tokens** (OKLCH, light/dark).
- **LLM** : endpoint OpenAI-compatible (`/v1/chat/completions`) — Ollama ou
  llama.cpp, GGUF Q4_K_M CPU. Config gérée en base (Module 7), pas en env.

---

## Prérequis

- Python ≥ 3.12
- Node.js ≥ 20 (Next.js 14)
- PostgreSQL 14+
- Redis 7+
- (Optionnel) Ollama ou llama.cpp pour l'inférence

---

## Démarrage rapide

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configurer l'environnement
cp .env.example .env          # puis éditer (secrets, DATABASE_URL, REDIS_URL)

# Migrations
alembic upgrade head

# Lancer l'API
uvicorn app.main:app --factory --reload --port 8000

# Lancer le worker Celery (ingestion PubMed / inférence)
celery -A app.celery_app.celery_app worker --loglevel=info
```

> En dev sans broker : `CELERY_EAGER=true` exécute les tâches inline.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local    # NEXT_PUBLIC_API_ORIGIN=http://localhost:8000
npm run dev                   # http://localhost:3000
```

En dev, le frontend proxy `/api/v1/*` vers `NEXT_PUBLIC_API_ORIGIN` (pas de CORS).
En prod, c'est nginx qui fait le proxy same-origin.

---

## Configuration

Voir [`.env.example`](.env.example) (racine) pour la liste complète.

**Backend** (`backend/.env`) : `DATABASE_URL`, `JWT_SECRET`, `ANON_SALT`,
`ENCRYPTION_KEY`, `REDIS_URL`, `CELERY_EAGER`, `APP_BASE_URL`, `SMTP_*`,
`CORS_ORIGINS`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`.

> **Bootstrap du premier admin** : au démarrage, si `ADMIN_EMAIL`/`ADMIN_PASSWORD`
> sont définis et qu'aucun admin n'existe, le compte est créé (idempotent). C'est le
> mécanisme pour démarrer l'app à l'état vide — sans admin, on ne peut ni inviter
> ni configurer. Ensuite, l'admin invite les autres utilisateurs (email → `/register?token=…`).

**Frontend** (env de build) : `NEXT_PUBLIC_API_ORIGIN`, `NEXT_PUBLIC_API_BASE`.

**Géré en base (Module 7, chiffré)** : `llm_api_base_url`, `llm_api_key`,
`ncbi_api_key`, `zotero_*`, `smtp_*` — via l'API settings ou l'UI Paramètres.

> **Fail-fast** : le backend refuse de démarrer si `JWT_SECRET`, `ANON_SALT` ou
> `ENCRYPTION_KEY` sont encore les valeurs de dev par défaut.

---

## Production (Docker)

Stack complète (6 services) derrière nginx :

```
nginx (:8080)
  ├── /api/v1/*  ──►  backend (FastAPI/uvicorn, :8000)
  └── /*         ──►  frontend (Next.js, :3000)

backend ──►  postgres (16)
   │
   └──►  redis (7)  ◄──  worker (Celery)
```

### Quick-start

```bash
# 1. Configurer l'environnement
cp .env.example .env          # puis éditer (secrets, JWT_SECRET_KEY, SMTP_*, …)

# 2. Démarrer la stack (build + up)
make up                       # ou : docker compose up -d --build

# 3. Vérifier
make ps                       # état des services
make logs                     # logs en temps réel
```

L'application est accessible sur `http://localhost:8080` (port configurable via `NGINX_PORT`).

- Les migrations Alembic sont appliquées automatiquement au démarrage du backend.
- Le worker Celery démarre en parallèle (ingestion PubMed / inférence).
- nginx pose `X-Forwarded-For` / `X-Forwarded-Proto` (le rate limiting s'y fie).
- Le LLM local (Ollama/llama.cpp) tourne sur le même host, reachable par le backend.

### Cibles Makefile

| Cible | Action |
|-------|--------|
| `make up` | Build + démarrage de la stack |
| `make down` | Arrêt de la stack |
| `make build` | Builder les images |
| `make logs` | Logs en temps réel |
| `make ps` | État des services |

---

## API (extraits)

Toutes les routes sont sous `/api/v1`. Auth par cookie JWT (httpOnly, Secure,
SameSite=Strict). Rôles : `LEAD` / `ANNOTATOR` / `ADMIN`.

| Méthode | Route | Description |
|---------|-------|-------------|
| POST | `/auth/register` | Inscription (token d'invitation) |
| POST | `/auth/login` | Connexion (cookie JWT) |
| GET | `/projects` | Projets de l'utilisateur (+role, +article_count) |
| POST | `/projects` | Créer un projet |
| POST | `/projects/{id}/import` | Lancer l'import PubMed |
| GET | `/projects/{id}/articles` | Liste des articles |
| GET | `/projects/{id}/articles/{aid}` | Article + texte méthodes |
| POST | `/projects/{id}/articles/{aid}/annotation` | Soumettre une annotation |
| GET/POST | `/projects/{id}/prompts` | Prompts versionnés |
| GET/POST | `/projects/{id}/benchmarks` | Benchmarks (métriques) |
| GET/POST | `/projects/{id}/inference` | Inférence (job) |
| GET/PUT | `/admin/settings` | Settings par scope (secrets masqués) |
| POST | `/admin/settings/smtp/test` | Test d'envoi email |
| DELETE | `/users/me/anonymize` | Anonymisation RGPD |

---

## Tests

```bash
# Backend (100 % offline : SQLite in-memory, SMTP/NCBI/LLM mockés)
cd backend && source .venv/bin/activate
pytest -q
ruff check . && ruff format .

# Frontend
cd frontend
npx jest          # tests des libs pures (highlight, swipe)
npm run build     # compile + type-check + lint
npm run lint
```

---

## Structure

```
diggypaper/
├── .env.example              # variables d'env (backend + frontend)
├── backend/
│   ├── app/
│   │   ├── api/v1/           # routes (auth, projects, prompts, gold, …)
│   │   ├── core/             # config, crypto (AES-256-GCM), deps
│   │   ├── db/               # modèles SQLAlchemy + session
│   │   ├── pubmed/           # client NCBI E-utilities (throttle, erreurs propres)
│   │   ├── inference/        # service LLM OpenAI-compatible
│   │   ├── settings/         # Module 7 (clés, service chiffré)
│   │   ├── tasks/            # tâches Celery (import, benchmark, inférence)
│   │   ├── emails/           # FastAPI-Mail (DB > env)
│   │   └── main.py           # app factory (middleware + routers)
│   ├── alembic/              # migrations (0001 → 0004)
│   └── tests/                # 22 suites, offline
└── frontend/
    ├── src/app/              # routes (login, register, projects/[id]/*)
    ├── src/components/ui/    # design system (Button, Field, Card, Feedback)
    ├── src/lib/              # api.ts, auth.tsx, highlight.ts, swipe.ts
    ├── src/messages/         # i18n (fr, en, de, es)
    └── tailwind.config.ts    # tokens sémantiques
```

---

## Design system & qualité UI

L'interface est construite selon des règles de production (pas du « vibe coding ») :

- **Design tokens** (3 niveaux : primitives OKLCH → sémantiques → composants).
  Jamais de valeur hex brute dans un composant. Light + dark, couleurs avec teinte
  (pas de gris/noir pur).
- **Performance** (règles Vercel) : fetch parallèle (`Promise.all`), polling
  conditionnel (uniquement pendant qu'un job est actif), skeletons de chargement,
  re-renders maîtrisés.
- **Accessibilité WCAG 2.2** : labels natifs, `role="alert"`/`status`, focus visible
  (≥ 2 px, contraste ≥ 3:1), erreurs identifiées par texte + icône (pas couleur
  seule), cibles ≥ 32 px, `prefers-reduced-motion` respecté.
- **Typographie** : Geist (next/font), échelle de spacing 8 px, motion
  `cubic-bezier(0.16,1,0.3,1)` 120–180 ms.

---

## Licence

MIT — voir [LICENSE](LICENSE).
