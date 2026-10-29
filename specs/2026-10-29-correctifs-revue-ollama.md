# Correctifs de la revue « OLLAMA » (16 commits origin/main..bb5f4b0)

Date : 2026-10-29 — spec d'implémentation TDD (agent de codage). Passe 1 = spec + tests
rouges (déjà écrits, NE PAS les modifier hors lint/format) ; passe 2 = les rendre verts.

## Contexte

Revue des 16 derniers commits (code : `backend/app`, `backend/tests`, `frontend/src`,
`docker-compose.yml`). Suite verte (521 backend, 53 jest) mais défauts réels : endpoint
Ollama décidé une fois par service (modèles mélangés cassés), température ignorée en natif,
lock par modèle qui expire en cours de job, suppression de poids partagés, sorties de petits
modèles qui tuent le job. Chaque item : problème → décision → fichiers → tests.

## Constats écartés (vérifiés, rien à faire)

- **#6 log de `llm_extra_body`** : la clé est dans `NON_SECRET_KEYS` (settings/keys.py:19),
  pas `SECRET_KEYS` → le `%r` dans le warning est acceptable.
- **#9a compose `LLM_EXTRA_BODY`** : `docker compose config` (v2.40.3) rend bien
  `'{"think": false, "options": {"num_ctx": 16384}}'` par défaut et la valeur d'env sinon.
- **#9b dates Celery** : sérialiseur `json` (celery_app.py:18) ; kombu ≥ 5.3 (celery>=5.3,
  installé 5.6.2) encode `date` en `{"__type__": "date"}` et redonne un `date` → pas de `str`.
- **#9c `AnswerTextarea`** : Entrée = saut de ligne, voulu (réponses multi-lignes, spec
  2026-10-28-annotation-ux-scroll-evidence) ; aucun raccourci clavier global, l'envoi passe
  par le bouton « Enregistrer ».
- **#5 lock pour le bouton Test** : le bouton ne prend pas le lock mais le lit (`_model_busy`,
  api/v1/models.py) : clé posée → `{"ok": false, "error": "model_busy"}` sans contacter le
  modèle (clé partagée `model_lock_key`) ; Redis injoignable → ping quand même (fail-open) ;
  `OPENAI_API` non concerné. Reste une fenêtre TOCTOU (job démarré entre la lecture et le ping),
  acceptée.

## 1. `DELETE /models/{id}` (api/v1/models.py `delete_model`)

**Problème** : (a) `/api/delete` sur `model_identifier` même si une autre ligne du registre
pointe les mêmes poids (create/`_register` ne contrôlent que le nom) → l'autre modèle cassé ;
(b) aucun refus si un job actif référence le modèle (poids supprimés en plein job → FAILED) ;
(c) pas de `_require_admin` explicite avant de supprimer des poids serveur (repose sur
l'invariant `owner_id NULL` des lignes OLLAMA, non garanti en base) ; (d) mode `off` / `host`
avec base compose : 409 → ligne OLLAMA indélébile.

**Décision** — ordre des contrôles :
1. 404 si absent ; `_require_owner_or_admin` (inchangé).
2. `backend_type == "OLLAMA"` → `await _require_admin(user)` (403, avant tout appel).
3. Tous backends : 409 `model_in_use` si un `InferenceJob` PENDING/RUNNING a
   `model_used == m.name` ou `prefilter_model == m.name`, ou un `BenchmarkRun`
   PENDING/RUNNING contient `m.name` dans `models_tested` (filtre Python sur les runs actifs :
   pas de containment JSONB portable SQLite/PG). Avant tout appel Ollama.
4. OLLAMA : appeler `_ollama_delete` **seulement si** (i) Ollama est géré par l'instance
   (`_require_ollama` ne lève pas ; sinon on n'y touche pas) **et** (ii) aucune autre ligne
   (`id != m.id`) n'a le même `model_identifier`. Sinon : suppression de la **ligne seule**.
5. 502 de `_ollama_delete` → ligne conservée (inchangé).

**Tests** (`backend/tests/test_models_api.py`) :
`test_delete_ollama_model_shared_identifier_keeps_weights`,
`test_delete_model_in_use_by_active_inference_job_409[principal-pending|prefiltre-running]`,
`test_delete_model_in_use_by_active_benchmark_409`,
`test_delete_model_after_finished_jobs_ok` (garde, vert),
`test_delete_ollama_model_owned_by_non_admin_403_before_ollama`,
`test_off_or_host_default_delete_ollama_model_removes_row_only[off|host-base-compose]`
(remplace `test_off_delete_ollama_model_409_keeps_row`, supprimé).

## 2. Endpoint natif / `/v1` et corps additionnel décidés PAR MODÈLE (inference/__init__.py)

**Problème** : `make_inference_service(model=…)` fige `native_ollama` et `extra_body` d'après
UN modèle, puis `infer_with` envoie tout modèle non-`OPENAI_API` sur ce même service :
LLAMA_CPP/TRANSFORMERS → `/api/chat` (404) dès que le service vient de la fabrique sans
modèle (bouton Test, query_assistant, benchmark) ou d'un principal OLLAMA ; pré-filtre OLLAMA
avec principal OPENAI_API/LLAMA_CPP → `/v1` sans `num_ctx`/`think` (troncature silencieuse).

**Décision** (racine commune : `infer_with`, tous les appelants passent par là) :
- `make_inference_service(db, http=None)` : **paramètre `model` supprimé** ; corps additionnel
  toujours chargé (le service par défaut ne sert qu'aux backends locaux) ; `native_ollama=True`.
  `tasks/inference.py` l'appelle sans `model`.
- `infer_with` : `OPENAI_API` inchangé ; `model is None` ou `OLLAMA` → service par défaut ;
  autre backend local (LLAMA_CPP, TRANSFORMERS, …) → `copy.copy(default)` avec
  `_native_ollama = False` (jamais muter le service partagé). Les fakes de tests
  (`FakeInference`, modèles OLLAMA) ne passent jamais par la copie.
- `warmup(service, model_identifier, model=None)` → passe par
  `infer_with(model, service, SYS, USER, name=model_identifier)` ; la boucle de warm-up de
  `_do_inference` transmet `m`. Tests existants (`warmup(svc, "id")`) inchangés.

**Tests** — `test_inference.py` : `test_infer_with_decides_endpoint_per_model_on_shared_default` ;
`test_inference_task.py` (vrai `OpenAICompatibleService` sur `httpx.MockTransport`) :
`test_mixed_job_endpoint_per_model_real_service`,
`test_prefilter_ollama_native_with_extra_body_when_main_is_openai_api` ;
`test_models_api.py` : `test_test_installed_llama_cpp_uses_v1`.
Tests remaniés (verts) : `test_make_service_applies_extra_body_from_setting`,
`test_make_service_invalid_extra_body_is_empty`, `test_make_service_null_extra_body_is_empty`
(sans `model=`), `test_infer_with_openai_api_never_sends_extra_body` (garde),
`test_make_service_default_native_flag_true` ; supprimés : `…_ollama_model_sets_native_flag`,
`…_openai_api_model_native_flag_false_and_no_extra_body`,
`…_non_ollama_local_backend_native_flag_false`, `…_no_model_defaults_native_flag_true`.

## N1 (nouveau, bloquant). Température ignorée sur `/api/chat`

**Problème** : le corps natif met `"temperature": 0.0` en racine ; Ollama ne lit les
paramètres d'échantillonnage que dans `options` (ChatRequest : model, messages, format,
options, stream, keep_alive, think) → génération à la température du Modelfile, résultats
non reproductibles. Le spec 2026-10-28-ollama-native-chat-endpoint ne l'a pas vu.

**Décision** : corps natif = `{model, messages, stream: False, format: "json", options}` avec
`options = {**extra_body.get("options", {}), "temperature": 0.0}` (temperature fixe, prime
sur le réglage ; `num_ctx` & co. conservés) ; plus de `temperature` en racine. `/v1` inchangé.

**Tests** (modifiés, rouges) : `test_infer_native_body_exact_without_extra_body`,
`test_infer_native_body_merges_extra_body_options_think`,
`test_infer_native_extra_body_cannot_override_fixed_keys`.

## N2 (nouveau, mineur). Réponse 200 inexploitable

**Problème** : `resp.json()` (deux chemins) et `data["choices"][0]…` (`/v1`) non protégés →
`JSONDecodeError`/`KeyError` brutes (500 sur le bouton Test et query_assistant).

**Décision** : `InferenceError("invalid_response")` (comme le natif sans `message`).

**Test** : `test_infer_unusable_200_raises_invalid_response[v1-html|v1-sans-choices|natif-html]`.

## 3. Lock par modèle (tasks/inference.py)

**Problèmes** : (a) TTL = timeout + 300 s fixé à l'acquisition, jamais renouvelé → expire
après quelques minutes d'un job de plusieurs heures → deux générations sur le même modèle ;
(b) exception Redis au `SET` (ou commit RUNNING) hors du `try` → job PENDING à vie (bloque
« un job par projet ») ; (c) `_lock_ttl` : `int(float("inf"))` → OverflowError, `nan` →
ValueError (même chemin (b)) ; la fabrique accepte `inf`/`nan`/`0`/négatif ; (d) une fois le
TTL corrigé, un job en attente échoue après 40 × 30 s ≈ 20 min alors qu'un job CPU dure des
heures ; (e) pré-filtre jamais locké (timeouts croisés entre projets).

**Décision** :
- (c) Un seul parseur `get_inference_timeout(db) -> float` dans `app/inference` : valeur
  finie et > 0, sinon 120 (+ warning) ; utilisé par `make_inference_service` et `_lock_ttl`.
- (b) Toute exception autre que `ModelBusy` entre la garde de redélivrance et la fin du job
  (acquisition, commit RUNNING inclus) → `_record_failure` (FAILED) puis relance ; les locks
  déjà pris sont relâchés en `finally`.
- (a) Renouvellement : `_do_inference(…, heartbeat=None)` appelle `await heartbeat()` juste
  **avant chaque appel modèle** (warm-ups et `predict`). Le heartbeat de `_run_inference` fait,
  pour chaque lock détenu, `GET` == token → `EXPIRE clé ttl` (même TTL qu'à l'acquisition) ;
  token différent ou erreur Redis → warning, le job continue (best-effort comme la release).
- (e) Locks = liste `[(clé, token)]` : principal puis pré-filtre (sauf OPENAI_API, absent du
  registre, ou même clé que le principal). Tout-ou-rien : un `SET NX` refusé → release de ceux
  déjà pris, `waiting_model`, `ModelBusy`. Release de tous en `finally`.
- (d) `_MODEL_BUSY_MAX_RETRIES = 24 * 3600 // _MODEL_BUSY_RETRY_SECONDS` (2880) : horizon
  aligné sur `visibility_timeout` (24 h, celery_app.py:26) ; l'attente reste annulable
  (`POST …/inference/cancel` → FAILED → garde de redélivrance). Message d'abandon inchangé.
- Docstrings du module (« le pré-filtre n'est pas locké ») à mettre à jour.

**Tests** (`test_inference_task.py`) : `test_lock_renewed_before_each_model_call`,
`test_lock_redis_error_marks_job_failed`, `test_lock_ttl_non_finite_timeout_falls_back`,
`test_lock_busy_keeps_waiting_beyond_40_retries`, `test_lock_also_taken_on_prefilter_model`,
`test_lock_prefilter_busy_releases_main_and_waits`, `test_lock_prefilter_same_model_single_key`
(garde anti auto-blocage, vert) ; `test_inference.py` :
`test_make_inference_service_timeout_non_finite_or_non_positive_falls_back[inf|-inf|nan|0|-5]`.
Ajustés (verts) : `test_lock_busy_retries_task_countdown_30`,
`test_lock_busy_exhausted_retries_fails_job` (utilisent `_MODEL_BUSY_MAX_RETRIES`).
`FakeRedis.expire` + `expire_calls` ajoutés dans `tests/conftest.py`.

**Addendum (audit)** : une `RedisError` au `SET` devient `RuntimeError("model_lock_unavailable")`
(détail host:port loggé seulement, jamais dans `job.error`), locks déjà pris relâchés ; clés
acquises triées (`sorted`) pour éviter le livelock de deux jobs aux rôles principal/pré-filtre
inversés ; `GET` puis `EXPIRE` non atomique (commentaire `ponytail`, Lua si contention).
`options` non-dict du corps additionnel natif → ignoré (N1). `DELETE` : poids partagés comparés
sur l'identifiant normalisé (`:latest` implicite, lignes OLLAMA seulement) ; `model_in_use`
aussi pour une analyse programmée `waiting` (`schedule.model` / `prefilter_model`).

## 4. Sorties de petits modèles (tasks/inference.py `_do_inference`, `_triage`)

**Problème** : (a) étage 1 non-JSON (`invalid_json`) → `continue` : l'article n'atteint jamais
le modèle principal ; (b) `confidence_score` non numérique (`"high"`, `"0.9"`, liste…) →
`TypeError` dans `_triage` → tout le job FAILED (et un `str` stocké en colonne Float).

**Décision** :
- `_confidence(v) -> float | None` : `bool` → None ; `int`/`float`/`str` (strippée) → `float`
  si fini **et dans [0, 1]** (échelle 0-100 : `85` → None, jamais auto-accepté), sinon None ;
  autre → None. Fonction partagée `metrics.confidence` (aussi utilisée par `tasks/benchmark.py`
  pour `BenchmarkPrediction.confidence_score`, colonne Float). `_triage` l'applique ; `ArticlePrediction.confidence_score`
  stocke la valeur coercée (`decision_json` reste brut). Invalide → `NEEDS_HUMAN_REVIEW`.
- Étage 1 en `invalid_json` → l'article passe à l'étage 2 (non compté invalide) ; seul le
  `invalid_json` du modèle principal compte et saute l'article. Autres `InferenceError`
  toujours fatales.

**Tests** : `test_triage_non_numeric_confidence_needs_review`,
`test_triage_numeric_string_coerced`, `test_run_inference_non_numeric_confidence_completes`,
`test_prefilter_invalid_json_falls_through_to_main`.

## 5. `POST /models/{id}/test` en mode off (api/v1/models.py `test_installed`)

**Problème** : jamais de `_require_ollama` → en `off` (ou `host` + base compose) le bouton Test
d'un modèle OLLAMA contacte quand même `llm_api_base_url`.

**Décision** : `backend_type == "OLLAMA"` → `await _require_ollama(db)` après le contrôle
d'accès, avant tout appel (409 au détail précis). Autres backends inchangés.

**Test** : `test_off_test_installed_ollama_409`.

## 7. Bornes de date + query contenant `[dp]` (pubmed/__init__.py `_term_bounded`)

**Problème** : query avec `[dp]` → bornes utilisateur ignorées sur le compte et le chemin
direct (≤ 9 999), mais appliquées par la bissection (> 9 999) : résultat dépendant du volume.

**Décision** : supprimer l'exception `[dp]` — les bornes sont **toujours** ANDées
(intersection, déjà la sémantique des feuillets ; deux clauses `[dp]` sont valides NCBI).
Docstring mise à jour.

**Tests** (`test_pubmed.py`) : `test_fetch_all_pmids_shortcut_query_with_dp_still_bounded`
(remplace `…_no_dp_doubling_when_query_has_dp`), `test_term_bounded_ands_user_bounds_even_if_query_has_dp`.

## 8. Codes d'erreur bruts sur la page /models (frontend)

**Problème** : `timeout`, `url_not_allowed`, `invalid_json`, `HTTP 404`, `ollama_…`,
`model_in_use` affichés tels quels en fr/de/es (résultat du bouton Test, test externe,
`ErrorBanner` de `run()`).

**Décision** : nouveau `frontend/src/lib/modelError.ts` :
`modelErrorMessage(code: string, t: (key: string, values?: Record<string, string | number>) => string): string`
— `/^HTTP (\d{3})$/` → `t("err_http", { status })` ; code connu → `t("err_" + code)` ; sinon
le code brut. Clés (namespace `models`, en/fr/de/es) : `err_timeout`, `err_connection_error`,
`err_url_not_allowed`, `err_invalid_json`, `err_invalid_response`, `err_http` (avec
`{status}`), `err_ollama_disabled_by_deployment`, `err_ollama_base_url_is_compose_default`,
`err_ollama_unreachable`, `err_ollama_delete_failed`, `err_model_in_use`. Exemples fr :
« délai dépassé (modèle trop lent ou en cours de chargement) », « réponse non JSON du
modèle », « erreur HTTP {status} du serveur de modèles », « modèle utilisé par une analyse
ou un benchmark en cours ». Utilisé dans `models/page.tsx` : résultat Test
(`{t("testError")} {modelErrorMessage(error, t)}`), `testExt` (`extFail` avec
`error: modelErrorMessage(r.error ?? "?", t)`), et le `catch` de `run()`.

**Test** : `frontend/src/lib/__tests__/modelError.test.ts` (parité 4 langues sur les vrais
JSON, `HTTP 404`, repli brut).

## 10. Lint (étape finale, CI : `ruff check .` + `ruff format --check .`)

État à la revue : 17 erreurs `ruff check` — E501 `app/api/v1/models.py:26,403`,
`app/api/v1/projects.py:122`, `app/ollama_mode.py:20`, `app/tasks/inference.py:303`,
`tests/test_pubmed.py` ×10 ; I001 `app/api/v1/models.py:8`, `tests/test_projects_api.py:215` ;
`ruff format --check` : 9 fichiers (`app/api/v1/{models,projects}.py`,
`app/tasks/inference.py`, `tests/test_{inference,inference_task,models_api,projects_api,pubmed,sources}.py`).
Après implémentation : `ruff check . && ruff format .` (backend) ; `tsc --noEmit`,
`npm run lint`, `jest` (frontend).

## Hors périmètre (noté)

- Jobs avec modèle OLLAMA lancés en mode `off` : pas refusés au déclenchement (badge
  « non lançable » informatif, spec 2026-10-28) — ils échouent en `connection_error`.
- Warning `capabilities` en mode host + base compose loggé à chaque poll de la page.

## Critères d'acceptation

- [ ] Les 35 tests rouges listés passent ; aucun test existant cassé (suite complète verte)
- [ ] `make_inference_service` sans paramètre `model` ; endpoint natif/`/v1` décidé par appel
- [ ] Corps natif : `temperature` dans `options`, jamais en racine
- [ ] Lock renouvelé avant chaque appel, pré-filtre locké tout-ou-rien, erreurs pré-RUNNING → FAILED
- [ ] DELETE : admin pour OLLAMA, 409 `model_in_use`, poids partagés / mode off → ligne seule
- [ ] `ruff check .`, `ruff format --check .`, `tsc`, `next lint`, `jest` verts

## Fichiers à modifier (implémentation)

- `backend/app/inference/__init__.py` (§2, §3c, N1, N2)
- `backend/app/tasks/inference.py` (§2 appel fabrique + warm-up, §3, §4)
- `backend/app/api/v1/models.py` (§1, §5)
- `backend/app/pubmed/__init__.py` (§7)
- `frontend/src/lib/modelError.ts` (nouveau), `frontend/src/app/models/page.tsx`,
  `frontend/src/messages/{en,fr,de,es}.json` (§8)
- Lint/format : fichiers du §10.

Tests déjà écrits (passe 1) : `backend/tests/{conftest,test_inference,test_inference_task,
test_models_api,test_pubmed}.py`, `frontend/src/lib/__tests__/modelError.test.ts`.
