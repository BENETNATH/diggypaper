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

Stack complète (8 services) : nginx est le **seul service exposé**, tout le reste reste
sur le réseau interne de Docker.

```
reverse proxy TLS (établissement)            ← HTTPS, certificat, HSTS
  └── nginx (127.0.0.1:8080)
        ├── /api/v1/*  ──►  backend (FastAPI/uvicorn, :8000)
        └── /*         ──►  frontend (Next.js, :3000)

backend ──►  postgres (16)  ·  redis (7)  ◄──  worker (Celery)
backend / worker ──►  ollama (génératif)  ·  laya (décision)
```

### Quick-start

```bash
# 1. Configurer l'environnement
cp .env.example .env          # puis éditer (secrets, APP_BASE_URL, SMTP_*, ADMIN_*, …)

# 2. Démarrer la stack (build + up)
make up                       # ou : docker compose up -d --build

# 3. Vérifier
make ps                       # état des services
make logs                     # logs en temps réel
```

Les migrations Alembic sont appliquées au démarrage du backend ; le worker attend qu'il
soit prêt. Les services redémarrent seuls après un reboot (`restart: unless-stopped`) et
les logs Docker sont limités (5 × 10 Mo par service).

> **Windows** : cloner avec les fins de ligne LF (le dépôt les impose pour les `*.sh` via
> `.gitattributes`). Un `entrypoint.sh` en CRLF fait échouer le backend avec
> « exec ./entrypoint.sh: no such file or directory ».

### HTTPS : reverse proxy devant nginx

Diggypaper ne gère pas TLS lui-même : placez-le derrière le reverse proxy de votre
établissement (Apache, Traefik, Caddy, nginx…) qui porte le certificat. Réglages :

| Variable (`.env`) | Valeur |
|-------------------|--------|
| `APP_BASE_URL` | URL publique **https** (`https://diggypaper.exemple.fr`) : liens des e-mails |
| `COOKIE_SECURE` | `true` (défaut) — le cookie de session n'est envoyé qu'en HTTPS. `false` seulement pour un essai en HTTP local |
| `NGINX_BIND` | `127.0.0.1` (défaut) si le proxy tourne sur la **même machine** ; `0.0.0.0` (ou l'IP d'une interface) s'il est sur une autre machine — filtrez alors le port par pare-feu |
| `NGINX_PORT` | port local d'nginx (défaut `8080`) |
| `TRUSTED_PROXY_CIDR` | adresse ou réseau du proxy, vu depuis nginx (ex. `172.16.0.0/12` si le proxy est sur la même machine, via la passerelle Docker). **Indispensable** : sans lui, toutes les requêtes semblent venir du proxy et la limitation de débit par IP s'applique à tout le monde d'un coup |

Le proxy doit transmettre `Host`, `X-Forwarded-For` et `X-Forwarded-Proto`, et poser
`Strict-Transport-Security`. nginx (interne) ajoute les en-têtes `X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` et masque sa version ;
il **ne croit jamais** un `X-Forwarded-For` venant d'un client (il le remplace par l'IP
réelle). Exemple de bloc pour un nginx frontal :

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

### Comptes

- **Admin** : créé au premier démarrage (`ADMIN_EMAIL`/`ADMIN_PASSWORD`). Il invite des
  **créateurs** (page *Administration → Utilisateurs*) : ils peuvent créer des projets et
  y inviter des collègues.
- **Invités** (invités à un projet par un créateur) : ne peuvent pas créer de projet ;
  leur compte est supprimé automatiquement quand ils n'ont plus aucun projet. Un
  créateur ou un admin n'est jamais supprimé automatiquement.

### Capacité et limites

| Variable | Rôle |
|----------|------|
| `WORKER_CONCURRENCY` | tâches de fond en parallèle (imports, analyses, exports) — défaut `3` |
| `IMPORT_MAX_ARTICLES` | plafond d'articles par import — défaut `5000` (max NCBI : 10 000) |
| `TASK_SOFT_TIME_LIMIT` | durée max d'une tâche en secondes — `0` = aucune |
| `*_MEM_LIMIT` | `OLLAMA_`, `LAYA_`, `WORKER_`, `BACKEND_`, `FRONTEND_` : plafonds mémoire (défauts 24g, 8g, 8g, 2g, 1g) |
| `PRIVACY_URL` | lien « politique de confidentialité » affiché à la première connexion |

Les PDF d'articles sont stockés dans PostgreSQL : prévoir de la place (jusqu'à 25 Mo par
fichier). Seuls les membres LEAD peuvent lancer un import, un export ou remplacer le
texte intégral d'un article.

### Sauvegarde et restauration

Tout ce qui compte est dans la base PostgreSQL (comptes, projets, articles, PDF,
annotations, modèles et clés chiffrées).

```bash
scripts/backup.sh                              # un pg_dump compressé dans ./backups (14 j conservés)
scripts/restore.sh backups/diggypaper-….sql.gz # REMPLACE la base courante (confirmation demandée)
TARGET_DB=essai scripts/restore.sh <fichier>   # vérifier une sauvegarde dans une base à part
```

Planification : `0 2 * * * cd /chemin/diggypaper && scripts/backup.sh >> backups/backup.log 2>&1`.
**Copiez `./backups` et votre `.env` hors de la machine** : sans `ENCRYPTION_KEY`, les clés
API chiffrées de la base sont illisibles. L'export RO-Crate sert à la reproductibilité,
pas de sauvegarde (il n'inclut ni comptes, ni modèles, ni partages ; pas d'import pour
l'instant). `docker compose down -v` **efface** les volumes.

### Réseau sortant

Les modèles externes saisis par un utilisateur et les téléchargements de PDF ouverts
sont refusés vers des adresses internes (contrôle à chaque appel). La fenêtre de
réécriture DNS entre la vérification et la connexion n'est pas fermée : pour une
instance exposée à des utilisateurs non fiables, filtrez aussi la sortie des conteneurs
`backend`/`worker` vers votre réseau interne (pare-feu / proxy de sortie).

### Inférence locale (Laya / Kev / Ollama)

Deux familles de modèles, deux services :

- **Laya** (modèle de décision, encodeur type BERT ~400 M, scores calibrés,
  protocole `POST /v1/systemone`) : servi par le service Docker `laya`
  (`laya-serve`, :8000, build `./laya`, poids dans le volume `hf`, `LAYA_URL`
  défaut `http://laya:8000`). **CPU par design** — un encodeur de cette taille
  n'a rien à gagner d'un GPU.
- **Kev** (modèles génératifs GGUF, 0.8B / 4B) : soit via le service `ollama`
  de la stack (téléchargés depuis l'UI, poids dans le volume `ollama`,
  `LLM_API_BASE_URL` défaut `http://ollama:11434/v1`), soit via un serveur
  `llama serve` séparé (`KEV_URL`, défaut vide).

Le tri CPU est suffisant : Kev-0.8B ≈ 3–6 s/article, Kev-4B ≈ 10–20 s/article
sur un Core Ultra 5 225H.

**GPU sur Windows (Docker Desktop)** : pas de passthrough d'iGPU Intel dans
Docker Desktop (NVIDIA uniquement) — un Ollama en conteneur reste sur CPU,
aucun flag ne change cela. Pour exploiter l'Arc 130T : Ollama **natif
Windows** avec `OLLAMA_IGPU_ENABLE=1`, `OLLAMA_VULKAN=1`, `OLLAMA_NUM_GPU=999`,
puis `LLM_API_BASE_URL=http://host.docker.internal:11434/v1` dans le `.env`
(le compose supporte déjà l'override).

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

Toutes les routes sont sous `/api/v1`. Auth par cookie JWT (httpOnly, Secure par défaut,
SameSite=Strict, révoqué au changement de mot de passe et à la déconnexion). Rôles : `LEAD` / `ANNOTATOR` / `ADMIN`.

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
