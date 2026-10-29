# Fuite de l'identifiant d'un modèle externe (OPENAI_API)

Date : 2026-10-29 — spec d'implémentation TDD (agent de codage). Passe 1 = spec + tests
rouges (déjà écrits, NE PAS les modifier hors lint/format) ; passe 2 = les rendre verts.
Exigence utilisateur : « on ne veut pas avoir de risque de fuite ».

## Contexte

Un modèle `OPENAI_API` est stocké `model_identifier = "<base_url>#<modèle>"`
(api/v1/models.py `register_external`, l.172). Aucune règle sur `base_url` : une URL
`https://user:pass@hôte/v1?key=…` est stockée en clair et renvoyée telle quelle par `_payload`
(l.118-128) au propriétaire ET à tous les destinataires d'un partage (utilisateurs, LEAD des
projets partagés).

## Surfaces auditées

Fuites réelles (toutes via `_payload`, api/v1/models.py:118) :
- `GET /models` (l.107, `list_models`) — propriétaire, partages utilisateur/projet, admin ;
- `POST /models` (l.126, `create_model`) — l'admin peut y créer une ligne `OPENAI_API` brute ;
- `POST /models/external` (l.162) ; `PATCH /models/{id}` (l.182, accepte en plus un
  `model_identifier` OPENAI_API arbitraire côté admin).
- Stockage au repos : le secret est en clair dans `model_registry.model_identifier` (dumps,
  sauvegardes), alors que la clé a un champ chiffré dédié (`api_key_enc`).
- `public_model_ref` (inference/__init__.py:151, chantier traçabilité) : prend le modèle par
  `partition("#")` → une ligne héritée `https://h/v1#SECRET#gpt` donne le modèle `SECRET#gpt`.
- Frontend : `models/page.tsx:166` affiche `m.model_identifier` (type `ModelInfo`, api.ts:221).

Écartés (vérifiés, rien à faire) :
- Partages : `POST/GET …/shares` ne renvoient pas l'identifiant.
- Exports crate/xlsx, `GET …/inference` (`model_info`), prédictions (`model_identifier`) :
  instantané/repli via `public_model_ref` (chantier traçabilité) — sans userinfo/chemin/query.
- Locks Redis / logs : `OPENAI_API` exclu des locks (tasks/inference.py:229, models.py:234) et
  du warm-up ; `InferenceError` ne porte que des codes (`timeout`, `HTTP 4xx`…) ;
  `check_public_url` a des messages fixes ; benchmarks : noms seulement ; export RGPD
  (users.py) et admin_users : pas de modèle (compteurs).
- `llm_api_base_url` : réglage d'instance, admin seulement (admin.py) — hors registre.
- Import de crate : `model_identifier` importé = donnée du crate de l'importateur (clean_meta).

## 1. Refus à l'entrée (422, sans écho du secret)

**Décision** : une base URL ne porte jamais de secret ; il va dans `api_key` (chiffré).
- Nouvelle fonction `check_url_secrets(url) -> None` dans `app/model_access.py` (à côté de
  `check_public_url`) : `ValueError("base_url must not contain credentials, query or fragment")`
  si `"@" in urlsplit(url.strip()).netloc` (userinfo, même user seul), `"?" in url` (query, même
  vide) ou `"#" in url` (fragment ; c'est aussi notre séparateur). `urlsplit` qui lève →
  `ValueError("base_url is not a valid URL")`. Messages FIXES : jamais l'URL.
- Contrôle **dans la route** (HTTPException 422, `detail=str(exc)`), PAS en `field_validator`
  pydantic : la 422 de pydantic renvoie `input` (= le secret).
- Appliqué à TOUS (admin compris : un modèle de plateforme est partagé), AVANT `_check_url`
  (SSRF, non-admin seulement — inchangé), dans :
  - `POST /models/external` et `POST /models/external/test` (aucun appel sortant) ;
  - `ExternalModel.model` contenant `#` → 422 (`"model must not contain '#'"`) : un seul `#`
    par identifiant OPENAI_API, invariant désormais garanti ;
  - `POST /models` et `PATCH /models/{id}` quand le backend est `OPENAI_API` : helper
    `_check_openai_identifier(ident)` = exactement un `#`, URL et modèle non vides
    (sinon 422 `"model_identifier must be <base_url>#<model>"`), puis `check_url_secrets(url)`.
    Effet voulu : renvoyer l'identifiant masqué (`gpt-4o`) en PATCH est refusé, la ligne n'est
    pas corrompue. Pour changer d'URL, l'admin ressaisit `<url>#<modèle>` complet ; un
    non-admin ne peut toujours pas PATCHer l'identifiant (403, inchangé) : il supprime et
    ré-enregistre.
- Le chemin reste permis à l'entrée (`/v1`, `/openai/v1`… indispensables) ; il est masqué en
  sortie (§2).

## 2. Masquage en sortie (`_payload`)

**Décision** : même vue pour tous (propriétaire, partage, admin) — un destinataire ne voit
jamais plus que le propriétaire, et le propriétaire n'a pas besoin de l'URL complète (il ne
peut pas l'éditer).
- `_payload` : si `backend_type == "OPENAI_API"` →
  `ident, endpoint = public_model_ref("OPENAI_API", m.model_identifier)` ;
  `"model_identifier": ident or ""`, `"endpoint": endpoint` ; sinon identifiant inchangé et
  `"endpoint": None`. Nouvelle clé `endpoint` dans toutes les réponses modèle.
- **Chemin masqué** (`scheme://hôte[:port]` seulement, comme `public_model_ref`) : certains
  fournisseurs/passerelles mettent un jeton ou un identifiant de compte dans le chemin
  (`/v1/<token>`, passerelles `/<account>/<gateway>/openai`) ; indiscernable d'un chemin
  anodin → on ne publie jamais le chemin. Le stockage garde le chemin (appel fournisseur).
- `public_model_ref` (fichier du chantier traçabilité, à ajuster une fois celui-ci commité) :
  URL = avant le PREMIER `#`, modèle = après le DERNIER `#` (`rpartition`) ; pas de `#` du
  tout → modèle `None` (jamais l'URL entière). Les cas existants
  (`test_inference.py::test_public_model_ref…`) restent verts.

## 3. Migration de nettoyage `0031` (données)

**Décision** : migration en plus du masquage (défense en profondeur : le secret ne doit plus
exister en clair au repos ; le masquage seul laisse le secret dans la base et les sauvegardes).
- `backend/alembic/versions/0031_scrub_model_url_secrets.py`, `revision="0031"`,
  `down_revision="0030"` (0029 = traçabilité, 0030 = sources multiples). Autonome (pas d'import
  `app`), `sa.text` portable SQLite/PG.
- `upgrade()` : `SELECT id, model_identifier FROM model_registry WHERE backend_type =
  'OPENAI_API'` ; pour chaque ligne : `base = ident.split("#", 1)[0]`, `model =
  ident.rsplit("#", 1)[1]` si `#` présent ; `p = urlsplit(base)` ; netloc sans userinfo
  = `p.netloc.rsplit("@", 1)[-1]` (garde hôte, `[IPv6]`, port tels quels) ;
  `new = urlunsplit((p.scheme, netloc, p.path, "", ""))` + (`"#" + model` si `#`). UPDATE
  seulement si différent. Idempotente. Log : nombre de lignes nettoyées + leurs NOMS, jamais
  l'URL.
- Effet sur le fonctionnement : query et fragment n'ont jamais fonctionné
  (`f"{base}/chat/completions"` met `/chat/completions` dans la query ; un fragment décale le
  modèle) → aucune régression. Userinfo : httpx en faisait du Basic auth (qui écrasait le
  Bearer) ; il est retiré → un tel modèle peut répondre 401, le bouton Test le montre et le
  propriétaire ré-enregistre avec `api_key`. Le Basic auth via URL n'est pas une
  fonctionnalité supportée (le secret ne peut pas être conservé hors clair).
- `downgrade()` : no-op (on ne restaure jamais un secret).
- Lignes OLLAMA / autres backends : intactes.

## 4. Frontend

- `lib/api.ts` `ModelInfo` : `endpoint?: string | null` (+ commentaire : identifiant OPENAI_API
  = nom du modèle seul).
- `app/models/page.tsx:166` : `{m.model_identifier}{m.endpoint ? ` · ${m.endpoint}` : ""}`.
  Pas de helper ni de test jest (JSX trivial).
- Formulaire « modèle externe » : sous le champ URL, aide i18n (fr/en/de/es) « La clé va dans
  le champ Clé API, jamais dans l'URL » ; afficher le `detail` 422 tel quel (déjà le cas).

## 5. Non-régression

- SSRF : `_check_url` / `check_public_url` et le re-contrôle par appel
  (`OpenAICompatibleService(public_only=…)`) inchangés ; messages inchangés.
- Authz inchangée (`_require_owner_or_admin`, `_owned`, `access_filter`).
- `infer_with` lit toujours l'identifiant stocké complet (chemin compris).

## Tests (`backend/tests/test_model_identifier_leak.py`, rouges avant implémentation)

- `test_register_external_rejects_url_secrets_admin[userinfo|user-only|query|empty-query|fragment]`
- `test_register_external_rejects_url_secrets_non_admin`
- `test_register_external_rejects_hash_in_model_name`
- `test_external_test_rejects_url_secrets_before_any_call`
- `test_create_model_openai_api_rejects_url_secrets`
- `test_patch_openai_identifier_rejects_secrets_and_bad_format`
- `test_register_payload_is_masked`
- `test_legacy_row_with_secrets_masked_in_every_payload` (liste propriétaire/partage/admin,
  share, PATCH ; vue partagée == vue propriétaire hors `mine`)
- `test_odd_legacy_identifiers_masked[fragment-legacy|no-hash|no-scheme]`
- `test_public_model_ref_never_returns_url_parts[fragment-legacy]` (rouge) / `[no-hash]` (garde)
- `test_local_backends_payload_unchanged` (rouge : clé `endpoint` absente)
- `test_clean_url_with_path_still_registers_and_ssrf_unchanged` (garde, vert)
- `test_migration_0031_scrubs_openai_identifiers`

Tests existants à adapter (par l'implémenteur) :
- `test_models_api.py::test_register_external_encrypts_key` (l.402) :
  `body["model_identifier"] == "gpt-4o"` et `body["endpoint"] == "https://api.x"`.
- `test_inference.py` (paramétrage `public_model_ref`, l.727-729) : ajouter le cas
  `"https://api.x.test/v1#FRAG#gpt-4o" → ("gpt-4o", "https://api.x.test")` (optionnel, couvert
  ici).

## Fichiers

`backend/app/model_access.py`, `backend/app/api/v1/models.py`,
`backend/app/inference/__init__.py` (`public_model_ref`, après commit du chantier
traçabilité), `backend/alembic/versions/0031_scrub_model_url_secrets.py`,
`frontend/src/lib/api.ts`, `frontend/src/app/models/page.tsx`, `frontend/src/messages/*.json`.

## Commandes

```bash
V=<venv>/bin; cd backend
$V/python -m pytest -q -p no:cacheprovider tests/test_model_identifier_leak.py
$V/python -m pytest -q -p no:cacheprovider tests/test_models_api.py tests/test_access_control.py tests/test_inference.py tests/test_crate_import.py
$V/ruff check app tests && $V/ruff format --check app tests
cd ../frontend && node_modules/.bin/tsc --noEmit && node_modules/.bin/jest
```
