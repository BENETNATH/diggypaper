# Corps de requête LLM paramétrable (contexte + thinking local)

Date : 2026-10-27 — spec d'implémentation TDD (agent de codage).

## Contexte

Sur Ollama local, deux incidents de production (E2E du 2026-10-27, base de test
`serv-ics-testing:8123`) ont été diagnostiqués :

1. **Troncature silencieuse du prompt** : le prompt d'un article complet fait
   ~14 500 tokens ; le serveur Ollama alloue `num_ctx=4096` par défaut et tronque
   le prompt à ~2 050 tokens sans le signaler (`truncating input prompt`
   dans le log du serveur). Le modèle n'analyse que ~14 % de l'article.
2. **Loops de génération sur les modèles « thinking »** : qwen3 (mode thinking
   actif par défaut) génère des milliers de tokens de « pensée » sans
   aboutir ; sur un prompt court il a même bouclé sur la répétition de
   tokens jusqu'à l'annulation serveur (`prediction aborted, token repeat
   limit reached` → HTTP 500 côté client).

Les deux se corrigent au corps de la requête : `options.num_ctx` (contexte
adapté au matériel) et `think: false` (pas de mode réflexion pour de
l'extraction JSON à température 0). Ce corps est propre au backend local
(Ollama) ; les backends distants `OPENAI_API` rejettent les champs inconnus
et ne doivent pas les recevoir.

## État actuel

- `OpenAICompatibleService.infer` (backend/app/inference/__init__.py:48–62)
  construit un corps fixe : `model`, `messages`, `temperature: 0.0`,
  `response_format: {"type": "json_object"}`. Aucun `options`, aucun `think`.
- `OpenAICompatibleService.__init__` (l.33–44) : `base_url`, `api_key`,
  `timeout`, `http`, `public_only` — pas de corps supplémentaire.
- `make_inference_service` (l.122–175) : lit `llm_api_base_url`,
  `llm_api_key`, `inference_timeout` via `get_setting(session, …)`
  (backend/app/settings/keys.py) et crée le service ; la distinction
  `OPENAI_API` vs `OLLAMA` est déjà faite (parsing `<url>#<modèle>`,
  l.169–175).
- Réglages existants du même type : `inference_timeout`
  (backend/app/settings/keys.py) + env `LLM_TIMEOUT_SECONDS`
  (docker-compose.yml, services backend/worker) — le pattern de lecture
  DB > env à réutiliser.
- Tests du service : `backend/tests/test_inference.py`
  (corps de requête vérifié via `httpx.MockTransport`) et
  `backend/tests/test_inference_task.py`.

## Spécification

1. **Réglage `llm_extra_body`** (JSON, chaîne) — clé de réglage comme
   `inference_timeout` :
   - `backend/app/settings/keys.py` : declaration
     `llm_extra_body: str | None` (défaut `None`), description : « corps
     JSON additionnel des requêtes LLM (ex. `{"think": false,
     "options": {"num_ctx": 16384}}`), appliqué uniquement aux backends
     locaux ; `None` ou invalide → corps vide » ;
   - env `LLM_EXTRA_BODY` dans `docker-compose.yml` (backend + worker),
     injectée dans l'env par défaut du `.env` du repo :
     `{"think": false, "options": {"num_ctx": 16384}}`.
2. **`make_inference_service`** : parse le réglage —
   - valeur nulle/vide → `{}` ;
   - `json.loads` → dictionnaire (pas un dict → corps `{}` + log warning) ;
   - **appliqué uniquement si le backend est local** (tous backends sauf
     `OPENAI_API`) ; un modèle `OPENAI_API` ne reçoit jamais le corps
     additionnel (ses providers distants rejettent les champs inconnus) ;
   - transmis au service via un nouveau kwarg `extra_body: dict = {}`.
3. **`OpenAICompatibleService`** :
   - `__init__` gagne `extra_body: dict = {}` (stocké tel quel) ;
   - dans `infer`, le corps de requête = corps fixe existant **fusionné**
     avec `extra_body` (les clés du corps fixe priment en cas de
     conflit : `model`, `messages`, `temperature`, `response_format` ne
     sont jamais écrasés — fusion `body = {**self._extra_body, **body_fixe}`) ;
   - rien d'autre ne change (headers, gestion d'erreurs, parsing de la
     réponse).
4. **Pas de détection de troncature** dans cette passe (Ollama ne l'expose
   pas dans la réponse `/v1`) : le `num_ctx` suffisant est la
   contre-mesure ; la limite est notée dans la docstring du réglage.

## Tests à écrire (TDD)

Dans `backend/tests/test_inference.py` (pattern existant `httpx.MockTransport`
qui capture le corps) :

1. `test_infer_no_extra_body_unchanged` — service sans `extra_body` : le
   corps capturé est exactement le corps fixe actuel (régression verrou).
2. `test_infer_with_extra_body_merged` — `extra_body={"think": False,
   "options": {"num_ctx": 16384}}` : le corps capturé contient `think
   is False`, `options == {"num_ctx": 16384}` **et** toujours
   `temperature == 0.0`, `response_format == {"type": "json_object"}`.
3. `test_infer_extra_body_cannot_override_fixed_keys` —
   `extra_body={"temperature": 1.0, "model": "autre"}` : le corps capturé a
   `temperature == 0.0` et le `model` du service (les clés fixes priment).

Dans le fichier qui couvre `make_inference_service` (test_inference.py ou
test_inference_task.py selon la couverture actuelle) :

4. `test_make_service_ollama_applies_extra_body_from_setting` — réglage
   `llm_extra_body='{"think": false, "options": {"num_ctx": 8192}}'` +
   modèle OLLAMA du registre → le service créé a
   `extra_body == {"think": False, "options": {"num_ctx": 8192}}`.
5. `test_make_service_openai_api_never_applies_extra_body` — même réglage +
   modèle `OPENAI_API` (`url#name`) → `extra_body == {}`.
6. `test_make_service_invalid_extra_body_is_empty` —
   `llm_extra_body="pas-un-json"` → `extra_body == {}` (pas d'exception).
7. `test_make_service_null_extra_body_is_empty` — réglage absent/None →
   `extra_body == {}`.

`backend/tests/test_inference_task.py` : les tests qui injectent un service
n'ont rien à changer (kwarg par défaut) ; vérifier que la suite passe telle
quelle (pas de test à ajouter si la couverture ci-dessus est suffisante).

## Critères d'acceptation

- [ ] Réglage `llm_extra_body` (JSON) lisible DB > env, pattern
      `inference_timeout`
- [ ] corps de requête local (non `OPENAI_API`) = corps fixe fusionné avec
      le réglage, clés fixes non écrasables
- [ ] modèle `OPENAI_API` : corps fixe strict, aucun champ additionnel
- [ ] JSON invalide/absent → corps vide, pas d'exception
- [ ] `.env` du repo et `docker-compose.yml` portent
      `LLM_EXTRA_BODY='{"think": false, "options": {"num_ctx": 16384}}'`
- [ ] tous les tests existants passent sans modification

## Contraintes

- Surface minimale : `backend/app/settings/keys.py` (1 clé),
  `backend/app/inference/__init__.py` (1 kwarg + 2 lignes dans
  `make_inference_service` + 1 fusion dans `infer`),
  `docker-compose.yml` (1 ligne env × 2 services), `.env` (1 ligne),
  tests.
- Pas de migration DB (la table de réglages existe déjà), pas de nouveau
  module, pas de changement d'API.
- `num_ctx: 16384` par défaut dans le `.env` : à ajuster par déploiement
  (noté dans la docstring du réglage) ; sur un GPU, 32768 est jouable.
- Hors périmètre : détection/alerte de troncature effective, option par
  modèle (la clé est globale au déploiement — tous les backends locaux
  d'un déploiement partagent le réglage).

## Preuves (diagnostic du 2026-10-27)

- Log serveur Ollama : `truncating input prompt limit=2050 prompt=14485`
  (prompt réel d'un article, base de test).
- `qwen3:1.7b` sans `think:false` : 18 752 tokens générés en 3600 s sans
  JSON terminé ; avec `think:false` + prompt complet :
  `HTTP 500 — prediction aborted, token repeat limit reached` (132 s) ;
  avec `think:false` + prompt « methods » seul : 140 s, JSON dégénéré
  (1 clé) — modèle trop petit pour la tâche (ce réglage ne le rend pas
  compétent, il rend les modèles capables sains et rapides).
- `qwen3:4b` : à valider par probe avant E2E (voir note d'exécution).
