# Suppression des modèles Ollama via `DELETE /models/{id}`

Date : 2026-10-27 — spec d'implémentation TDD (agent de codage).

## Contexte

`DELETE /models/{model_id}` ne retire que la ligne du registre `model_registry` ; les
poids restent sur le serveur Ollama et le disque n'est pas libéré (bug 1 de
« Bugs / gaps connus », BACKLOG.md:22–25). Attendu : pour un modèle
`backend_type=OLLAMA`, appeler l'API Ollama `DELETE /api/delete` **avant** de retirer la
ligne registre ; comportement inchangé pour les autres backends (ex. `OPENAI_API`).

## État actuel

- `DELETE /{model_id}` — `delete_model` (backend/app/api/v1/models.py:336–343, statut
  204) : `db.get(ModelRegistry, model_id)` → 404 « model not found » si absent (l.338–
  340) ; `_require_owner_or_admin(m, user)` (l.341, helper l.80–82) ; `db.delete(m)` +
  `db.commit()` (l.342–343). Aucun appel Ollama.
- `ModelRegistry` (backend/app/db/models.py:367–382) : `name` unique (l.371),
  `backend_type` (l.372–374, `BackendType`, défaut serveur `OLLAMA`),
  `model_identifier` (l.375) — c'est le **nom du modèle côté Ollama** (le pull
  enregistre avec `identifier = ref`, cf. `_pull` → `_register`, models.py:217–218).
- Base URL Ollama : `_ollama_base(db)` (models.py:119–122) = réglage
  `llm_api_base_url` (défaut `localhost:11434`, cf. test_models_api.py:283) sans le
  suffixe `/v1` — à réutiliser tel quel.
- Pattern d'appel Ollama existant :
  - pull (asynchrone, progression) : `pull_ollama` (models.py:225–233) → `_pull`
    (models.py:196–222) : `httpx.AsyncClient` créé en ligne, `f"{base}/api/pull"`, corps
    JSON `{"name": ref}` (l.203), erreur HTTP ≥ 400 → `RuntimeError(f"HTTP {code}")`
    (l.204–205) ;
  - appel synchrone court : `list_ollama` (models.py:174–193) :
    `httpx.AsyncClient(timeout=10)` (l.180), `GET f"{base}/api/tags"` (l.181),
    `except (httpx.HTTPError, ValueError)` → `HTTPException(502,
    detail="ollama_unreachable")` (l.184–185).
- Test existant du endpoint : `test_delete_model_admin` (backend/tests/test_models_api.
  py:107–114) — crée un modèle (backend par défaut `OLLAMA`) puis attend 204.

## Spécification

Flow de `DELETE /models/{model_id}` (endpoint `delete_model`, models.py:336–343) :

1. Invarianté : recherche de la ligne → **404** si absente ; `_require_owner_or_admin` →
   **403** sinon. Ces deux vérifications restent **avant** tout appel Ollama (pas
   d'appel Ollama pour un inconnu ou un utilisateur sans droit).
2. **Si et seulement si `m.backend_type == "OLLAMA"`** (comparaison au type
   `BackendType`, cf. db/models.py:372–374 ; les routes enregistrent déjà des chaînes,
   cf. models.py:271, 306) : appeler Ollama **avant** la suppression de la ligne
   registre —
   - `base = await _ollama_base(db)` (l.119–122) ;
   - `httpx.AsyncClient(timeout=10)` (comme `list_ollama`, l.180) ;
   - `DELETE f"{base}/api/delete"` avec corps JSON `{"name": m.model_identifier}` (même
     payload « name » que le pull, l.203) ;
   - **réponse 2xx** → continuer ;
   - **404 d'Ollama** (modèle déjà supprimé) → continuer (**idempotence**) : retirer la
     ligne registre, 204 ;
   - **autre HTTP ≥ 400** → `HTTPException(502, detail="ollama_delete_failed")`, ligne
     registre **conservée** (l'exception lève avant `db.delete` ; le rollback du
     dépendance `get_db` suffit, pas de commit sur ce chemin) ;
   - **erreur réseau** (`httpx.HTTPError` : connection refused, timeout, DNS…) →
     `HTTPException(502, detail="ollama_unreachable")` (même vocabulaire de détail que
     l.185), ligne registre **conservée**.
3. Invarianté : `db.delete(m)` + `db.commit()` → **204** (l.342–343).
4. **Autres backends** (`OPENAI_API`, `LLAMA_CPP`, `TRANSFORMERS`) : aucun appel Ollama ;
   suppression registre seule, 204 — comportement actuel inchangé.

Ordre garanti : appel Ollama → (si OK/404) suppression ligne registre. Un 502 Ollama
laisse donc la ligne en place (ré-essayer plus tard = idempotent).

## Tests à écrire (TDD)

Dans `backend/tests/test_models_api.py` (fixtures existantes `client`, `session`,
`admin_user`, `do_login` ; tests `async def`). Pour les tests Ollama, patcher
`httpx.AsyncClient` avec un `httpx.MockTransport` — même pattern que
`test_infer_with_uses_model_url_and_key` (l.249–255) — et restaurer après le test.
Le handler Ollama est appelé par `http.delete(...)` dans le endpoint/helper.

1. `test_delete_ollama_model_calls_ollama_delete_then_removes_row` — admin ; créer un
   modèle OLLAMA (`POST /api/v1/models`, `model_identifier="qwen3:4b"`, cf. l.20–31) ;
   handler : assert `request.method == "DELETE"`, `request.url.path == "/api/delete"`,
   `json.loads(request.content) == {"name": "qwen3:4b"}` ; renvoyer `200
   {"deleted": true}`. Assertions : `DELETE /api/v1/models/{id}` → **204** ;
   `GET /api/v1/models` → items vides ; l'appel Ollama a eu lieu (1 fois).
2. `test_delete_ollama_model_missing_on_ollama_still_removes_row` — Ollama renvoie
   `404 {"error": "model 'qwen3:4b' not found"}` → **204**, ligne absente de
   `GET /api/v1/models` (idempotence).
3. `test_delete_ollama_model_ollama_http_error_502_keeps_row` — Ollama renvoie
   `500` → **502**, `detail == "ollama_delete_failed"` ; `GET /api/v1/models` contient
   encore le modèle (ligne conservée).
4. `test_delete_ollama_model_network_error_502_keeps_row` — handler lève
   `httpx.ConnectError("refused", request=…)` → **502**, `detail ==
   "ollama_unreachable"` ; ligne conservée.
5. `test_delete_ollama_model_no_call_for_openai_api_backend` — modèle `OPENAI_API`
   (via `POST /api/v1/models/external`, cf. `test_register_external_encrypts_key`
   l.202–226) ; handler qui lève `AssertionError("Ollama ne doit pas être appelé")` ;
   DELETE → **204**, ligne absente.
6. `test_delete_ollama_model_forbidden_before_ollama_call` — chercheur (non propriétaire,
   non admin) sur un modèle OLLAMA de la plateforme → **403**, et le handler Ollama
   (levant `AssertionError`) n'est pas appelé : le contrôle de permission précède
   l'appel Ollama.
7. `test_delete_model_admin` (existant, l.107–114) — **à mettre à jour** : sans mock,
   l'appel Ollama pointe vers `localhost:11434` (défaut, cf. l.283) et échouerait hors
   ligne (502). Mocker le `DELETE /api/delete` (200) ; les assertions 204 + liste vide
   restent inchangées.

## Critères d'acceptation

- [ ] `DELETE /models/{id}` sur modèle `backend_type=OLLAMA` appelle
      `DELETE {base}/api/delete` avec `{"name": model_identifier}`, `base` =
      `llm_api_base_url` sans `/v1` (`_ollama_base`, models.py:119–122), **avant**
      `db.delete`
- [ ] 404 d'Ollama → ligne supprimée quand même, 204 (idempotence)
- [ ] échec réseau ou autre HTTP ≥ 400 (≠ 404) d'Ollama → 502 (`ollama_unreachable` /
      `ollama_delete_failed`), ligne registre conservée
- [ ] 404 modèle inconnu (registre) et 403 non propriétaire/non admin inchangés, et
      précèdent tout appel Ollama
- [ ] modèles `OPENAI_API` / autres backends : aucun appel Ollama, 204, suppression
      registre seule
- [ ] `test_delete_model_admin` (mis à jour avec le mock Ollama) + tous les tests
      existants de `test_models_api.py` passent

## Contraintes

- Un seul fichier de code modifié : `backend/app/api/v1/models.py` (endpoint
  `delete_model`, l.336–343, + au plus 1 helper local type `_ollama_delete(db, name)`
  suivant les pattern `list_ollama` (l.179–185) et `_pull` (l.202–205) : même base URL,
  même corps JSON, même gestion d'erreurs 502). Plus `backend/tests/test_models_api.py`.
- L'appel est **synchron** dans l'endpoint (pas de tâche de fond ni d'état `_PULLS` —
  la suppression est courte, `timeout=10` comme `list_ollama`, l.180 ; le `timeout=None`
  du pull, l.202, ne s'applique pas ici).
- Pas de nouvelle dépendance (httpx déjà importé, l.15), pas de changement de
  permissions (`_require_owner_or_admin` inchangé), pas d'other endpoint touché
  (pull, register, list, update… inchangés).
- Pas de commit/sur-écriture sur le chemin d'erreur : l'exception `HTTPException(502)`
  se lève avant `db.delete(m)` ; le rollback de la dépendance `get_db` garantit la
  conservation de la ligne.
