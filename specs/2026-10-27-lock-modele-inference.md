# Lock par modèle pour l'inférence (Redis)

Date : 2026-10-27 — spec d'implémentation TDD (agent de codage).

## Contexte

Seule garde anti-concurrence : « un job PENDING/RUNNING par projet »
(backend/app/api/v1/inference.py:112–123). Le worker Celery tourne en
`concurrency=3` prefork par défaut (docker-compose.yml:99) et Ollama
`num_parallel=1` par défaut (aucun `OLLAMA_NUM_PARALLEL` dans la compose) :
plusieurs projets distincts qui utilisent le même modèle local frappent Ollama
en parallèle et se sérialisent **à l'intérieur du serveur, de façon invisible**
— le job reste « RUNNING » en attente, consomme le budget
`LLM_TIMEOUT_SECONDS` par appel, occupe un slot prefork (BACKLOG.md:27–32 ;
évolution BACKLOG.md:44–49). Le lock rend l'attente visible et équitable.

## État actuel

- Garde par projet : `trigger_inference` lève 409 si un job PENDING/RUNNING
  existe (backend/app/api/v1/inference.py:112–123). Rien de comparable entre
  projets. Le job créé part `PENDING` puis `inference_task.delay` (l.146–159).
- Task : wrapper sync `inference_task` → thread dédié →
  `asyncio.run(_run_inference(job_id))` (backend/app/tasks/inference.py:108–126).
  `_run_inference` (l.129–194) : garde de redélivrance — job déjà
  FAILED/COMPLETED/RUNNING → `return {}` (l.160–167) ; passage à `RUNNING`
  (l.168) ; service créé par `make_inference_service(session)` (l.172–173) ;
  le warm-up se fait **dans** `_do_inference`, avant la boucle d'articles, pour
  chaque modèle (prefilter puis principal), backend `OPENAI_API` sauté
  (l.307–312, saut l.308).
- Modèles : `ModelRegistry.backend_type` (défaut `OLLAMA`) +
  `model_identifier` (backend/app/db/models.py:372–375) ; le job porte
  `model_used`, `prefilter_model`, `result_json`, `status` (l.785–798).
  `OPENAI_API` = identifiant `<url>#<modèle>` propre au modèle, pas de
  backend local (backend/app/inference/__init__.py:169–175).
- Timeout par appel : réglage `inference_timeout` (env `LLM_TIMEOUT_SECONDS`,
  défaut 120 s) lu via `get_setting` dans `make_inference_service`
  (backend/app/inference/__init__.py:122–133 ; docker-compose.yml:104).
- Phase lisible : `_progress` écrit `{"phase": "running", done, total, …}`
  dans `result_json` à chaque article (backend/app/tasks/inference.py:432–443)
  ; `GET /inference` l'expose via `result` (backend/app/api/v1/inference.py:74,
  endpoint l.199–235, variante `active` l.213–219).
- Redis : déjà en infra (service `redis:7-alpine`, docker-compose.yml:31–39 ;
  `REDIS_URL` dans l'env du backend l.71 et du worker l.106), utilisé uniquement
  comme broker Celery (backend/app/celery_app.py:13, 16). **Aucun client Redis
  direct dans le code applicatif** : `app/core/config.py` (Settings) n'a pas de
  champ redis et aucun module ne crée de client. Le package `redis` est déjà
  installé (le broker Redis de Celery l'exige via kombu) → pas de nouvelle
  dépendance.
- Échec / redélivrance : `_record_failure` (l.460–470) ; redémarrage du worker :
  les jobs RUNNING passent FAILED au démarrage, les PENDING restent en file
  (backend/app/celery_app.py:49–51, 84–92).

## Spécification

- Nouvelle exception `ModelBusy` (module `app/tasks/inference.py`).
- `_run_inference(...)` gagne un kwarg optionnel `redis=None` (injectable en
  tests, même pattern que `session_factory`/`http`/`inference`, l.137–138) ; en
  production, client créé à la demande seulement si le lock est requis :
  `redis.asyncio.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))`
  (même défaut que backend/app/celery_app.py:13).
- **Acquisition du lock dans le code du task, au début du job, avant le warm-up**
  — dans `_run_inference`, **après** la garde de redélivrance (l.160–167) et
  **avant** `job.status = RUNNING` (l.168). L'endpoint
  (backend/app/api/v1/inference.py) n'est pas modifié :
  1. Charger la ligne `ModelRegistry` du `model_used` du job (selon l.295–300).
  2. Si la ligne existe **et** `backend_type != OPENAI_API` :
     - clé : `inference:lock:<backend_type>:<model_identifier>`
       (ex. `inference:lock:OLLAMA:qwen3:4b`) ;
     - token : `uuid.uuid4().hex`, unique par délivrance ;
     - TTL : `inference_timeout + 300` s, `inference_timeout` lu avec la même
       logique que `make_inference_service` (`get_setting(session,
       "inference_timeout")`, défaut 120 — backend/app/inference/__init__.py:
       124–131) ;
     - `SET <clé> <token> NX EX <TTL>` ;
     - **succès** → on garde `(clé, token)` et on continue (RUNNING, warm-up,
       boucle) ;
     - **échec** (clé déjà prise) → écrire
       `job.result_json = {"phase": "waiting_model"}` + commit (le job reste
       `PENDING`, donc visible par `GET /inference?active=true` et la garde
       « un job par projet » continue d'appliquer), puis lever `ModelBusy`.
  3. Ligne absente du registre ou `OPENAI_API` → **pas de lock**, comportement
     actuel inchangé.
- **Requeue** : le wrapper `inference_task` attrape `ModelBusy` :
  - si `self.request.retries < 40` →
    `raise inference_task.retry(countdown=30, max_retries=40)` (le job est
    replacé en file Celery ; côté base il reste PENDING avec
    `waiting_model`) ;
  - sinon (40 × 30 s ≈ 20 min d'attente dépassées) → marquer le job
    `FAILED` (erreur lisible : « modèle occupé : abandonné après 40 tentatives
    (waiting_model) », `completed_at` posée — pattern `_record_failure`,
    l.460–470) puis relancer l'exception.
- **Release en `finally`** (succès, échec, annulation inclus) — seulement si le
  lock a été acquis : `DEL <clé>` **uniquement si la valeur courante est égale
  au token du job** (owner check ; petit script Lua `GET/COMPARER/DEL` ou
  lecture-comparaison-suppression). Un échec de release (Redis injoignable) est
  loggé, pas relancé : le TTL fera expirer le lock.
- Pré-filtre (`prefilter_model`, optionnel, l.348–353) : **pas locké** dans
  cette passe — seul le modèle principal est locké (voir Contraintes).
- Hors périmètre, note : l'évolution GPU passera `OLLAMA_NUM_PARALLEL=n` —
  le lock binaire sera remplacé par un compteur de `n` slots sur la même clé
  (incrément/décrément borné à la place de SET NX).

## Tests à écrire (TDD)

Dans le fichier de tests existant du task inférence (`backend/tests/`, les
tests injectent déjà `session_factory`, `http`, `inference` dans
`_run_inference` — backend/app/tasks/inference.py:137–138). Fake Redis en
mémoire dans le test (dict : `set` NX, `get`, `delete`, release avec owner
check), injecté via le nouveau kwarg `redis=` ; prévoir un fixture
`fake_redis` réutilisable.

1. `test_lock_key_ttl_and_token` — modèle OLLAMA `qwen3:4b` en registre,
   `inference_timeout=600` → assert `SET` sur la clé
   `inference:lock:OLLAMA:qwen3:4b`, valeur = token hex du job, `NX`,
   `EX == 900` (600 + 300).
2. `test_lock_acquired_before_warmup` — fake redis + service injecté qui
   journalise ses appels → le `SET` de lock précède strictement tout appel
   `infer()` (warm-up).
3. `test_lock_busy_writes_waiting_model_and_stays_pending` — clé déjà prise
   dans le fake → `_run_inference` lève `ModelBusy` ; en base :
   `job.status == PENDING` et `job.result_json == {"phase": "waiting_model"}`.
4. `test_lock_busy_retries_task_countdown_30` — au niveau du wrapper (core
   async patché pour lever `ModelBusy`, `inference_task.push_request(retries=0)`)
   → exception Celery `Retry` avec `countdown == 30` et `max_retries == 40`.
5. `test_lock_busy_exhausted_retries_fails_job` — `push_request(retries=40)` →
   `MaxRetriesExceededError` ; job `FAILED`, `job.error` non vide (mention
   « modèle occupé » / `waiting_model`), `completed_at` posée.
6. `test_lock_released_after_success` — job terminé COMPLETED → release appelé
   avec `(clé, token)` ; dans le fake la clé est supprimée.
7. `test_lock_released_on_inference_error` — `infer()` lève
   `InferenceError` → job FAILED **et** clé release (chemin `finally`).
8. `test_release_only_own_token` — après acquisition, le fake passe la valeur
   à un autre token → la release ne supprime pas la clé (owner check).
9. `test_no_lock_for_openai_api_model` — modèle principal `OPENAI_API`
   (`url#name`) → aucun `SET`, aucune release ; job COMPLETED comme aujourd'hui.
10. `test_no_lock_when_model_not_in_registry` — `model_used` absent du registre
    → aucun `SET`, chemin inchangé.
11. `test_redelivered_closed_job_does_not_relock` — job déjà `FAILED`
    (ex. annulé pendant l'attente) → `return {}`, aucun `SET` (garde
    l.160–167 avant le lock).
- Tests existants du task qui couvrent un backend local (défaut `OLLAMA`) :
  comportement inchangé, mais ils doivent injecter le `fake_redis` (fixture du
  module de tests ou ajout du kwarg dans les appels) — les lister et les
  mettre à jour.

## Critères d'acceptation

- [ ] Clé `inference:lock:<backend_type>:<model_identifier>`, `SET NX` + TTL =
      `inference_timeout + 300 s`, token unique par délivrance
- [ ] Acquisition dans le code du task, après la garde de redélivrance, avant le
      warm-up ; endpoint inchangé
- [ ] Modèle occupé → job PENDING, `result_json.phase == "waiting_model"`
      (lisible via `GET /inference`), `retry(countdown=30)`, max 40 retries
- [ ] 40 retries dépassées → job FAILED avec erreur lisible
- [ ] Release en `finally` avec owner check (uniquement le propre token),
      même en cas d'erreur ou d'annulation
- [ ] `OPENAI_API` et modèle hors registre : pas de lock, comportement actuel
      inchangé
- [ ] Tous les tests existants du task inférence passent (fake injecté là où
      nécessaire)

## Contraintes

- Surface minimale : `backend/app/tasks/inference.py` (exception, kwarg
  `redis`, bloc lock/release, gestion `ModelBusy` dans le wrapper) + le fichier
  de tests du task. Pas de nouveau module, pas de migration, pas de changement
  d'endpoint, pas de nouvelle dépendance (`redis` déjà présent via le broker
  Celery), pas de modification de docker-compose (`REDIS_URL` déjà dans
  l'env du worker, docker-compose.yml:106).
- Le pré-filtre n'est pas locké : l'étage 1 est optionnel et léger (titre +
  abstract, backend/app/tasks/inference.py:348–353) ; lock les deux modèles
  ajouterait un ordre d'acquisition et un double release sans gain
  proportionnel. Limitation documentée.
- La garde « un job PENDING/RUNNING par projet » (backend/app/api/v1/
  inference.py:112–123) est conservée telle quelle : un job requeue reste
  PENDING en base.
- TTL = timeout + 300 s est un filet anti-fuite (crash du worker : pas de
  `finally`, le job passe FAILED au redémarrage — backend/app/celery_app.py:
  84–92 — et le lock expirera au TTL). Sur les runs CPU longs,
  `LLM_TIMEOUT_SECONDS` est relevé (docker-compose.yml:104) : le TTL suit.
- L'affichage frontend de `waiting_model` (« en file ») est hors périmètre de
  cette passe : la phase est lisible via `GET /inference`, l'UI suivra.
