# Diggypaper

Plateforme **souveraine et collaborative de revue systématique de littérature**.
On importe un corpus depuis PubMed/PMC, on récupère les textes intégraux ouverts, on pose
une ou plusieurs questions au corpus, des modèles (qui tournent sur CPU, sans cloud) trient
et extraient les réponses, puis des relecteurs humains valident ou corrigent. Les résultats
s'exportent en tableur, en RO-Crate et en diagramme PRISMA.

> Interface en français, anglais, allemand et espagnol. RGPD *privacy by design*.

---

## Fonctionnalités

### Constituer le corpus
- **Import PubMed/PMC** par requête, avec le volume attendu avant import, un plafond réglable
  (`IMPORT_MAX_ARTICLES`), le dédoublonnage et les métadonnées (journal, date, auteurs, DOI).
- **Textes intégraux libres d'accès** : PMC, puis Europe PMC, Unpaywall et OpenAlex
  (manuscrits acceptés, dépôts institutionnels, préprints bioRxiv / medRxiv / arXiv). Les
  articles sans copie ouverte restent en résumé seul. Aucune source pirate.
- **PDF déposé à la main** (accès institutionnel) : le texte est extrait et sectionné ; on
  vérifie que le PDF correspond bien à l'article (titre, à défaut DOI) avant de l'adopter.
- **Recherche relancée par article** : relit la notice PubMed (un préprint publié change de
  PMCID ou de DOI) puis cherche à nouveau le texte intégral.

### Poser une question au corpus
- Une **question** et ses **items** de réponse : oui/non, choix dans une liste, texte libre.
- Choix des **parties du texte** lues par les modèles (résumé, méthodes, résultats…).
- **Modèles de décision** (Laya, Kev) : un seul passage, probabilités calibrées, très
  rapides sur CPU — adaptés au tri et aux items oui/non ou à choix.
- **Modèles génératifs** (Ollama en local, ou toute API compatible OpenAI) : réponses en texte
  libre et citations de preuve.
- **Pré-filtre** rapide avant le modèle principal, **triage automatique** (accepté, rejeté,
  à relire) selon des seuils de confiance, estimation du temps, **reprise** d'une analyse
  interrompue et **arrêt** d'une analyse en cours (les articles déjà traités sont conservés).
- **Gold standard**, **prompts versionnés** et **benchmark** (précision, rappel, F1, temps)
  pour comparer des modèles et des formulations.

### Relire
- Fiche de relecture : texte avec **surlignage des preuves**, réponses du modèle et certitude
  par item, **validation, correction ou complément** par le relecteur, avec enregistrement du
  statut (validé, corrigé, saisi à la main) et de la personne qui a relu.
- File de relecture dédiée aux articles incertains, parcours **au doigt** (swipe) sur mobile.
- Liste des résultats avec **filtres sur les réponses** (par item), statut de l'IA, statut de
  relecture ; les réponses affichées sont celles de la relecture quand elle existe.

### Exporter et documenter
- **Tableur XLSX** (réponses IA, relectures, certitudes, liens), **références RIS et BibTeX**.
- **Archive RO-Crate** (articles, PDF, prédictions, annotations, paramètres de l'analyse :
  tout ce qu'il faut pour reproduire), générée en tâche de fond.
- **Diagramme de flux PRISMA 2020** (SVG) calculé depuis les données du projet.

### Travailler à plusieurs
- **Comptes** : l'administrateur invite des **créateurs** de projets ; ceux-ci invitent des
  collègues, simples **invités** dont le compte disparaît quand ils n'ont plus de projet.
- **Rôles par projet** : responsable (LEAD) et annotateur. Invitations par e-mail dans la
  langue de la personne invitée.
- **Modèles** : registre des modèles, **partage** d'un modèle ou d'une clé avec des personnes
  ou un projet, modèles externes **personnels** ; les clés sont chiffrées (AES-256-GCM). Un
  compte ne peut utiliser que les modèles qu'il possède ou qu'on lui a partagés.
- **Administration** : liste des comptes (modifier, désactiver, supprimer avec ou sans leurs
  projets), invitations en attente, tous les projets, réglages SMTP / NCBI / LLM.

### Souveraineté et sécurité
- **100 % CPU, hébergé chez soi** : aucune donnée n'est envoyée à un service cloud (hors
  interrogation des API publiques PubMed, Europe PMC, Unpaywall, OpenAlex).
- **RGPD** : consentement, anonymisation des annotations à la suppression d'un compte.
- Session par cookie httpOnly révocable, limitation de débit, contrôle des URL sortantes,
  neutralisation des formules dans les exports, sauvegarde et restauration scriptées.

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
- **Modèles** : modèles de décision (protocole `/v1/systemone` : Laya, Kev) et modèles
  génératifs (endpoint OpenAI-compatible `/v1/chat/completions` : Ollama, API externe).
  Réglages gérés en base (chiffrés), pas en variables d'environnement.

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

**Géré en base (chiffré)** : `llm_api_base_url`, `llm_api_key`,
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

Deux familles de modèles, qui ne s'utilisent pas de la même façon :

**Modèles de décision** (protocole `POST /v1/systemone`) — ils lisent un texte et répondent
à des questions typées (oui/non, choix) avec des probabilités calibrées, en un seul
passage. Ils ne génèrent ni texte libre ni citations, et n'ont **pas** d'interface de chat.

- **Laya** (encodeur type BERT ~400 M) : service Docker `laya` (build `./laya`, poids dans le
  volume `hf`, `LAYA_URL` défaut `http://laya:8000`). **CPU par design.** Enregistré au
  démarrage.
- **Kev** (4B, GGUF) : servi par **llama.cpp**, pas par Ollama (Ollama ne propose que
  `/v1/chat/completions`, d'où l'erreur 400 si on l'y installe comme modèle de chat).
  Service optionnel du compose :
  ```bash
  # .env : KEV_URL=http://kev:8080
  docker compose --profile kev up -d      # télécharge ggml-org/Kev-4B-GGUF au premier démarrage
  ```
  Il est alors enregistré automatiquement sous le nom `Kev-4B`.

**Modèles génératifs** (`/v1/chat/completions`) — réponses en texte libre et citations de
preuve, plus lents. Service `ollama` de la stack (modèles GGUF de chat installés depuis la
page *Modèles*, poids dans le volume `ollama`, `LLM_API_BASE_URL` défaut
`http://ollama:11434/v1`) ou n'importe quelle API compatible OpenAI (URL + clé + nom du
modèle, depuis la page *Modèles*).

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
│   │   ├── settings/         # réglages et secrets chiffrés
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
