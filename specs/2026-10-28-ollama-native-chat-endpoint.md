# Spec TDD — Backend Ollama local via l'endpoint natif `POST /api/chat`

**Date :** 2026-10-28
**Repo :** diggypaper (FastAPI + Celery + Next.js)
**Périmètre de code :** `backend/app/inference/__init__.py` + tests `backend/tests/test_inference.py`
**Statut :** spec TDD à implémenter — aucun code d'implémentation complet ci-dessous, uniquement signatures et extraits de référence

---

## 1. Contexte — résultat empirique établi

Mesuré sur **Ollama 0.40.3** (conteneur compose) :

Le réglage `llm_extra_body` (ex. `{"think":false,"options":{"num_ctx":16384}}`) est **bien transmis** par
l'app dans le corps de `/v1/chat/completions` (corps capturé in-process : `options.num_ctx=16384` présent).
MAIS l'endpoint OpenAI-compat d'Ollama **ignore** `options.num_ctx` :

| Appel (options identiques : `{"num_ctx": 16384}`) | Log Ollama |
|---|---|
| `POST /v1/chat/completions` | `n_ctx_slot = 4096` (défaut CPU sans VRAM) |
| `POST /api/chat` (natif) | `n_ctx_slot = 16384` (appliqué) |

Conséquence constatée en production (E2E) : prompt tronqué à 2050 tokens
(`truncating input prompt limit=2050 prompt=4472`) au lieu de 16384 → inférence dégradée.

**Objectif :** quand le backend est **OLLAMA** (local), l'app doit appeler l'endpoint natif
`POST {root}/api/chat` (root = base_url sans le suffixe `/v1`) avec les `options` dans le corps natif.
Les backends **OPENAI_API** (externes, API clé) gardent strictement `/v1/chat/completions` (invariant).

### 1.1 Reproduction (origine du bug)

Deux commandes de test (Ollama 0.40.3, conteneur compose), puis lecture du log :

```bash
# 1) Endpoint OpenAI-compat : options.num_ctx IGNORÉ → n_ctx_slot = 4096
curl -sS http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:4b","stream":false,"messages":[{"role":"user","content":"ping"}],"options":{"num_ctx":16384}}'

# 2) Endpoint natif : options.num_ctx APPLIQUÉ → n_ctx_slot = 16384
curl -sS http://localhost:11434/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:4b","stream":false,"messages":[{"role":"user","content":"ping"}],"options":{"num_ctx":16384}}'

# Lecture du log du conteneur Ollama :
docker compose logs ollama 2>&1 | grep "n_ctx_slot"
```

Attendu (observé) : `n_ctx_slot = 4096` pour l'appel `/v1`, `n_ctx_slot = 16384` pour `/api/chat`.

---

## 2. État actuel (références fichier:ligne)

### 2.1 `backend/app/inference/__init__.py`

- `InferenceError` (l.25–26) : exception assainie (message court, sans URL ni secret).
- `OpenAICompatibleService.__init__` (l.32–47) :
  - `self._base_url = base_url.rstrip("/")` (l.42) — le slash final est déjà supprimé à la construction ;
  - `self._extra_body = extra_body or {}` (l.47).
- `OpenAICompatibleService.infer` (l.49–98) :
  - headers l.55–57 : `Content-Type` + `Authorization: Bearer {api_key}` si `api_key` présente ;
  - corps fixe l.58–66 : `{"model", "messages": [system, user], "temperature": 0.0, "response_format": {"type": "json_object"}}` ;
  - merge l.67 : `body = {**self._extra_body, **body}` — **les clés fixes priment** ;
  - URL l.68 : `url = f"{self._base_url}/chat/completions"` ;
  - `public_only` l.69–78 : `check_public_url` ré-vérifié à chaque appel (inchangé, appliqué aux deux modes) ;
  - erreurs : `httpx.TimeoutException` → `InferenceError("timeout")` (l.84–85) ; `httpx.HTTPError` → `InferenceError("connection_error")` (l.86–87) ; `resp.status_code >= 400` → `InferenceError(f"HTTP {resp.status_code}")` (l.88–89) ;
  - parse l.90–91 : `data = resp.json()` puis `content = data["choices"][0]["message"]["content"]` ;
  - l.92–95 : `json.loads(content)`, sinon `InferenceError("invalid_json")` (catch `JSONDecodeError, TypeError`) ;
  - l.96–98 : `finally` → `client.aclose()` si client créé par le service.
- `unload_ollama` (l.101–112) : **pattern existant de dérivation `root`** —
  `root = base_url.rstrip("/").removesuffix("/v1")` (l.103), `POST {root}/api/generate` (l.107).
- `make_inference_service` (l.115–150) :
  - `base_url` depuis `get_setting(db, "llm_api_base_url")` (l.128 ; défaut `http://localhost:11434/v1`,
    voir `backend/app/settings/keys.py` l.43) ;
  - `api_key` (l.129) ; `timeout` (l.130–136, défaut 120 s) ;
  - `extra_body` l.137–147 : réglage `llm_extra_body`, **uniquement hors OPENAI_API** —
    condition l.139 : `model is None or model.backend_type != "OPENAI_API"` ; JSON non-dict → corps vide + warning ;
  - instanciation l.148–150 : `OpenAICompatibleService(base_url=…, api_key=…, http=…, timeout=…, extra_body=…)` —
    **aucun flag natif aujourd'hui**.
- `warmup` (l.153–171) : passe par `service.infer(model_identifier, …)` (l.165) — même service, même méthode.
- `infer_with` (l.174–194) :
  - branche OPENAI_API l.186–192 : construit son propre `OpenAICompatibleService(url, key, timeout=…, public_only=…)`
    (l.189–191) — sans `extra_body`, sans flag natif ;
  - sinon l.194 : `return await default.infer(ident, system_prompt, user_prompt)`.

### 2.2 `backend/app/tasks/inference.py` (task inférence massive)

- Imports l.50–56 (`InferenceError`, `OpenAICompatibleService`, `infer_with`, `make_inference_service`, `warmup`).
- `_run_inference` (l.151+) : `inference` injectable (l.155) ; si absent →
  `make_inference_service(session, http=client, model=main_model)` (l.228–230) — **le modèle du registre détermine le service**.
- Warm-up l.423–428 : `await warmup(inference, ident)` (l.428) — **même service que l'appel principal**.
- Appel principal : `predict` → `infer_with(model, inference, system, user_prompt, …)` (l.439) → `default.infer` (l.194 du service).
- **Confirmé : le warm-up ET l'appel principal passent tous les deux par `inference.infer(...)`** (directement via
  `warmup` → `service.infer`, et via `infer_with` → `default.infer`). Aucun autre chemin HTTP dans la task.
- Tolérance `invalid_json` l.472–479 : seule la valeur exacte `"invalid_json"` ne tue pas le job
  (l.475 : `if str(exc) != "invalid_json": raise`) ; `timeout`/`connection_error`/`HTTP *` restent fatals.

### 2.3 Audit des autres appelants — aucun ne dépend du format /v1 de la réponse

Tous les consommateurs n'utilisent que le **dict renvoyé** par `infer()` (le format de réponse est interne au service) :

| Appellant | Références | Constat |
|---|---|---|
| `backend/app/tasks/benchmark.py` | `make_inference_service(session, http=client)` **sans modèle** (l.124) ; `infer_with` (l.233) ; `unload_ollama` (l.315) ; tolérance `invalid_json` (l.242–244) | consomme le dict ; service par défaut → concerné par le flag natif via `model=None` (§3.1) |
| `backend/app/api/v1/query_assistant.py` | `make_inference_service(db)` **sans modèle** (l.54) ; `infer_with(models[0], service, …)` (l.58) ; retry `invalid_json` (l.61) | idem — un modèle OLLAMA du registre passe par le service par défaut |
| `backend/app/api/v1/models.py` | `test_external` : `OpenAICompatibleService(payload.base_url, payload.api_key, timeout=30, public_only=…)` (l.300–304) | test d'endpoint **externe OpenAI-compat** → doit rester /v1 ; flag absent → défaut `False` → inchangé |
| `backend/app/inference/__init__.py` | branche OPENAI_API de `infer_with` (l.189–191) | construction directe sans flag → /v1 inchangé |

`backend/tests/test_inference_task.py` : les tests injectent `FakeInference` (l.28–46) — un mock de `infer`
renvoyant un dict fixe (`{"confidence_score": …, …}`) : **aucune dépendance au format /v1** → pas de changement requis.

### 2.4 Tests existants du service — `backend/tests/test_inference.py`

- 100 % offline : `httpx.MockTransport` ; helpers `_llm_handler` (l.16–27, fabrique une réponse **/v1**
  `{"choices":[{"message":{"content":…}}]}`), `_client` (l.30–31), `_capture_client` (l.159–166, capture du corps).
- Fixture `session` (SQLite in-memory, `backend/tests/conftest.py` l.52–57) ; `asyncio_mode = "auto"`
  (`backend/pyproject.toml` l.43) ; modèles simulés via `SimpleNamespace(backend_type=…)` (pattern l.210–249) ;
  réglages via `set_setting` (`app.settings.service`).
- Listing (nom → ce que le test couvre) :

| Test (ligne) | Couvre |
|---|---|
| `test_infer_returns_parsed_json` (l.45) | parse /v1 `choices[0].message.content` → dict |
| `test_infer_invalid_json_raises` (l.53) | contenu non-JSON → `invalid_json` |
| `test_infer_timeout_raises` (l.60) | `httpx.ReadTimeout` → `timeout` |
| `test_infer_http_error_sanitized` (l.70) | HTTP 500 → `HTTP 500`, sans URL ni secret |
| `test_infer_sends_bearer_and_body` (l.82) | header Bearer + corps /v1 (model, messages, temperature) |
| `test_make_inference_service_from_settings` (l.99) | réglages DB → `_base_url`, `_api_key` |
| `test_make_inference_service_defaults` (l.109) | défaut `http://localhost:11434/v1`, pas de clé |
| `test_make_inference_service_timeout_from_settings` (l.118) | timeout réglable (300) |
| `test_make_inference_service_timeout_invalid_falls_back` (l.126) | timeout invalide → 120 s |
| `test_warmup_calls_infer_with_model` (l.134) | warm-up → `infer(model=…)` |
| `test_warmup_absorbs_failure` (l.146) | échec warm-up → pas d'exception |
| `test_infer_no_extra_body_unchanged` (l.169) | verrou de régression : corps /v1 exact sans extra_body |
| `test_infer_with_extra_body_merged` (l.185) | `think` + `options` fusionnés, clés fixes préservées |
| `test_infer_extra_body_cannot_override_fixed_keys` (l.200) | `temperature`/`model` non écrasés par extra_body |
| `test_make_service_ollama_applies_extra_body_from_setting` (l.210) | OLLAMA → `llm_extra_body` appliqué |
| `test_make_service_openai_api_never_applies_extra_body` (l.223) | OPENAI_API → extra_body jamais appliqué |
| `test_make_service_invalid_extra_body_is_empty` (l.236) | JSON invalide → corps vide |
| `test_make_service_null_extra_body_is_empty` (l.246) | réglage absent → corps vide |

---

## 3. Spécification

### 3.1 Détecteur de mode natif (ponytail : le plus petit périmètre)

**Décision : un flag booléen `native_ollama` sur le service existant `OpenAICompatibleService`, pas une nouvelle
classe** — justification en une phrase : les seules différences entre les deux modes sont l'URL, les clés fixes du
corps (`format`/`stream` au lieu de `response_format`) et le parse du contenu, soit trois points de branchement dans
`infer()` ; une sous-classe ou une seconde classe multiplierait l'API sans rien encapsuler de plus.

- Le flag est **fixé à la construction** (pas de détection par URL) ; les tests construisent le service avec le flag
  directement.
- Valeur par défaut : `False` → **toutes les constructions existantes sont inchangées** (l.189–191 de
  `infer_with`, l.300–304 de `models.py`, tous les tests /v1 existants).

Signature exacte du constructeur (seul paramètre ajouté) :

```python
def __init__(
    self,
    base_url: str,
    api_key: str | None = None,
    timeout: float = 120.0,
    http: httpx.AsyncClient | None = None,
    public_only: bool = False,
    extra_body: dict | None = None,
    native_ollama: bool = False,  # NOUVEAU — défaut False : constructions existantes inchangées
) -> None:
```

`make_inference_service` (l.148–150) calcule et passe le flag :

```python
native_ollama = model is None or model.backend_type == "OLLAMA"
return OpenAICompatibleService(
    base_url=base_url, api_key=api_key, http=http, timeout=timeout,
    extra_body=extra_body, native_ollama=native_ollama,
)
```

**Décision sur `model is None` (documentée, à verrouiller par test) :** la fabrique sans modèle construit le service
par défaut qui pointe sur `llm_api_base_url` — Ollama par défaut (`http://localhost:11434/v1`,
`settings/keys.py` l.43). La condition « backend local » existante (l.139 : `model is None or
model.backend_type != "OPENAI_API"`) traite déjà `model is None` comme local ; la règle du flag suit la même
sémantique, restreinte à OLLAMA. Conséquence voulue : le query-assistant (`query_assistant.py` l.54) et le
benchmark (`benchmark.py` l.124), qui appellent la fabrique **sans modèle**, appellent aussi `/api/chat` pour Ollama —
sinon le bug num_ctx persisterait sur ces chemins. Un modèle local explicite **non-OLLAMA**
(`LLAMA_CPP`, `TRANSFORMERS`, `SYSTEMONE`, …) garde `/v1` : leur serveur n'expose pas `/api/chat`
(flag `False`, régression verrouillée par test).

### 3.2 Cible et corps natif (mode `native_ollama=True`)

**URL** (même dérivation que `unload_ollama`, l.103) :

```python
root = self._base_url.removesuffix("/v1")  # self._base_url est déjà rstrip("/") (l.42)
url = f"{root}/api/chat"
```

Cas de dérivation :

| `base_url` (paramètre) | `_base_url` (l.42) | `root` | URL finale |
|---|---|---|---|
| `http://ollama:11434/v1` | `http://ollama:11434/v1` | `http://ollama:11434` | `http://ollama:11434/api/chat` |
| `http://ollama:11434/v1/` (slash final) | `http://ollama:11434/v1` | `http://ollama:11434` | `http://ollama:11434/api/chat` |
| `http://ollama:11434` (sans /v1) | `http://ollama:11434` | `http://ollama:11434` | `http://ollama:11434/api/chat` |
| `http://ollama:11434/` (sans /v1, slash final) | `http://ollama:11434` | `http://ollama:11434` | `http://ollama:11434/api/chat` |

**Corps natif** (extrait de référence) :

```python
body = {
    "model": model,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    "stream": False,
    "temperature": 0.0,
    "format": "json",
}
body = {**self._extra_body, **body}  # clés fixes priment — même sémantique que l.67
```

- `extra_body` est fusionné **en racine** (clés `options`, `think`) — c'est là que Ollama natif les lit
  (résultat empirique §1).
- `response_format` (`json_object`) **n'est PAS envoyé** en mode natif : remplacé par `format: "json"`.
- Merge : même ordre que l.67 (`{**extra_body, **fixes}`) — une clé fixe (`model`, `messages`, `stream`,
  `temperature`, `format`) ne peut JAMAIS être écrasée par `llm_extra_body` ; une clé libre (`options`, `think`) l'est.
- Headers l.55–57 **inchangés** : `Authorization: Bearer {api_key}` si `api_key` présente — comportement uniforme
  (Ollama local n'en a pas, mais le service reste agnostique).
- Vérification `public_only` l.69–78 : inchangée, s'applique aux deux modes (elle porte sur `self._base_url`).

### 3.3 Parse de la réponse natif

`/api/chat` répond (non-streaming) : `{"message": {"role": "assistant", "content": "…"}, "done": true, …}` —
le contenu est `data["message"]["content"]` (**pas** `data["choices"][…]`).

Extrait de référence (le chemin /v1 — l.88–95 — reste lettre pour lettre ; seul le
choix de `content` est branché) :

```python
if resp.status_code >= 400:
    raise InferenceError(f"HTTP {resp.status_code}")
data = resp.json()                                          # l.90 inchangé (les 2 modes)
if self._native_ollama:
    try:
        content = data["message"]["content"]
    except (KeyError, TypeError) as exc:                    # "message" absent/None ou "content" absent
        raise InferenceError("invalid_response") from exc
else:
    content = data["choices"][0]["message"]["content"]      # l.91 inchangé
try:
    return json.loads(content)
except (json.JSONDecodeError, TypeError) as exc:
    raise InferenceError("invalid_json") from exc          # l.92–95 inchangés
```

Erreurs — **mêmes messages que le mode /v1** (sans URL, sans secret) :

| Situation | Message | Origine |
|---|---|---|
| `status_code >= 400` | `HTTP {code}` (ex. `HTTP 500`) | l.88–89 (commun) |
| `httpx.TimeoutException` | `timeout` | l.84–85 (commun) |
| `httpx.HTTPError` | `connection_error` | l.86–87 (commun) |
| corps de réponse non-JSON | `httpx.InvalidJSONError` (propagation brute) | **identique au /v1 actuel** (l.90 hors du try/except) — comportement existant non modifié, hors périmètre |
| `message` ou `content` absent/malformé | `invalid_response` | **nouveau, mode natif seulement** (catch `KeyError, TypeError`) |
| `content` non-JSON | `invalid_json` | l.92–95 (commun) |

Note de sémantique (documentée) : `invalid_response` n'a **pas** le
statut tolérant de `invalid_json` — la task ne tolère que `"invalid_json"` exactement (`tasks/inference.py` l.475) ;
une enveloppe de réponse malformée (Ollama mal configuré / pointé ailleurs) est fatale pour le job, comme
`timeout`/`HTTP *` (pattern de test de régression : `test_run_inference_failure_marks_failed`, l.210–241).

### 3.4 Invariants

- **Mode OPENAI_API strictement inchangé** : URL `{base_url}/chat/completions`, corps avec
  `response_format: {"type": "json_object"}`, parse `choices[0]["message"]["content"]`, `llm_extra_body` jamais
  appliqué (l.139), service construit sans flag natif (`infer_with` l.189–191 ; `models.py` l.300–304).
- **Branche /v1 de `infer` (l.58–98) : zéro ligne modifiée**, y compris le cas limite `resp.json()` non enveloppé
  (l.90) — l'invariant porte sur le comportement, pas sur la parité des cas limites entre modes.
- **`llm_extra_body` reste le SEUL point de réglage du corps** (num_ctx, think) : aucun nouveau setting DB/env
  pour cette spec.
- **Warm-up, lock modèle, timeout : non concernés.** Le warm-up (`tasks/inference.py` l.428) passe par le même
  service → il utilise automatiquement `/api/chat` pour Ollama (effet de bord inoffensif : il absorbe le cold start
  de la même façon) ; le lock par modèle (l.206–220) et le timeout (l.130–136) ne dépendent ni de l'URL ni du corps.
- `unload_ollama` (l.101–112) : inchangé (elle sert de modèle de dérivation, pas de cible de changement).
- `query_assistant.py` et `benchmark.py` : inchangés en code — ils bénéficient du flag via
  `make_inference_service(..., model=None)` (§3.1).

### 3.5 Hors périmètre

- Pas de changement des endpoints de gestion de modèles (`/models/*`, pull, register — ils utilisent déjà l'API
  native `/api/*`, cf. `_ollama_base` `models.py` l.119–122).
- Pas de migration, pas de frontend.
- Pas de streaming (`stream` est toujours `false` dans le corps natif).
- Pas de nouvelle classe, pas de nouveau setting, pas de détection par URL.

---

## 4. Tests à écrire (TDD)

100 % offline (`httpx.MockTransport`), dans `backend/tests/test_inference.py`.

**Fixtures / helpers à ajouter** :

- `_native_handler(content, status=200)` — analogue de `_llm_handler` (l.16–27) : renvoie
  `httpx.Response(status, json={"message": {"role": "assistant", "content": content}, "done": True})`
  (ou `{"error": "boom"}` si `status != 200`).
- Capture URL + corps : `captured["url"] = str(request.url)` et `captured["body"] = json.loads(request.content)`
  (pattern existant : `_capture_client` l.159–166 + `test_infer_sends_bearer_and_body` l.82–97).
- Fixture `session` (conftest l.52–57) + `set_setting` (`app.settings.service`) ; modèles simulés
  `SimpleNamespace(backend_type=…)` (pattern l.210–249). `asyncio_mode = "auto"` (pyproject l.43).

### 4.1 Tests neufs — mode natif (service)

| # | Test | Assertion clé | Fixture |
|---|---|---|---|
| 1 | `test_infer_native_ollama_hits_api_chat` | URL requise == `http://llm.test/api/chat` (plus rien sur `/v1/`) | `OpenAICompatibleService("http://llm.test/v1", native_ollama=True, http=_client(_native_handler("{}")))` + capture `request.url` |
| 2 | `test_infer_native_body_exact_without_extra_body` | corps == `{"model": "Kev-4B", "messages": [sys, usr], "stream": False, "temperature": 0.0, "format": "json"}` ET `"response_format" not in body` | service natif sans extra_body + `_capture_client` (adapté pour l'URL) |
| 3 | `test_infer_native_body_merges_extra_body_options_think` | `body["options"] == {"num_ctx": 16384}`, `body["think"] is False`, `body["format"] == "json"` | service natif, `extra_body={"think": False, "options": {"num_ctx": 16384}}` |
| 4 | `test_infer_native_extra_body_cannot_override_fixed_keys` | avec `extra_body={"temperature": 1.0, "stream": True, "format": "text", "model": "autre"}` → `temperature == 0.0`, `stream is False`, `format == "json"`, `model == "Kev-4B"` | idem, clés fixes piégées |
| 5 | `test_infer_native_returns_parsed_json` | `_native_handler(VALID_JSON)` → `result["use_ai_behavior"] is True`, `result["confidence_score"] == 0.9` (dict parsé depuis `message.content`) | `VALID_JSON` existant (l.34–42) |
| 6 | `test_infer_native_invalid_json_raises` | `content` = `"not json"` → `InferenceError` contenant `invalid_json` | `_native_handler("not json")` |
| 7 | `test_infer_native_missing_message_raises_invalid_response` | réponses `{"done": True}` ET `{"message": {}}` → `InferenceError` contenant `invalid_response` (pas `invalid_json`) | handler inline renvoyant ces deux corps |
| 8 | `test_infer_native_http_error_sanitized` | status 500 → `HTTP 500` dans le message ; `"llm.test" not in msg` ; `"SECRET-KEY" not in msg` | service natif `api_key="SECRET-KEY"`, `_native_handler("x", status=500)` |
| 9 | `test_infer_native_timeout_raises` | handler lève `httpx.ReadTimeout` → `timeout` | handler l.61–62 réutilisé |
| 10 | `test_infer_native_connection_error_raises` | handler lève `httpx.ConnectError` → `connection_error` | handler inline |
| 11 | `test_infer_native_sends_bearer_header` | `captured["auth"] == "Bearer K"` (header uniforme, présent si api_key) | service natif `api_key="K"` + capture header |
| 12 | `test_infer_native_root_without_v1_suffix` | `base_url="http://llm.test:11434"` (sans `/v1`) → URL == `http://llm.test:11434/api/chat` | service natif, capture `request.url` |
| 13 | `test_infer_native_root_with_trailing_slash` | `base_url="http://llm.test/v1/"` (slash final) → URL == `http://llm.test/api/chat` | idem |
| 14 | `test_warmup_native_ollama_uses_api_chat` | `warmup(svc_natif, "qwen3:4b")` → URL capturée == `http://llm.test/api/chat` et `body["model"] == "qwen3:4b"` (le warm-up passe par le même chemin natif, sans casser) | service natif + `_native_handler("{}")` (pattern l.134–143) |

### 4.2 Tests neufs — `make_inference_service` (flag + invariants existants)

| # | Test | Assertion clé | Fixture |
|---|---|---|---|
| 15 | `test_make_service_ollama_model_sets_native_flag` | `model=SimpleNamespace(backend_type="OLLAMA")` → `svc._native_ollama is True` (ET `_extra_body` appliqué — invariant existant l.210–220) | `session` + `set_setting("llm_extra_body", …)` |
| 16 | `test_make_service_openai_api_model_native_flag_false_and_no_extra_body` | `backend_type="OPENAI_API"` → `svc._native_ollama is False` ET `svc._extra_body == {}` (double invariant) | idem (étend l.223–233) |
| 17 | `test_make_service_no_model_defaults_native_flag_true` | `model=None` → `svc._native_ollama is True` (décision §3.1 documentée : service par défaut = Ollama) | `session` sans `set_setting` |
| 18 | `test_make_service_non_ollama_local_backend_native_flag_false` | `backend_type="LLAMA_CPP"` → `svc._native_ollama is False` (seul OLLAMA passe natif ; /v1 conservé pour les autres backends locaux) | `session` + `SimpleNamespace` |

### 4.3 Mode /v1 inchangé — tests EXISTANTS conservés à l'identique (listing)

Ces tests passent **sans modification** car le défaut du flag est `False` (chemin /v1 identique) :
`test_infer_returns_parsed_json` (l.45, parse `choices[0]`), `test_infer_invalid_json_raises` (l.53),
`test_infer_timeout_raises` (l.60), `test_infer_http_error_sanitized` (l.70), `test_infer_sends_bearer_and_body` (l.82,
corps /v1), `test_make_inference_service_from_settings` (l.99), `test_make_inference_service_defaults` (l.109),
`test_make_inference_service_timeout_from_settings` (l.118), `test_make_inference_service_timeout_invalid_falls_back`
(l.126), `test_warmup_calls_infer_with_model` (l.134), `test_warmup_absorbs_failure` (l.146),
`test_infer_no_extra_body_unchanged` (l.169, corps /v1 exact avec `response_format`), `test_infer_with_extra_body_merged`
(l.185), `test_infer_extra_body_cannot_override_fixed_keys` (l.200), `test_make_service_ollama_applies_extra_body_from_setting`
(l.210), `test_make_service_openai_api_never_applies_extra_body` (l.223), `test_make_service_invalid_extra_body_is_empty`
(l.236), `test_make_service_null_extra_body_is_empty` (l.246).

### 4.4 Tests de task — pas de changement requis

`backend/tests/test_inference_task.py` : `FakeInference` (l.28–46) mocke `infer` et renvoie un dict final —
aucune dépendance au format /v1 ni à l'URL. L'ensemble des tests de task (warm-up l.310/339, lock l.375+,
triage, annulation) reste à l'identique.

---

## 5. Critères d'acceptation

1. **Modèle OLLAMA du registre** (task inférence) : les requêtes partent vers
   `POST {root}/api/chat` (root = `llm_api_base_url` sans `/v1`) ; avec
   `llm_extra_body = {"options": {"num_ctx": 16384}}`, le log Ollama passe de `n_ctx_slot = 4096` à
   `n_ctx_slot = 16384` et plus de `truncating input prompt limit=2050` pour un prompt > 2050 tokens.
2. **Corps natif** : `model`, `messages` (system+user), `stream: false`, `temperature: 0.0`, `format: "json"` ;
   `options`/`think` de `llm_extra_body` présents en racine ; `response_format` absent ; les clés fixes ne sont
   jamais écrasées par `llm_extra_body`.
3. **Parse natif** : résultat = `json.loads(data["message"]["content"])` ; erreurs `HTTP {code}` / `timeout` /
   `connection_error` / `invalid_json` / `invalid_response` avec les mêmes messages que le mode /v1 (sans URL,
   sans secret) ; `message`/`content` absent → `invalid_response`.
4. **Invariants /v1** : un backend OPENAI_API (service de `infer_with` et `test_external`) reste sur
   `/v1/chat/completions` avec corps et parse inchangés ; les 18 tests existants de `test_inference.py`
   (dont les 12 verrouillant le corps/parse /v1) passent sans modification.
5. **Réglage unique** : `llm_extra_body` reste le seul point de réglage du corps ; aucun nouveau setting DB/env
   (aucune entrée ajoutée dans `settings/keys.py`).
6. **Non-régressions** : warm-up, lock par modèle, timeout, `unload_ollama`, query-assistant, benchmark,
   endpoints `/models/*` : comportements inchangés (tests existants verts).
7. **Qualité** : suite de tests 100 % offline (MockTransport) ; 18 nouveaux tests (§4.1–4.2) + 18 tests existants
   passent ; le code reste dans `backend/app/inference/__init__.py` (aucun autre module modifié).

## 6. Contraintes (ponytail)

- **Un flag sur le service existant, pas de nouvelle classe** : `native_ollama: bool = False` sur
  `OpenAICompatibleService`, branchement à 3 points dans `infer()` (URL, corps, parse).
- **Pas de nouveau setting** (DB ou env) : pas d'ajout dans `settings/keys.py`, pas de migration.
- **Pas de détection par URL** : le flag est fixé par `make_inference_service` (§3.1) ; constructions directes
  existantes (`infer_with` l.189–191, `models.py` l.300) sans flag → `/v1`.
- **Branche /v1 strictement inchangée** (l.58–98 de `infer`) : zéro ligne modifiée dans le chemin OpenAI-compat.
- **Aucun autre module modifié** : pas de task, pas d'endpoint, pas de frontend ; la seule livrable est ce
  fichier de spec (les autres fichiers de `specs/` ne sont pas touchés).
