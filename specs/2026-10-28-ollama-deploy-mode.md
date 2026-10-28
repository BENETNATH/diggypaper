# Spec TDD — Flag de déploiement Ollama (`OLLAMA_MODE` : docker / host / off)

**Date :** 2026-10-28
**Repo :** diggypaper (FastAPI + Celery + Next.js)
**Périmètre de code :** `backend/app/ollama_mode.py` (nouveau), `backend/app/api/v1/models.py`,
`docker-compose.yml`, `.env.example`, `frontend/src/lib/api.ts` + `frontend/src/app/models/page.tsx`
(+ 4 fichiers i18n), tests `backend/tests/test_ollama_mode.py` (nouveau) et `backend/tests/test_models_api.py`
**Statut :** spec TDD à implémenter — aucun code d'implémentation complet ci-dessous, uniquement signatures
et extraits de référence

---

## 1. Contexte — demande produit

Aujourd'hui, l'inférence locale est couplée au conteneur Ollama du compose : service `ollama`
(`docker-compose.yml` l.43–50, profil `ollama`), URL joignable entre conteneurs
(`LLM_API_BASE_URL=http://ollama:11434/v1`, interpolation compose l.66/l.104 ; le réglage DB
`llm_api_base_url` reprend cette valeur via le défaut env, `backend/app/settings/keys.py` l.43).

On veut pouvoir tester l'app contre :

1. un **Ollama déjà déployé sur la machine hôte** (service système, autre compose) — le conteneur
   compose doit alors être absent, et l'app doit pointer vers l'hôte ;
2. **pas d'Ollama du tout** (instance 100 % API clé, type `OPENAI_API`).

Demande utilisateur : un flag `.env` à trois valeurs, `OLLAMA_MODE` :

| Valeur | Sémantique |
|---|---|
| `docker` (dÉFAUT) | comportement actuel : Ollama dans le compose (`COMPOSE_PROFILES=ollama`) |
| `host` | Ollama local déjà déployé sur la machine, URL joignable depuis les conteneurs |
| `off` | pas d'Ollama — backends API clé uniquement |

Le mode est porté par le **couple `OLLAMA_MODE` / `COMPOSE_PROFILES`** (pas de magie) :
`OLLAMA_MODE` dit à l'app quoi faire, `COMPOSE_PROFILES` dit au compose quoi démarrer.
Serv-ics-testing est CPU-only : le branchement GPU hôte est documenté (§3.8) sans exécution.

**Garde-fou central (invariant) : le mode `docker` est strictement invariant** — quand
`OLLAMA_MODE` est absent (ou `docker`), aucun comportement actuel ne change : mêmes URLs,
mêmes pings, mêmes codes HTTP, mêmes corps de requête.

---

## 2. État actuel (références fichier:ligne)

### 2.1 `backend/app/settings/keys.py` — pattern de défauts à réutiliser (sans l'étendre)

- `DEFAULTS` (l.42–46) : valeurs par défaut lues **depuis l'env à l'import** —
  `llm_api_base_url` = `os.environ.get("LLM_API_BASE_URL", "http://localhost:11434/v1")` (l.43),
  `inference_timeout` (l.44), `llm_extra_body` (l.45). C'est le pattern de lecture env→défaut.
- `KNOWN_KEYS` / `NON_SECRET_KEYS` (l.15–31) : clés **stockées en base** (Module 7).
- **Décision : `OLLAMA_MODE` n'ENTRE PAS dans ce module** — c'est un flag de déploiement,
  pas un setting DB (pas de `set_setting`, pas d'UI Paramètres, pas de ligne `system_settings`).
  Ajouter une clé à `KNOWN_KEYS` l'exposerait dans l'API settings et ouvrirait un chemin de
  changement à chaud non désiré. Périmètre minimal : un module dédié (§3.1).

### 2.2 `backend/app/api/v1/models.py` — tout le point de passage Ollama

- `_BACKENDS` (l.35) : `{"OLLAMA", "LLAMA_CPP", "TRANSFORMERS", "OPENAI_API"}` ;
  `ModelCreate` (l.38–57) — `backend_type` défaut `"OLLAMA"` (l.40).
- `_PULLS` (l.112) : état des téléchargements en mémoire.
- `_ollama_base` (l.119–122) : `base = await get_setting(db, "llm_api_base_url")`,
  `rstrip("/")`, `removesuffix("/v1")` → URL racine des endpoints natifs `/api/*`.
  **C'est le SEUL point de lecture de la base Ollama côté API.**
- `_reachable` (l.148–157) : ping HTTP (n'importe quel code), timeout 2 s, jamais bloquant.
- `capabilities` (l.160–171) : `"ollama": {"configured": bool(ollama), "reachable": await _reachable(ollama, "/api/tags")}`
  (l.168) — **aucun champ `mode` aujourd'hui** ; ping systématique si base non vide.
- `list_ollama` (l.174–193) : admin-only, `GET {base}/api/tags`, 502 `ollama_unreachable` si échec (l.185).
- `_ollama_delete` (l.196–206) : `DELETE {base}/api/delete`, 502 `ollama_unreachable` / `ollama_delete_failed`.
- `_pull` (l.209–235) : `POST {base}/api/pull` (stream), puis `_register(… "OLLAMA" …)` (l.231).
- `pull_ollama` (l.238–246) : admin-only, 409 `pull already running` (l.243),
  `asyncio.create_task(_pull(await _ollama_base(db), …))` (l.245).
- `register_ollama` (l.249–254) : admin-only, `_register` seul (aucun appel HTTP).
- `create_model` (l.276–293) : admin-only, 409 `model name already exists` (l.283) —
  **aucun contrôle de mode aujourd'hui** sur `backend_type`.
- `register_external` (l.310–327) / `test_external` (l.296–307) : chemin `OPENAI_API`
  (payload `base_url` fourni par le client) — **indépendant du réglage `llm_api_base_url`**.
- `update_model` (l.330–346) : `is_active` / `ram_usage_mb` / `model_identifier` — aucun appel Ollama.
- `delete_model` (l.349–358) : branche `if m.backend_type == "OLLAMA": await _ollama_delete(…)` (l.355–356)
  **avant** la suppression de la ligne ; sinon suppression directe (l.357–358).
- `search_hf` (l.484–513) : recherche Hugging Face (pas d'appel Ollama).
- `install_hf` (l.550–571) : admin-only, 422 `decision_model` (l.556), 409 `install already running`
  (l.561), `asyncio.create_task(_pull(await _ollama_base(db), …))` (l.570).

### 2.3 `docker-compose.yml`

- En-tête (l.1–7) : documente déjà les profils — « Activez ceux dont vous avez besoin dans .env,
  ex. `COMPOSE_PROFILES=ollama` » (l.6–7).
- Service `ollama` (l.43–50) : **`profiles: ["ollama"]` DÉJÀ présent (l.44)** ; image épinglée 0.40.3
  (l.45), volume `ollama:/root/.ollama` (l.47), `mem_limit: ${OLLAMA_MEM_LIMIT:-24g}` (l.50).
  Aucune exposition de port, aucun `devices`/`deploy` GPU.
- Services `backend` (l.52–92) et `worker` (l.94–129) :
  `LLM_API_BASE_URL: ${LLM_API_BASE_URL:-http://ollama:11434/v1}` (l.66, l.104) ;
  `LLM_TIMEOUT_SECONDS` (l.67, l.105) ; `LLM_EXTRA_BODY` (l.68, l.106).
  **Aucun `OLLAMA_MODE`, aucune `extra_hosts`.**
- `depends_on` : `backend` → postgres+redis (l.88–92) ; `worker` → postgres+redis+backend (l.123–129).
  **Aucun service ne dépend de `ollama`** → le profile peut être retiré sans effet de démarrage
  (vérifié ; rien à changer dans les `depends_on`).

### 2.4 `.env` / `.env.example` (noms de variables seulement — pas de valeurs affichées)

- `.env.example` : section DEPLOIEMENT (l.14–47) ; sous « --- Modèles --- » :
  `COMPOSE_PROFILES=ollama` commenté (l.138) et `LLM_API_BASE_URL=http://ollama:11434/v1`
  commenté « à changer pour un autre serveur » (l.139). **Pas de ligne `OLLAMA_MODE`.**
- `.env` réel : `COMPOSE_PROFILES` (l.108), `LLM_API_BASE_URL` (l.109), `LLM_TIMEOUT_SECONDS` (l.116),
  `LLM_EXTRA_BODY` (l.122). La ligne `OLLAMA_MODE` sera ajoutée par l'implémentation
  (section DEPLOIEMENT, à côté de `COMPOSE_PROFILES`) — le fichier `.env` n'est pas modifié par la spec.

### 2.5 Pipeline d'inférence (hors périmètre de code — comportement à documenter)

- `backend/app/inference/__init__.py` : `make_inference_service` (l.139–186) lit
  `llm_api_base_url` via `get_setting` (l.156) ; `infer_with` (l.210–230) : un modèle
  `OPENAI_API` part sur son propre `base_url` (l.222–228), **tous les autres backends passent par
  le service par défaut** (l.230) — donc un modèle `OLLAMA` pointe sur `llm_api_base_url`.
- `backend/app/tasks/inference.py` : service construit par `make_inference_service`
  (l.228–230) ; échec → `_record_failure` (l.576–586) : job `FAILED`, message assaini
  (`_sanitize` l.589–591, pas d'URL ni secret) ; lock modèle relâché dans `finally` (l.250–251).
  Erreur réseau Ollama absente → `InferenceError("connection_error")`
  (`inference/__init__.py` l.104–105).
- `backend/app/api/v1/query_assistant.py` : `infer_with` → `InferenceError` →
  502 `model_error:{exc}` (l.56–62).

### 2.6 Tests existants — `backend/tests/test_models_api.py` (patterns à réutiliser)

- 100 % offline : fixture `client` (conftest l.108–130, app réelle + SQLite in-memory + overrides),
  `admin_user` / `researcher_user` / `do_login` (conftest l.155–178), `session`.
- **Pattern mock http** : `real = httpx.AsyncClient ; httpx.AsyncClient = lambda **kw:
  real(transport=httpx.MockTransport(handler), **kw)` (test_delete_model_admin l.120–125) ;
  handler avec `raise AssertionError("Ollama ne doit pas être appelé")` (l.233–234, l.258–259)
  pour prouver l'absence d'appel.
- **Pattern monkeypatch** : `monkeypatch.setattr(m, "_reachable", fake)` où
  `from app.api.v1 import models as m` (capabilities l.424–429) ;
  `monkeypatch.setattr(m, "_hf_get", fake_hf)` (hf/install l.306).
- Capabilities : `test_capabilities_reports_ollama_hardware_and_recommendation` (l.420–448) —
  assertion l.444 : `caps["ollama"] == {"configured": True, "reachable": True}` (défaut
  `localhost:11434`, pas de `LLM_API_BASE_URL` dans conftest l.7–13).
- Suite Ollama : delete (l.111–227, dont 502 `ollama_unreachable` l.225 /
  `ollama_delete_failed` l.201), delete OPENAI_API sans appel Ollama (l.230–250),
  403 avant appel (l.253–276), register idempotent (l.278–291), hf (l.294–360).
- Réglages DB en test : `set_setting` (`app.settings.service`) sur la fixture `session`
  (pattern `backend/tests/test_inference.py` l.210+) — le `client` partage cette session
  (override `get_db`, conftest l.118–121) → l'API voit les valeurs posées.
- Hors fichier : `backend/tests/test_access_control.py` l.180–184 — un admin peut enregistrer
  un modèle `OPENAI_API` pointant sur une URL interne (`http://ollama:11434/v1`) → 201 :
  **le chemin `register_external` reste ouvert** (non concerné par le guard Ollama).

### 2.7 Frontend — `frontend/src/app/models/page.tsx` + `frontend/src/lib/api.ts`

- `ModelCapabilities` (api.ts l.1101–1105) : `ollama: Capability` avec
  `Capability = { configured, reachable }` (l.1092–1095). **Pas de champ `mode`.**
- `ModelInfo.backend_type` (api.ts l.217) : déjà exposé au client (payload `models.py` l.101)
  → l'UI sait si un modèle du registre est OLLAMA.
- `load()` (page.tsx l.42–57) : `listOllama()` appelé par un admin **sans condition sur `caps`**
  (l.51) ; échec absorbé (l.54–56) → `detected` vide, la liste reste utilisable.
- Carte « services » (l.129–172) : badge `svcUp` / `svcDown` / `svcOff` selon
  `reachable` / `configured` (l.133–134) ; hint `svcHow` (« Pour l'activer :
  COMPOSE_PROFILES=ollama… ») affiché quand non joignable (l.137) ; bouton « installer »
  des modèles recommandés : `isAdmin && caps.ollama.reachable && m.fits` (l.161).
- **Gating existant du bloc HfSearch : `isAdmin && caps?.ollama.configured` (l.174–178)** —
  le bloc disparaît donc DÉJÀ quand `configured=false` (mode off OU host mal configuré).
- Liste « modèles installés » (l.180–238) : chaque modèle du registre (tous backends) est
  affiché avec boutons Partage/Supprimer (l.201–216) — **aucune annotation de lançabilité
  selon le mode de déploiement**.
- i18n : `svcUp` / `svcOff` / `svcHow` dans `frontend/src/messages/{fr,en,de,es}.json`
  (l.695–698 dans chaque fichier).

**Verdict frontend** : le « propre » est déjà en place pour la *gestion* (HfSearch, boutons
installer, badge service) ; il **manque** (i) un hint pertinent en mode `off` (`svcHow` parle
d'activation, or c'est un choix de déploiement), (ii) une annotation sur les modèles OLLAMA
du registre (« non lançable ») — aujourd'hui leur suppression donne un 409 remonté brut dans
l'`ErrorBanner` (l.108).

---

## 3. Spécification

### 3.1 Flag `OLLAMA_MODE` — module dédié `backend/app/ollama_mode.py` (nouveau)

**Décision : un module dédié, pas `settings/keys.py`** (ponytail, périmètre le plus petit) —
justification en une phrase : `settings/keys.py` porte les clés du *stockage en base*
(`KNOWN_KEYS`, `is_secret`, `DEFAULTS` consumés par `get_setting`, cf.
`settings/service.py` l.15, l.84) ; `OLLAMA_MODE` est un flag de déploiement lu une fois à
l'import, sans écriture possible, et l'y mixerait dans l'API settings (PUT `/admin/settings`)
sans raison. Le *pattern* DEFAULTS est réutilisé dans l'esprit : lecture `os.environ.get`
à l'import, valeur par défaut explicite.

Signature exacte :

```python
# backend/app/ollama_mode.py
MODES: frozenset[str] = frozenset({"docker", "host", "off"})

#: Racine du conteneur Ollama du compose (sans /v1) — base résiduelle détectée en mode host.
COMPOSE_ROOT: str = "http://ollama:11434"

def resolve_mode() -> str:
    """Valeur normalisée de OLLAMA_MODE ; absente/empty/invalid → 'docker' (+ warning si invalid)."""
    ...

#: Lu à l'import (pattern DEFAULTS, settings/keys.py l.42–46) — tests : monkeypatcher ce module.
MODE: str = resolve_mode()
```

- `resolve_mode()` : `raw = os.environ.get("OLLAMA_MODE", "").strip().lower()` ;
  si `raw in MODES` → `raw` ; sinon → `"docker"`, et `logger.warning("OLLAMA_MODE invalide
  (%r) : 'docker' appliqué", raw)` **uniquement si `raw` non vide**.
- **Valeur absente → `docker`** (invariant §1). **Valeur invalide (ex. `banana`) → `docker` +
  warning** — justification : un flag de *déploiement* mal orthographié ne doit ni faire
  échouer le démarrage ni basculer silencieusement en `off` (perte de l'inférence locale) ni
  en `host` (appels vers une URL inexistante) ; rester sur le comportement actuel (fail-open
  vers le défaut) et avertir dans les logs du conteneur est le comportement le plus sûr et le
  plus lisible. Le casing est normalisé (`HOST` → `host`).
- Lecture à l'import (pas par appel) : cohérent avec un flag de déploiement qui change avec
  le conteneur, pas à chaud. Tests : `monkeypatch.setattr(app.api.v1.models, "MODE", …)`
  côté routes ; `monkeypatch.setenv` + `resolve_mode()` côté module.

### 3.2 Sémantique par mode (table de référence)

| Aspect | `docker` (défaut) | `host` | `off` |
|---|---|---|---|
| Conteneur compose | démarré (`COMPOSE_PROFILES=ollama`) | **absent** (profil non actif) | absent |
| Base URL Ollama | réglage `llm_api_base_url` (env `LLM_API_BASE_URL`, défaut compose `http://ollama:11434/v1`) — **inchangé** | **obligatoirement** le réglage `llm_api_base_url` (ex. `http://host.docker.internal:11434/v1`) | n/a |
| `capabilities.ollama` | `{mode: "docker", configured: bool(base), reachable: ping}` — inchangé | `{mode: "host", configured: base set ET base ≠ COMPOSE_ROOT, reachable: ping si configured else false}` ; base résiduelle compose → `configured=false` + **log warning** | `{mode: "off", configured: false, reachable: false}` **SANS ping** |
| `GET /models/ollama` | inchangé (502 si injoignable) | 409 si base résiduelle compose, sinon inchangé | 409 `ollama_disabled_by_deployment` |
| `POST /models/ollama/pull` | inchangé | idem | 409 (rien dans `_PULLS`) |
| `POST /models/ollama/register` | inchangé | idem | 409 |
| `POST /models/hf/install` | inchangé | idem | 409 (avant tout appel HF) |
| `POST /models` avec `backend_type=OLLAMA` | inchangé | idem | 409 |
| `POST /models` autres backends / `POST /models/external` | **inchangé** | **inchangé** | **inchangé** (201) |
| `DELETE /models/{id}` modèle OLLAMA | inchangé (502 si Ollama absent) | idem | 409 (ligne conservée, Ollama non appelé) |
| `DELETE /models/{id}` autre backend | inchangé | inchangé | inchangé (204) |
| Inférence d'un modèle OLLAMA du registre | inchangé | inchangé (vers la base du réglage) | échec propre : job `FAILED` `connection_error` (§3.6) |
| Corps de requête `extra_body` | appliqué aux backends locaux — inchangé | idem | idem (chemin inchangé) |

### 3.3 `GET /models/capabilities` — champ `mode` ajouté

`capabilities` (models.py l.160–171) renvoie :

```python
"ollama": {
    "mode": MODE,
    "configured": …,
    "reachable": …,
}
```

Branches (extrait de référence — le calcul `hardware` / `recommendation` l.166–170 est
totalement inchangé) :

```python
if MODE == "off":
    ollama = {"mode": MODE, "configured": False, "reachable": False}      # AUCUN ping
else:
    base = await _ollama_base(db)
    if MODE == "host" and base == COMPOSE_ROOT:
        logger.warning("OLLAMA_MODE=host mais llm_api_base_url pointe encore sur le "
                       "conteneur du compose (%s) : définir LLM_API_BASE_URL sur "
                       "l'Ollama de la machine hôte", base)
    configured = bool(base) and not (MODE == "host" and base == COMPOSE_ROOT)
    reachable = await _reachable(base, "/api/tags") if configured else False
    ollama = {"mode": MODE, "configured": configured, "reachable": reachable}
```

- **`host` + base résiduelle compose → `configured=false` + warning** (choix specifié,
  justifié) : le `.env` du compose impose `LLM_API_BASE_URL=http://ollama:11434/v1` par
  défaut (compose l.66), donc un opérateur qui passe en `host` sans éditer `.env` retombe
  exactement sur cette valeur ; `capabilities` est l'endroit naturel pour le signaler
  (badge `svcDown` + sections Ollama masquées côté UI, §3.5), avec un log exploitable.
  Le 422 à l'inscription aurait bloqué l'UI sans diagnostic d'ensemble, alors que le
  problème est le déploiement, pas le payload. Le refus d'action est porté par le guard
  409 (§3.4) — cohérent : *état visible + actions refusées*.
- `COMPOSE_ROOT` est comparé **après** dérive `_ollama_base` (sans `/v1`, `rstrip("/")`,
  models.py l.121–122) : `http://ollama:11434/v1`, `http://ollama:11434/v1/` et
  `http://ollama:11434` donnent tous `http://ollama:11434`.

### 3.4 Guard 409 des actions Ollama (signatures exactes)

Un seul helper dans `models.py`, appelé **après** `_require_admin` et **avant** toute
mutation/HTTP (état `_PULLS` non touché, ligne DB non touchée, `_hf_get` non appelé) :

```python
async def _require_ollama(db: AsyncSession) -> str:
    """Refuse les actions Ollama interdites par le mode de déploiement (409, détail précis).
    Renvoie la base Ollama (sinon le comportement existant est inchangé)."""
    if MODE == "off":
        raise HTTPException(status_code=409, detail="ollama_disabled_by_deployment")
    base = await _ollama_base(db)
    if MODE == "host" and base == COMPOSE_ROOT:
        raise HTTPException(status_code=409, detail="ollama_base_url_is_compose_default")
    return base
```

**409** (et non 422/403) : le payload est valide, l'action est légitime (admin), c'est la
*configuration de déploiement* qui entre en conflit — même famille que les 409 existants
`pull already running` (l.243), `model name already exists` (l.283), `install already running`
(l.561). En mode `docker` le helper renvoie la base sans rien changer (502 `ollama_unreachable`
conservé si le conteneur n'est pas up — comportement actuel exact, cf. tests l.225).

Points d'appel (un par route concernée) :

| Route (ligne) | Insertion du guard |
|---|---|
| `list_ollama` (l.175) | `base = await _require_ollama(db)` remplace `await _ollama_base(db)` (l.178) |
| `pull_ollama` (l.239) | `await _require_ollama(db)` juste après `_require_admin` (avant le check `_PULLS` l.242) |
| `register_ollama` (l.250) | `await _require_ollama(db)` après `_require_admin` |
| `create_model` (l.277) | **si `payload.backend_type == "OLLAMA"`** : `await _require_ollama(db)` après `_require_admin`, avant le check duplicate (l.279–283) |
| `delete_model` (l.350) | **dans** la branche `if m.backend_type == "OLLAMA":` (l.355), avant `_ollama_delete` |
| `install_hf` (l.551) | `await _require_ollama(db)` après `_require_admin`, **avant** `_hf_get` (l.554) |

Non concernés (documenté) : `update_model` (l.330 — pas d'appel Ollama), `register_external` /
`test_external` (chemin `OPENAI_API`, base fournie par le client), `search_hf` (recherche
seulement), `list_models` / `list_shares`. **Garde « un import par projet » et lock modèle**
(`tasks/inference.py` l.214–220) : non concernés — le flag ne change ni l'ordonnancement
ni le verrouillage ; un job reste un job.

### 3.5 `docker-compose.yml` + `.env` / `.env.example`

Le service `ollama` est **déjà** sous `profiles: ["ollama"]` (compose l.44) → aucun changement
de profil. Le mode est porté par le couple, documenté (pas de magie) :

```env
# .env — section DEPLOIEMENT (à côté de COMPOSE_PROFILES)
OLLAMA_MODE=docker          # docker | host | off — flag lu par le backend/worker
COMPOSE_PROFILES=ollama     # docker : conteneur Ollama ; host/off : line absente/commentée
# --- host : Ollama déjà déployé sur la machine hôte ---
# OLLAMA_MODE=host
# COMPOSE_PROFILES=                     (pas de profil ollama)
# LLM_API_BASE_URL=http://host.docker.internal:11434/v1
# --- off : instance 100 % API clé ---
# OLLAMA_MODE=off
# COMPOSE_PROFILES=                     (pas de profil ollama)
```

Changements compose :

1. `backend` ET `worker` : `OLLAMA_MODE: ${OLLAMA_MODE:-docker}` ajouté à `environment`
   (à côté de `LLM_API_BASE_URL`, l.66 / l.104) — le flag doit atteindre les conteneurs
   qui l'exécutent ; défaut `docker` = invariant.
2. `backend` ET `worker` : `extra_hosts: ["host.docker.internal:host-gateway"]` —
   **nécessaire en Linux** pour joindre l'Ollama de la machine hôte depuis les conteneurs
   (le nom `host.docker.internal` n'existe pas nativement dans le network compose sous
   Linux ; `host-gateway` résout l'IP de l'hôte. Inoffensif/surabondant sous macOS où le
   nom existe déjà.)
3. `depends_on` : **aucun changement** — aucun service ne dépend de `ollama`
   (compose l.88–92, l.123–129, vérifié §2.3) ; retirer le profil est sans effet.
4. En-tête de comment (l.1–7) : compléter la doc des profils pour mentionner
   `OLLAMA_MODE` (le couple flag/profil, §3.5).
5. GPU hôte : blocs **commentés** sur le service `ollama` (§3.8).

`.env.example` : ajout d'une ligne `OLLAMA_MODE=docker` (commentée + commentaire des deux
variantes) sous la section « --- Modèles --- » près de `COMPOSE_PROFILES` (l.138) ;
`LLM_API_BASE_URL` (l.139) conserve son commentaire « à changer pour un autre serveur »
(ici : l'URL de l'Ollama hôte en mode `host`).

### 3.6 Comportement d'un modèle OLLAMA déjà enregistré en mode `off`

**Spécifié : le pipeline d'inférence n'est PAS modifié** (ponytail, hors périmètre §3.9).
Le model registry conserve la ligne (pas de purge, pas de migration) ; à la lancée :

- **Task inférence** (`tasks/inference.py` l.228–231) : `make_inference_service` pointe sur
  `llm_api_base_url` (compose, conteneur absent) → échec réseau →
  `InferenceError("connection_error")` (`inference/__init__.py` l.104–105) →
  **job `FAILED` propre** via `_record_failure` (l.576–586, message assaini, sans URL),
  lock modèle relâché (`finally` l.250–251), worker non affecté. C'est l'échec propre demandé.
- **Query-assistant** : 502 `model_error:connection_error` (l.56–62) — l'utilisateur voit
  une erreur de modèle, pas une erreur de déploiement ; le bandeau « Ollama non déployé /
  off » de la page Modèles explique.
- Les backends `OPENAI_API` restent opérationnels (chemin `infer_with` l.222–228,
  indépendant de `llm_api_base_url`).

Raison de ne pas ajouter de guard dans le pipeline : la lancée d'un job est un flux
existant avec sa sémantique d'erreur ; un guard dédié y créerait une seconde source de
vérité sur le mode et toucherait task + benchmark + query-assistant pour un cas que
l'UI avertit déjà (modèles OLLAMA annotés « non lançable », §3.5/§3.6 UI).

### 3.7 Frontend (plus petit périmètre)

`frontend/src/lib/api.ts` (l.1092–1105) :

```ts
export interface OllamaCapability {
  mode: "docker" | "host" | "off";
  configured: boolean;
  reachable: boolean;
}
export interface ModelCapabilities {
  ollama: OllamaCapability;   // remplace `ollama: Capability`
  hardware: …;                // inchangé
  recommendation: …;          // inchangé
}
```

`frontend/src/app/models/page.tsx` :

1. **Hint mode `off`** (l.137) : remplacer le hint `svcHow` par une chaîne dédiée quand
   `caps.ollama.mode === "off"` — `svcHow` (« Pour l'activer : COMPOSE_PROFILES=ollama… »)
   est un conseil d'activation, inadapté à un mode délibérément off.
   Nouvelle clé i18n `svcHowOff` (4 fichiers `messages/*.json`), ex. fr :
   « Ollama désactivé par `OLLAMA_MODE=off` — backends API clé uniquement. ».
2. **Modèles OLLAMA du registre « non lançables »** (l.187–224) : quand
   `caps?.ollama.mode === "off"` et `m.backend_type === "OLLAMA"`, afficher un petit badge
   / mention muted (« non lançable — Ollama off ») sur la ligne. Nouvelle clé i18n
   `offBadge`. (`backend_type` déjà disponible : api.ts l.217.)
3. **Déjà au propre (documenté, aucun changement)** : le bloc HfSearch (l.174–178) est
   masqué par `caps?.ollama.configured` ; les boutons « installer » des modèles
   recommandés (l.161) par `caps.ollama.reachable` ; le badge service (l.133–134) affiche
   `svcOff` ; `listOllama()` (l.51) reçoit le 409 en mode off et l'absorbe (l.54–56) →
   `detected` vide. Amélioration *optionnelle* (hors périmètre si non trivial) : ne pas
   appeler `listOllama()` quand `caps.ollama.configured === false`.
4. Suppression d'un modèle OLLAMA en mode off : le 409 `ollama_disabled_by_deployment`
   remonte dans l'`ErrorBanner` (l.108) — acceptable, le message est précis ; le badge
   `offBadge` (§3.7.2) prépare l'utilisateur.

### 3.8 GPU sur la machine hôte (documentation — pas de détection automatique)

Le flag `OLLAMA_MODE` ne change rien à la façon dont Ollama voit le GPU : c'est le
**déploiement Ollama** qui expose le device. Serv-ics-testing est CPU-only → hors périmètre
d'exécution (documenté, non testé).

- **Mode `docker`** : c'est le service `ollama` du compose qui doit exposer le device à son
  conteneur. Le service doit prévoir ces options **commentées** (l'opérateur décommente
  selon le matériel — pas de détection automatique) :

  ```yaml
  # ollama:
  #   GPU — décommenter selon le matériel (docs.ollama.com/docker) :
  #   # NVIDIA (nvidia-container-toolkit requis sur l'hôte) :
  #   deploy:
  #     resources:
  #       reservations:
  #         devices:
  #           - driver: nvidia
  #             count: all
  #             capabilities: [gpu]
  #   # Intel (Arc/iGPU) ou AMD en Vulkan — image standard, Vulkan bundled et actif
  #   # si le device est accessible (OLLAMA_VULKAN=0 pour désactiver) :
  #   devices:
  #     - /dev/dri
  #     - /dev/kfd        # AMD uniquement
  #   # AMD ROCm : tag d'image ollama/ollama:rocm + mêmes devices.
  ```

- **Mode `host`** : le GPU est celui détecté par l'installation Ollama de la machine hôte —
  **aucune exposition nécessaire** dans le compose. C'est l'argument pour recommander
  `host` quand l'hôte n'est pas prêt pour le GPU passthrough (pas de
  `nvidia-container-toolkit`, ACLs udev non configurées, …).
- **Observabilité du mode GPU** : le log Ollama (`inference compute` / `total_vram`) est la
  source de vérité ; **la capabilities API n'expose pas le VRAM et aucun endpoint VRAM n'est
  ajouté** (les `hardware.*` / `recommendation` restent le profil du *serveur de l'app*,
  pas celui d'Ollama).

### 3.9 Invariants et hors périmètre

- **Mode `docker` strictement invariant** : `OLLAMA_MODE` absent ou `docker` → mêmes URLs,
  même ping (`_reachable` l.148–157), mêmes codes HTTP (502 `ollama_unreachable` /
  `ollama_delete_failed` conservés), même corps de requête ; le champ `mode: "docker"` est
  l'unique delta observable (capabilities).
- **Aucun changement du pipeline d'inférence** : `inference/__init__.py`, `tasks/*`,
  `query_assistant.py` intacts ; `extra_body` (`llm_extra_body`) reste appliqué aux
  backends locaux quel que soit le mode (`make_inference_service` l.167–175).
- **Pas de nouveau setting DB, pas de migration** : rien n'entre dans `settings/keys.py`
  (`KNOWN_KEYS` inchangé), pas de table, pas d'alembic.
- Hors périmètre : purge des modèles OLLAMA du registre en mode `off` ; blocage de la
  *lancée* de job OLLAMA côté API de soumission ; VRAM dans la capabilities ; détection
  automatique du GPU ; tout changement de l'UI au-delà de §3.7 ; le fichier `.env` réel
  (modifié par l'opérateur, pas par le code).

---

## 4. Tests à écrire (TDD)

100 % offline (`httpx.MockTransport`, SQLite in-memory via `client`/`session` de conftest).
Deux fichiers : `backend/tests/test_ollama_mode.py` (nouveau — flag) et
`backend/tests/test_models_api.py` (extensions).

**Fixtures / helpers à ajouter** :

- `monkeypatch.setattr(m, "MODE", "off")` où `from app.api.v1 import models as m`
  (pattern `monkeypatch.setattr(m, "_reachable", …)` — capabilities l.424–429) ; le mode
  par défaut des tests existants est `docker` (env non définie).
- Pas de ping : `monkeypatch.setattr(m, "_reachable", fake_reachable_leve_assertion)` —
  `async def _no(url, path=""): raise AssertionError("ping interdit en mode off")`
  (pattern AssertionError des handlers, l.233–234).
- Base custom en mode `host` : `await set_setting(session, "llm_api_base_url",
  "http://10.0.0.5:11434/v1")` (pattern `test_inference.py` l.210+) — le `client` partage
  la session (conftest l.118–121).
- Absence d'appel Ollama : handler `raise AssertionError("Ollama ne doit pas être appelé")`
  (l.258–259) ; absence d'appel HF : `monkeypatch.setattr(m, "_hf_get", fake_levante)` (l.306).

### 4.1 Lecture du flag — `backend/tests/test_ollama_mode.py`

| # | Test | Assertion clé | Fixture |
|---|---|---|---|
| 1 | `test_ollama_mode_absent_defaults_docker` | `monkeypatch.delenv("OLLAMA_MODE", raising=False)` → `resolve_mode() == "docker"` ET le module importé expose `MODE == "docker"` | `monkeypatch` |
| 2 | `test_ollama_mode_invalid_defaults_docker_with_warning` | `OLLAMA_MODE="banana"` → `resolve_mode() == "docker"` ET `caplog` contient un warning mentionnant `banana` ; `OLLAMA_MODE=""` → `"docker"` **sans** warning (cas empty ≠ invalid) | `monkeypatch.setenv` + `caplog` |
| 3 | `test_ollama_mode_case_and_space_normalized` | `OLLAMA_MODE=" HOST "` → `"host"` ; `"Off"` → `"off"` (strip + lower) | `monkeypatch.setenv` |

### 4.2 Capabilities — `backend/tests/test_models_api.py`

| # | Test | Assertion clé | Fixture |
|---|---|---|---|
| 4 | `test_capabilities_docker_mode_default_invariant` | sans `OLLAMA_MODE`, `_reachable` → True : `caps["ollama"] == {"mode": "docker", "configured": True, "reachable": True}` (défaut `localhost:11434`) | `client`, `do_login`, `monkeypatch.setattr(m, "_reachable", fake_true)` |
| 5 | `test_capabilities_host_mode_custom_url_configured` | `MODE="host"` + `set_setting("llm_api_base_url", "http://10.0.0.5:11434/v1")`, `_reachable` → True : `{"mode": "host", "configured": True, "reachable": True}` | idem + `set_setting` |
| 6 | `test_capabilities_host_mode_compose_default_not_configured` | `MODE="host"` + base résiduelle `http://ollama:11434/v1` : `{"mode": "host", "configured": False, "reachable": False}` ET `_reachable` non appelé (fake qui lève AssertionError) | idem + `_reachable` levant |
| 7 | `test_capabilities_off_mode_no_ping` | `MODE="off"` : `{"mode": "off", "configured": False, "reachable": False}` ET `_reachable` non appelé (levant AssertionError) — **aucun ping en mode off** | `client`, `do_login`, `monkeypatch` |

### 4.3 Mode `off` — refus des endpoints Ollama (409 + detail)

| # | Test | Assertion clé | Fixture |
|---|---|---|---|
| 8 | `test_off_list_ollama_409` | `GET /models/ollama` → 409, `detail == "ollama_disabled_by_deployment"` ; handler Ollama non appelé | `MODE="off"`, `do_login` admin, handler AssertionError |
| 9 | `test_off_pull_ollama_409_and_no_state` | `POST /models/ollama/pull {"name":"qwen3:4b"}` → 409 même detail ; `m._PULLS == {}` (rien lancé) | idem |
| 10 | `test_off_register_ollama_409` | `POST /models/ollama/register {"name":"m:1"}` → 409 même detail ; `GET /models` → items vides | idem |
| 11 | `test_off_create_ollama_409_but_openai_api_still_201` | `POST /models {"backend_type":"OLLAMA", …}` → 409 même detail ; PUIS `POST /models/external {"name":"GPT","base_url":"https://api.x/v1","model":"gpt-4o"}` → **201** (les backends API clé restent opérationnels) | idem |
| 12 | `test_off_delete_ollama_model_409_keeps_row` | modèle OLLAMA seedé, `DELETE /models/{id}` → 409 même detail ; ligne conservée (`GET /models` le liste) ; handler Ollama non appelé (suppression refusée AVANT tout appel) | idem + seed `ModelRegistry` via `session` |
| 13 | `test_off_hf_install_409_before_hf_call` | `POST /models/hf/install {"repo":"a/b"}` → 409 même detail ; `_hf_get` non appelé (fake qui lève AssertionError) ; `m._PULLS == {}` | idem + `monkeypatch.setattr(m, "_hf_get", …)` |

### 4.4 Mode `host` avec base résiduelle compose — comportement specifié (§3.3/§3.4)

| # | Test | Assertion clé | Fixture |
|---|---|---|---|
| 14 | `test_host_compose_default_endpoints_refused` | `MODE="host"` + base résiduelle : `GET /models/ollama` → 409 `ollama_base_url_is_compose_default` ; `POST /models/ollama/register` → 409 même detail ; handler Ollama non appelé | `MODE="host"`, admin, handler AssertionError |
| 15 | `test_host_compose_default_url_variants_detected` | `_ollama_base` dérive : avec `set_setting` à `http://ollama:11434/v1/`, `http://ollama:11434` et `http://ollama:11434/v1` → `capabilities` : `configured is False` dans les 3 cas (comparaison post-dérive, §3.3) | `MODE="host"`, `set_setting` ×3 |

### 4.5 Tests existants — verts, avec le seul ajustement suivant

- **Uniquement** `test_capabilities_reports_ollama_hardware_and_recommendation`
  (l.420–448) : l'assertion l.444 passe de
  `caps["ollama"] == {"configured": True, "reachable": True}` à
  `{"mode": "docker", "configured": True, "reachable": True}` (le champ `mode` gagné,
  comportement inchangé).
- À l'identique (mode par défaut `docker` → guard transparent) :
  `test_create_model_admin` (l.24), `test_list_models` (l.41), `test_create_model_duplicate_409`
  (l.50), `test_create_model_non_admin_403` (l.57), `test_create_model_bad_backend_422` (l.63),
  `test_list_models_researcher_with_project` (l.71), `test_list_models_researcher_sees_only_accessible`
  (l.78), `test_update_model_admin` (l.90), `test_update_model_404` (l.103),
  `test_delete_model_admin` (l.111), `test_delete_ollama_model_calls_ollama_delete_then_removes_row`
  (l.130), `test_delete_ollama_model_missing_on_ollama_still_removes_row` (l.159),
  `test_delete_ollama_model_ollama_http_error_502_keeps_row` (l.182 — le 502
  `ollama_delete_failed` est conservé : le guard ne s'active qu'en `off`/`host` mal configuré),
  `test_delete_ollama_model_network_error_502_keeps_row` (l.206),
  `test_delete_ollama_model_no_call_for_openai_api_backend` (l.230),
  `test_delete_ollama_model_forbidden_before_ollama_call` (l.253),
  `test_ollama_register_admin_only_and_idempotent` (l.278),
  `test_hf_search_files_and_install_validation` (l.294),
  `test_hf_decision_models_flagged_and_not_installable` (l.318),
  `test_hf_files_sorted_for_cpu` (l.343), `test_register_external_encrypts_key` (l.363),
  `test_infer_with_uses_model_url_and_key` (l.390), `test_recommend_by_resources` (l.451).
- Hors fichier, non affecté : `test_access_control.py` l.180–184 (admin `register_external`
  vers URL interne → 201) — le guard ne porte que sur les routes OllAMA.

**Total : 15 tests neufs (§4.1–4.4) + 1 assertion ajustée + ~23 tests existants verts.**

---

## 5. Critères d'acceptation

1. **Flag** : `OLLAMA_MODE` lu une fois à l'import (`backend/app/ollama_mode.py`) ;
   absent → `docker` ; `docker`/`host`/`off` normalisés (casse + espaces) ; invalide →
   `docker` + warning loggé. Aucune entrée dans `settings/keys.py`, aucun setting DB,
   aucune migration.
2. **Capabilities** : `GET /models/capabilities` renvoie
   `ollama = {mode, configured, reachable}` ; mode `off` → `{off, false, false}` SANS ping ;
   mode `host` + base résiduelle compose → `{host, false, false}` + warning ;
   mode `docker` → réponse de même forme qu'avant (ping `http://…/api/tags` inclus).
3. **Refus mode `off`** : `GET /models/ollama`, `POST /models/ollama/pull`,
   `POST /models/ollama/register`, `POST /models/hf/install`,
   `POST /models` (backend_type `OLLAMA`), `DELETE /models/{id}` (modèle `OLLAMA`) →
   **409 `ollama_disabled_by_deployment`**, aucune mutation d'état (`_PULLS`, lignes DB),
   aucun appel HTTP Ollama ni HF ; les modèles `OPENAI_API` restent créables (201) et
   supprimables (204).
4. **Mode `host`** : avec une base non-compose (`LLM_API_BASE_URL` = Ollama hôte),
   toutes les routes Ollama fonctionnent contre cette base (mêmes 502 existants si
   injoignable) ; avec la base résiduelle compose → 409
   `ollama_base_url_is_compose_default` + capabilities `configured=false`.
5. **Compose/env** : `OLLAMA_MODE=${OLLAMA_MODE:-docker}` présent sur `backend` ET
   `worker` ; `extra_hosts: ["host.docker.internal:host-gateway"]` sur les deux ; service
   `ollama` inchangé en comportement (profil `ollama` déjà présent, GPU options commentées) ;
   `depends_on` inchangés ; `.env.example` documente le couple `OLLAMA_MODE`/`COMPOSE_PROFILES`
   avec les deux variantes commentées.
6. **Frontend** : `ModelCapabilities.ollama` porte `mode` ; en mode `off`, le hint
   `svcHowOff` remplace `svcHow`, les modèles OLLAMA du registre sont annotés « non
   lançable » ; HfSearch / boutons installer restent masqués par `configured`/`reachable`
   (comportement existant). 4 fichiers i18n à jour.
7. **Pipeline** : zéro ligne modifiée dans `inference/__init__.py`, `tasks/*`,
   `query_assistant.py` ; un modèle OLLAMA du registre en mode `off` échouant à la lancée
   produit un job `FAILED` assaini (`connection_error`) — testé par les tests existants
   de task (non modifiés).
8. **Garde d'invariant** : `OLLAMA_MODE` absent → la suite complète de
   `test_models_api.py` (hors le champ `mode` ajouté) et les tests d'inférence existants
   passent sans modification de fixture ; aucun comportement HTTP observé ne change.

## 6. Contraintes (ponytail)

- **Le plus petit périmètre** : un module flag de ~20 lignes, un helper `_require_ollama`
  à 6 points d'appel, un champ dans capabilities, 2 clés i18n. Pas de refactor, pas de
  service de déploiement, pas de « détection intelligente » (pas de sondage de
  `http://ollama:11434` pour deviner le mode).
- **Pas de setting DB** : `KNOWN_KEYS`/`NON_SECRET_KEYS` de `settings/keys.py` inchangés ;
  le flag n'apparaît ni dans l'API settings ni dans l'UI Paramètres.
- **Pas de changement du pipeline d'inférence** (ni du lock modèle, ni du warm-up, ni du
  corps `extra_body`) : les seules surfaces modifiées sont `models.py` (routes de gestion),
  le compose, `.env.example`, le frontend page Modèles et leurs tests.
- **Le mode `docker` est strictement invariant** : le champ `mode: "docker"` dans
  capabilities est l'unique diff observable quand le flag est absent ; les tests existants
  (l.111–360 de `test_models_api.py`) verrouillent cet invariant sans changement de fixture.
- **409, pas 422/403, pour le refus de mode** : conflit configuration de déploiement,
  détail machine lisible (`ollama_disabled_by_deployment`,
  `ollama_base_url_is_compose_default`) — mêmes familles que les 409 existants (l.243, l.283,
  l.561).
- **GPU** : aucune détection automatique, aucun endpoint VRAM ; documentation + options
  commentées dans le compose (§3.8) ; observabilité par le log Ollama uniquement.
