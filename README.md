# Diggypaper

Plateforme **souveraine et collaborative de revue systématique de littérature**.
On importe un corpus depuis PubMed, Europe PMC, OpenAlex ou HAL, on récupère les textes intégraux ouverts, on pose
une ou plusieurs questions au corpus, des modèles (qui tournent sur CPU, sans cloud) trient
et extraient les réponses, puis des relecteurs humains valident ou corrigent. Les résultats
s'exportent en tableur, en RO-Crate et en diagramme PRISMA.

> Interface en français, anglais, allemand et espagnol. RGPD *privacy by design*.

---

## Fonctionnalités

### Constituer le corpus
- **Import multi-sources** : PubMed, Europe PMC, OpenAlex (métadonnées) ou HAL
  (thèses, HDR, dépôts français), par requête, avec le volume attendu avant import, un plafond
  réglable (`IMPORT_MAX_ARTICLES`), le dédoublonnage — par DOI, entre sources — et les
  métadonnées (journal, date, auteurs, DOI).
- **Import d'un RO-Crate** : un zip exporté par Diggypaper (Projets → « Importer un RO-Crate »,
  jusqu'à 1 Go) recrée un projet : question et items, articles, textes intégraux, PDF, réponses du
  modèle (certitudes, extraits de preuve) et réponses humaines finales. Le crate ne contient qu'une
  recherche : elle devient la recherche initiale. Les relectures sont attribuées à la personne qui
  importe, sans note de pertinence ; le gold, les autres recherches et l'équipe ne sont pas dans
  le crate. Le zip est vérifié (structure, tailles, chemins) avant toute lecture.
- **Assistant de requête PubMed** : on décrit sa recherche en texte libre, un modèle (au choix
  parmi ceux du compte) propose une requête, on affine en discutant. Chaque proposition est
  vérifiée par PubMed : nombre de résultats, termes inconnus (un MeSH inventé est signalé) et, si
  on donne des articles attendus (DOI / PMID), combien la requête en retrouve. Le modèle est
  borné côté serveur : il ne peut servir qu'à cela (sorties hors requête refusées, conversation
  et débit limités).
- **Liste de références** à la place d'une recherche : DOI ou PMID collés (un par ligne), ou
  export BibTeX, RIS, CSL JSON ou CSV de Zotero / EndNote / Mendeley. Chaque entrée est
  complétée via PubMed (Europe PMC pour retrouver le PMID d'un DOI), sinon Crossref, sinon les
  données du fichier ; les doublons sont ignorés et les références non résolues listées.
- **Enrichissement par citations** : à partir de tout ou partie du corpus (sans analyse
  préalable), ajoute les références et les citants des articles de départ (iCite, NIH). Les
  candidats les plus liés aux articles de départ entrent d'abord (seuil et plafond réglables,
  estimation avant import) ; une recherche est créée sur les nouveaux articles pour que le
  modèle valide leur inclusion. L'origine de chaque article (PubMed, liste, citation) est
  conservée et apparaît dans le PRISMA (« identification par d'autres méthodes »).
- **Textes intégraux libres d'accès** : PMC, puis Europe PMC, Unpaywall et OpenAlex
  (manuscrits acceptés, dépôts institutionnels, préprints bioRxiv / medRxiv / arXiv). Les
  articles sans copie ouverte restent en résumé seul. Aucune source pirate.
- **PDF déposé à la main** (accès institutionnel) : le texte est extrait et sectionné ; on
  vérifie que le PDF correspond bien à l'article (titre, à défaut DOI) avant de l'adopter.
- **Dépôt de PDF en lot** : plusieurs PDF d'un coup, chacun rattaché à l'article dont il porte
  le titre (ou le DOI) ; les cas ambigus ou sans correspondance sont signalés.
- **Recherche relancée par article** : relit la notice PubMed (un préprint publié change de
  PMCID ou de DOI) puis cherche à nouveau le texte intégral.

### Poser des questions au corpus : les recherches
Un projet contient plusieurs **recherches**. Une recherche, c'est une **question** (ses items de
réponse : oui/non, choix, texte libre) posée à un **lot d'articles**, avec ses propres résultats,
relectures, analyses et références. Le lot est tout le corpus, ou un sous-ensemble choisi par
critères — par exemple « les articles où la recherche 1 a répondu oui », avec relecture humaine
ou texte intégral, dans une plage de pertinence, un tirage au hasard ou une liste. On pose ainsi
une seconde question aux seuls articles retenus par la première, sans rien réanalyser.
- **Recherches successives** : on programme la recherche 2 pendant que la 1 tourne : elle démarre
  seule dès que la 1 a fini, sur ses résultats (avec des critères : acceptés par l'IA, réponse à un
  item…) ou sur le même lot. Une seule analyse tourne à la fois dans un projet, les suivantes
  attendent leur tour. La question d'une recherche déjà analysée ou relue ne se modifie plus :
  une nouvelle recherche est créée sur le même lot.
- Choix des **parties du texte** lues par les modèles (résumé, méthodes, résultats…).
- **Modèles génératifs** (Ollama en local, ou toute API compatible OpenAI) : réponses
  structurées, texte libre et citations de preuve. La page *Modèles* lit les ressources du
  serveur (RAM, cœurs, AVX2, GPU) et recommande un modèle adapté.
- **Pré-filtre** rapide (petit modèle sur titre + résumé) avant le modèle principal, **triage automatique** (accepté, rejeté,
  à relire) selon des seuils de confiance, estimation du temps, **reprise** d'une analyse
  interrompue et **arrêt** d'une analyse en cours (les articles déjà traités sont conservés).

### Gold standard, échantillon à l'aveugle et benchmark
- **Gold standard** : une référence vient d'une **relecture humaine**. N'importe quel membre du
  projet ajoute une relecture au gold depuis la fiche article, ou toutes les relectures d'un coup.
  Chaque référence est une copie figée, avec un lien vers sa fiche d'origine, son relecteur et
  son statut (validée, corrigée, à l'aveugle) ; elle est signalée « obsolète » si la relecture a
  changé depuis. Le gold vaut pour toutes les recherches de même question.
- **Échantillon à l'aveugle**, avant l'analyse complète : N articles tirés au hasard (N réglable)
  à relire **sans voir les réponses de l'IA**, pour constituer un gold non biaisé.
- **Éduquer les modèles** : à chaque analyse, case « utiliser N exemples du gold dans le prompt »
  (ces exemples sont exclus de la notation) et case « valider les résultats avec le gold » (rappel,
  précision, F1 de la pertinence et de chaque item).
- **Benchmark** (mise au point) : mêmes modèles et mêmes articles (un sous-lot), plusieurs
  **formulations de prompt** en saisie libre, pour trouver les meilleures questions et la meilleure
  sortie. Les variantes sont classées selon un **critère défini sur un item de la question** (par
  défaut le rappel de la pertinence), avec l'accord entre variantes, le détail par item, la
  liste des articles où elles divergent et, si on le demande, la notation contre le gold.

### Relire
- Fiche de relecture : texte avec **surlignage des preuves**, réponses du modèle et certitude
  par item, **validation, correction ou complément** par le relecteur, avec enregistrement du
  statut (validé, corrigé, saisi à la main) et de la personne qui a relu.
- File de relecture dédiée aux articles incertains, parcours **au doigt** (swipe) sur mobile.
- Liste des résultats avec **filtres sur les réponses** (par item), statut de l'IA, statut de
  relecture ; les réponses affichées sont celles de la relecture quand elle existe. La file de
  relecture suit les filtres et le tri de la liste.

### Exporter et documenter
- **Tableur XLSX** (réponses IA, relectures, certitudes, liens), **références RIS et BibTeX**.
- **Archive RO-Crate** (articles, PDF, prédictions, annotations, paramètres de l'analyse :
  tout ce qu'il faut pour reproduire), générée en tâche de fond, avec sa licence (`CRATE_LICENSE_*`).
- **Diagramme de flux PRISMA 2020** (SVG et PDF) calculé depuis les données du projet.

### Travailler à plusieurs
- **Comptes** : l'administrateur invite des **créateurs** de projets ; ceux-ci invitent des
  collègues, simples **invités** dont le compte disparaît quand ils n'ont plus de projet.
- **Rôles par projet** : responsable de projet (LEAD) et annotateur. Invitations par e-mail dans la
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
- **Modèles** : modèles génératifs (endpoint OpenAI-compatible `/v1/chat/completions` :
  Ollama, API externe).
  Réglages SMTP / NCBI / LLM gérés en base (chiffrés) ou, à défaut, par variables d'environnement.

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

Stack de base (nginx, frontend, backend, worker, postgres, redis) + services de modèles
**optionnel** (ollama : voir « Modèles »). nginx est le **seul service exposé**, tout
le reste reste sur le réseau interne de Docker.

```
reverse proxy TLS (établissement)            ← HTTPS, certificat, HSTS
  └── nginx (127.0.0.1:8080)
        ├── /api/v1/*  ──►  backend (FastAPI/uvicorn, :8000)
        └── /*         ──►  frontend (Next.js, :3000)

backend ──►  postgres (16)  ·  redis (7)  ◄──  worker (Celery, tâches générales)
                                                    └──  worker-ingest (imports, 1 process)
backend / worker ──►  ollama (optionnel)  ·  API externe de votre choix
```

### Quick-start

```bash
# 1. Générer l'environnement : secrets aléatoires (JWT, ANON, chiffrement AES), port, admin
python3 scripts/setup_env.py --yes   # ou sans --yes : interactif (recommandé)
#    puis éditer .env si besoin (APP_BASE_URL, SMTP_*, ADMIN_*, …) — chmod 600 déjà posé

# 2. Démarrer la stack (build + up)
make up                       # ou : docker compose up -d --build

# 3. Vérifier
make ps                       # état des services
make logs                     # logs en temps réel
```

Ne recopiez pas `.env.example` tel quel : ses valeurs sont celles de développement et le
backend **refuse de démarrer** avec elles (fail-fast sur `JWT_SECRET`, `ANON_SALT`,
`ENCRYPTION_KEY` — voir `backend/app/core/config.py`).

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

### Mettre à jour une installation existante

Après `git pull`, avant `docker compose up -d --build` :

1. **Services de modèles.** Laya et Kev (modèles de décision) sont retirés : peu fiables en
   zéro-shot sur des revues de littérature. Les modèles correspondants sont désactivés en base
   (les prédictions déjà produites sont conservées). Retirez `laya` de `COMPOSE_PROFILES`,
   `LAYA_URL` et `KEV_URL` de votre `.env` (`docker compose up -d --remove-orphans` arrête leur
   conteneur). Ollama reste un profil optionnel :
   ```bash
   COMPOSE_PROFILES=ollama
   ```
   Sans modèle local (API externe seulement), ne mettez rien : aucun service de modèle ne démarre.
2. **Cookies.** `COOKIE_SECURE` vaut `true` par défaut : derrière HTTPS rien à faire ; pour un accès
   HTTP simple, mettez `COOKIE_SECURE=false`.
3. **Port.** nginx n'écoute plus que sur `127.0.0.1` ; si le reverse proxy est sur une autre machine,
   `NGINX_BIND=0.0.0.0`.
4. **Sauvegardez** (`scripts/backup.sh`) : les migrations ajoutent les recherches (chaque projet
   reçoit une « Recherche initiale » qui reprend ses résultats, relectures et références).
5. Les conteneurs backend et worker tournent désormais sans root ; leurs volumes sont réparés au
   démarrage.

### Comptes

- **Admin** : créé au premier démarrage (`ADMIN_EMAIL`/`ADMIN_PASSWORD`). Il invite des
  **créateurs** (page *Administration → Utilisateurs*) : ils peuvent créer des projets et
  y inviter des collègues.
- **Invités** (invités à un projet par un créateur de projets) : ne peuvent pas créer de projet ;
  leur compte est supprimé automatiquement quand ils n'ont plus aucun projet. Un
  créateur ou un admin n'est jamais supprimé automatiquement.

### Capacité et limites

| Variable | Rôle |
|----------|------|
| `WORKER_CONCURRENCY` | tâches de fond en parallèle (analyses, exports, benchmarks) — défaut `3` ; les imports tournent sur `worker-ingest` (1 process, file `ingest`) pour rester sous le plafond NCBI |
| `IMPORT_MAX_ARTICLES` | plafond d'articles par import — défaut `5000` ; `0` = illimité (au-delà de 10 000 résultats, la requête est découpée par date de publication, les plus récents d'abord) |
| `TASK_SOFT_TIME_LIMIT` | durée max d'une tâche en secondes — `0` = aucune |
| `*_MEM_LIMIT` | `OLLAMA_`, `WORKER_`, `INGEST_`, `BACKEND_`, `FRONTEND_` : plafonds mémoire (défauts 24g, 8g, 2g, 2g, 1g) |
| `DISK_ALERT_PERCENT` / `DISK_ALERT_FREE_GB` | alerte disque (e-mail aux admins, bandeau) — défauts `85` % / `5` Go |
| `PROJECT_RETENTION_DAYS` | jours d'inactivité avant d'avertir les responsables d'un projet — `0` (défaut) = jamais de suppression automatique |
| `PROJECT_RETENTION_GRACE_DAYS` | délai de grâce avant suppression, après l'avertissement — défaut `14` |
| `PRIVACY_URL` | lien « politique de confidentialité » affiché à la première connexion |

Les PDF d'articles sont stockés dans PostgreSQL : prévoir de la place (jusqu'à 25 Mo par
fichier). La page *Paramètres* (admin) montre l'espace disque, le poids des PDF et les projets
(taille, inactivité) ; le worker (celery beat, toutes les 6 h) envoie une alerte aux admins quand
le disque dépasse le seuil. Avec `PROJECT_RETENTION_DAYS`, les responsables d'un projet inactif
sont prévenus par e-mail, puis le projet est supprimé à l'issue du délai de grâce sauf activité ou
clic sur « Conserver ce projet ». Seuls les responsables de projet peuvent lancer un import, un export RO-Crate ou remplacer le
texte intégral d'un article (les exports tableur, références et PRISMA sont ouverts à tout membre).

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

### Modèles : ce qui fonctionne, et ce qu'on déploie

Le tri utilise un **modèle génératif** qui répond en JSON (décision, items, citations). Deux
voies, au choix, dans `.env` puis `docker compose up -d --build` :

```bash
COMPOSE_PROFILES=ollama          # modèles locaux (sinon : aucun service de modèle)
# LLM_API_BASE_URL=http://ollama:11434/v1   # défaut du compose
```

| Voie | Déploiement | Usage |
|------|-------------|-------|
| **Ollama** (Qwen, Gemma, Llama… en GGUF) | profil `ollama` | local et souverain ; vitesse selon le CPU/GPU |
| **API externe** (OpenAI, Mistral, serveur interne…) | rien : URL + clé dans *Modèles* | rapide, pas de matériel ; les articles quittent le serveur |

- La page *Modèles* détecte les ressources du serveur (cœurs, RAM, AVX2, GPU) et recommande un
  modèle adapté, installable en un clic : moins de 16 Go de RAM → très petit modèle (ou API
  externe) ; 16–32 Go → ~4B ; 32 Go et plus → « mixture d'experts » 30B (3B actifs) ; GPU → ~12B.
  Les modèles conseillés sont de type *instruct* (sans mode « réflexion », qui multiplie le
  temps par article). Sans AVX2 ni GPU, l'inférence locale est 2 à 3 fois plus lente. Les temps
  affichés sont des ordres de grandeur : mesurez toujours la précision sur votre gold (*Benchmarks*).
- Chaque compte peut enregistrer ses propres modèles externes (clé chiffrée) et les partager avec
  des comptes ou des projets ; seuls les modèles qu'un compte possède ou qu'on lui a partagés
  sont utilisables. Un modèle partagé dépense les crédits de son propriétaire.
- Les modèles de décision sans chat (Laya, Kev…) ne sont plus pris en charge : la recherche
  Hugging Face les signale et refuse de les installer.

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
| GET/POST | `/projects/{id}/searches` | Recherches (question + lot d'articles) ; `?search=<id>` sur les routes de résultats |
| POST | `/projects/{id}/searches/preview` | Nombre d'articles d'une sélection |
| GET/POST | `/projects/{id}/gold` · `/gold/from-reviews` | Références (gold) et ajout depuis les relectures |
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

# Intégration continue : .github/workflows/ci.yml (ruff, pytest, tsc, lint, jest, build)
# Dépendances Python figées dans backend/constraints.txt (utilisées par l'image et la CI).

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
