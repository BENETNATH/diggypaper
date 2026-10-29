# Traçabilité du modèle ET de sa version (résultats + exports)

Date : 2026-10-29 — spec d'implémentation TDD (agent de codage). Passe 1 = spec + tests
rouges (déjà écrits, NE PAS les modifier hors lint/format) ; passe 2 = les rendre verts.

## Contexte

Demande : « dans l'export et sur les résultats, on doit avoir une mention du modèle et de sa
version utilisée, afin de tracer toute l'information ».

État actuel : `ArticlePrediction.model_used` et `InferenceJob.model_used` / `prefilter_model`
ne contiennent que le **nom du registre**. Aucune version, rien n'est figé au moment de la
prédiction : le registre se renomme, se supprime (`DELETE /models/{id}`), son
`model_identifier` se modifie (`PATCH`, admin), un tag Ollama se re-pull (autres poids, même
nom). `project.json` (RO-Crate) relit le registre **vivant** au moment de l'export, pour le
dernier job seulement, et y écrit l'identifiant `OPENAI_API` complet `<url>#<modèle>` (URL
avec éventuels `user:pass@` / `?key=` : fuite). Le XLSX ne donne que les noms.

## Décisions

### D1. Ce que « version » veut dire, par backend

| Backend | identifiant tracé | version tracée | source |
|---|---|---|---|
| `OLLAMA` (et modèle absent du registre → service par défaut) | `model_identifier` (ex. `qwen3:4b`) | **digest** des poids (64 hex, tel que renvoyé par Ollama ; ses 12 premiers = colonne ID de `ollama list`) | `GET {racine}/api/tags`, une fois par job |
| `LLAMA_CPP`, `TRANSFORMERS`, autres locaux | `model_identifier` | champ `model` **renvoyé** par le serveur dans la réponse (`/v1`) | chaque réponse |
| `OPENAI_API` | partie modèle seulement (`gpt-4o`) + `endpoint` = `scheme://hôte[:port]` | champ `model` renvoyé (ex. `gpt-4o-2024-08-06` : l'alias résolu, seule info fiable) | chaque réponse |

`/api/show` n'est pas utilisé (pas de digest). L'écho `model` de `/api/chat` (= le tag) n'est
**pas** une version pour Ollama : ignoré. Jamais la clé API, jamais l'URL complète.

### D2. Où c'est stocké (volume : prédictions nombreuses → per-prédiction compact)

- `ArticlePrediction.model_identifier` (String, NULL) et `ArticlePrediction.model_version`
  (String, NULL) : ~20 + 64 caractères par ligne, autoporteurs (survivent à la suppression
  du job, du registre, et au RO-Crate). Pas de FK vers le job (les prédictions importées
  d'un crate n'ont pas de job ; YAGNI).
- `InferenceJob.model_info` (JSONB, NULL) : instantané complet des deux étages, une ligne
  par job : `{"main": snap, "prefilter": snap | null}` avec
  `snap = {name, backend, identifier, version}` + selon le backend `endpoint`
  (OPENAI_API) ou `size, modified_at, parameter_size, quantization, family, format`
  (Ollama, depuis `details` de `/api/tags`).
- Lignes anciennes : NULL partout → affichage « version non enregistrée », jamais d'erreur.
  Pas de rattrapage (l'information n'a jamais été relevée).
- Migration `0029_model_traceability.py` (revision `"0029"`, down `"0028"`) : 3
  `op.add_column` (`postgresql.JSONB()` pour `model_info`), downgrade symétrique.

### D3. Capture (best-effort, jamais bloquante)

Un échec de relevé (Ollama injoignable, HTTP ≥ 400, JSON invalide, tag absent, digest au
mauvais format, service de test sans `base_url`, **toute** exception) → version `null` +
`logger.warning` (nom du modèle et type d'exception, **pas d'URL**) ; le job continue.
Digest relevé au démarrage du job ; un re-pull pendant le job n'est pas détecté
(commentaire `ponytail:` — relever à nouveau à la fin si besoin).

### D4. Les deux étages

Instantané pour le principal ET le pré-filtre. Une prédiction porte l'identifiant/version
du modèle **qui l'a produite** : rejet à l'étage 1 → ceux du pré-filtre (comme `model_used`
aujourd'hui) ; étage 1 illisible (`invalid_json`) → étage 2 → ceux du principal.

### D5. Sécurité

Clé API jamais lue par l'instantané ; `OPENAI_API` → partie modèle + `endpoint` sans
userinfo, chemin, query ni fragment (y compris dans le **repli** registre de `project.json`
pour les anciens jobs : corrige la fuite actuelle) ; chaînes relevées : `str` seulement,
caractères de contrôle retirés, 200 car. max (40 pour les détails) ; digest validé par
`^(sha256:)?[0-9a-f]{64}$`. Droits inchangés : la prédiction d'une recherche **aveugle**
reste `null` avant SA relecture (les nouveaux champs sont dans ce même payload) ; l'export
d'une recherche aveugle vide déjà les prédictions (`_load`). Le nom du modèle d'un job est
déjà visible en aveugle (`last_job.model`) ; sa version n'apprend rien sur les réponses.

## 1. Instantané et écho (backend/app/inference/__init__.py)

- `ollama_ref(identifier)` : **déplacé** depuis `api/v1/models.py` (`_ollama_ref`), qui
  l'importe (une seule normalisation `:latest`).
- `clean_meta(v, limit=200) -> str | None` : `str` seulement, `[\x00-\x1f\x7f]` retirés,
  strip, tronqué ; vide → None.
- `public_model_ref(backend, identifier) -> tuple[str | None, str | None]` :
  `OPENAI_API` → (partie après `#`, `scheme://hostname[:port]` ou None si URL invalide) ;
  sans `#` l'identifiant est `None` (jamais `""` ni la chaîne entière) ;
  sinon `(identifier, None)`.
- `async def model_snapshot(model, service, name=None) -> dict | None` : None si ni modèle
  ni nom. `{name, backend, identifier, version: None}` (+ `endpoint` pour OPENAI_API, via
  `public_model_ref`, **aucun appel réseau**). Backend `OLLAMA` ou modèle absent :
  `GET {service.base_url sans /v1}/api/tags` avec `service._http` s'il existe (tests) sinon
  `httpx.AsyncClient(timeout=10)` ; entrée retenue si
  `ollama_ref(entry["name"] ou entry["model"]) == ollama_ref(identifier)` ;
  `version = digest` validé ; détails `size` (int), `modified_at`, `parameter_size`,
  `quantization` (= `quantization_level`), `family`, `format` (via `clean_meta(…, 40)`).
  `try/except Exception` englobant (D3). Autres backends locaux : pas d'appel.
- `OpenAICompatibleService.infer` : après lecture réussie de la réponse (natif et `/v1`),
  `self.served_model = clean_meta(data.get("model"))`. Signature de `infer` inchangée (les
  faux services des tests ne bougent pas).
- `infer_with(…, meta: dict | None = None)` : si `meta` est fourni, après l'appel réussi,
  `meta["served_model"] = clean_meta(getattr(svc, "served_model", None))` (`svc` = service
  réellement appelé : par défaut, copie `/v1`, ou service OPENAI_API propre au modèle ; un
  faux service sans attribut → None). Les autres appelants (benchmark, Test, assistant de
  requête) ne passent pas `meta` : inchangés.

**Tests** (`tests/test_inference.py`) :
`test_model_snapshot_ollama_reads_digest_and_details_from_api_tags`,
`test_model_snapshot_ollama_matches_implicit_latest_tag`,
`test_model_snapshot_never_raises[http-500|non-json|connection-error|tag-absent|bad-digest]`,
`test_model_snapshot_without_http_service_is_best_effort`,
`test_model_snapshot_openai_api_hides_url_secrets_and_makes_no_call`,
`test_infer_with_meta_reports_served_model[v1-echo|native-echo|no-model-field|control-chars]`,
`test_infer_with_meta_openai_api_served_model`.

## 2. Tâche d'inférence (backend/app/tasks/inference.py `_do_inference`)

- Après la boucle de warm-up, avant `_progress(… 0 …)` (qui commit) :
  `job.model_info = {"main": await model_snapshot(main, inference, job.model_used),
  "prefilter": await model_snapshot(pre, inference, job.prefilter_model) if job.prefilter_model else None}`.
- `predict(…)` reçoit un `meta` (dict neuf par appel) transmis à `infer_with(…, meta=meta)`.
- À la création de chaque `ArticlePrediction` : `snap` = étage qui a produit la prédiction
  (D4) ; `model_identifier = snap["identifier"]` ; `model_version` = `snap["version"]` si
  `snap["backend"] in (None, "OLLAMA")`, sinon `meta.get("served_model")`. Backend non
  Ollama : la première version servie non nulle d'un étage — relevée après le premier appel
  réussi de CET étage, qu'il produise ou non la prédiction stockée (pré-filtre qui ne rejette
  jamais compris) — remplit `snap["version"]` du job (helper `note_served(stage, meta)` :
  réassigner `job.model_info` = nouveau dict pour que le JSON soit persisté). La version de
  chaque prédiction reste celle de SA réponse.
- Comportement connu : un job repris (`resume_after`) dont tous les articles sont déjà traités
  ne fait aucun appel ; pour un backend non Ollama son instantané garde `version = None`
  (les prédictions déjà écrites portent leur propre version).
- Docstring du module : mentionner la traçabilité (instantané + champs par prédiction).

**Tests** (`tests/test_inference_task.py`, vrai `OpenAICompatibleService` sur
`httpx.MockTransport`, helper `_tags_transport`) :
`test_job_snapshot_and_prediction_version_ollama_real_service`,
`test_prefilter_rejected_prediction_carries_prefilter_identifier_and_version`,
`test_snapshot_failure_never_fails_job[http-500|non-json|connection-error|tag-absent]`,
`test_local_v1_backend_version_is_served_model_echo`,
`test_openai_api_job_traces_served_model_without_url_secrets`,
`test_injected_fake_service_records_identifier_without_version`.
Garde existante : `test_mixed_job_endpoint_per_model_real_service` (son transport lève une
`JSONDecodeError` sur le `GET /api/tags` → le `except Exception` de D3 est requis).

## 3. Modèle de données + migration (backend/app/db/models.py, alembic)

Colonnes de D2 (commentaire français : « figé au moment de la prédiction »).
**Tests** (`tests/test_models.py`) : `test_article_predictions_columns` (ensemble exact
étendu, String nullables), `test_inference_jobs_model_info_column`,
`test_migration_0029_adds_traceability_columns` (charge le fichier de migration, `op`
simulé : 3 colonnes ajoutées, `down_revision == "0028"`).

## 4. Payloads API

- `api/v1/annotations.py` `_prediction_payload` : `+ model_identifier, model_version`.
- `api/v1/inference.py` `_job_payload` : `+ model_info` (l'instantané tel quel ; il ne
  contient aucun secret par construction).
- `api/v1/searches.py` `last_job` : `+ model_version` (= `model_info.main.version`, None
  sinon).

**Tests** : `test_annotations_api.py::test_get_prediction_exposes_model_identifier_and_version`
(ancienne prédiction → clés présentes à `null`) ;
`test_searches.py::test_blind_prediction_model_version_only_after_own_review`,
`test_searches.py::test_search_summary_last_job_model_version` ;
`test_inference_api.py::test_get_inference_status_exposes_model_info_snapshot`.

## 5. Exports (lisent l'INSTANTANÉ, jamais le registre vivant — sauf repli D2 ancien job)

**XLSX** (`api/v1/export.py` `build_workbook`) :
- « Résultats » : juste après « Modèle », colonnes « Identifiant du modèle » et « Version du
  modèle » (valeurs brutes, vide si absent). Les colonnes à lien (12, 13) sont avant :
  inchangées.
- « Projet » : après « Dernière analyse », pour l'étage 2 puis l'étage 1 (si pré-filtre) :
  `["Modèle (étage 2)", nom]`, `["Modèle (étage 2) · identifiant", identifiant ou
  "non enregistré"]`, `["Modèle (étage 2) · version", version ou "non enregistrée"]`,
  `["Modèle (étage 2) · détails", {endpoint, parameter_size, quantization, family, format,
  size, modified_at non vides}]` (ligne omise si vide ; `_cell` aplatit le dict) ; idem
  préfixe « Pré-filtre (étage 1) ». La ligne « Pré-filtre (étage 1) » sans pré-filtre reste
  telle qu'aujourd'hui (valeur vide). Source : `inf_job.model_info` seulement.

**RIS / BibTeX** (`_notes`) : la note se termine par
` Modèle : {model_used} ({model_identifier}), version {model_version}.` — parenthèse omise
sans identifiant ; `version non enregistrée` sans version.

**RO-Crate** (`app/crate.py`, `write_crate`) :
- `metadata.json` `ai` : `+ "model_identifier", "model_version"` (`"model"` reste le nom :
  les crates anciens et nouveaux s'importent pareil).
- `extraction.csv` : colonnes `model, model_identifier, model_version` après
  `fulltext_source`.
- `project.json` `analysis.stage_{1,2}_model` : copie de `inf_job.model_info[étage]`
  + `config_sha256` (même formule : `{name, backend, identifier}` + `endpoint` quand il est
  présent : même modèle sur un autre serveur = autre empreinte). Sans instantané (ancien
  job) : repli registre actuel mais identifiant via `public_model_ref` (+ `endpoint`) et
  `"version": None`. `write_crate` ne charge le registre que dans ce cas.
- `ro-crate-metadata.json` : `SoftwareApplication.softwareVersion` = version si connue,
  sinon l'identifiant (comportement actuel) ; `alternateName` = identifiant ; les clés à
  `None` sont omises de l'entité.
- Import (`tasks/crate_import.py`) : entrée non fiable, nettoyée par `clean_meta` (caractères
  de contrôle retirés : pas d'injection RIS/CSV) — `model_used = clean_meta(ai["model"]) or
  "RO-Crate"` (200), `model_identifier = clean_meta(ai["model_identifier"], 500)`,
  `model_version = clean_meta(ai["model_version"], 200)` ; absents → None.
- `references._bib` retire aussi les antislashs (avant l'échappement) : un `\` final
  échapperait l'accolade fermante de la note BibTeX.

**Tests** — `tests/test_team_export.py` :
`test_export_xlsx_traces_model_identifier_and_version`,
`test_export_xlsx_without_recorded_version_says_so`,
`test_export_ris_bib_notes_mention_model_and_version` ;
`tests/test_crate_import.py` :
`test_crate_export_traces_snapshot_even_if_registry_row_deleted`,
`test_crate_round_trip_restores_model_identifier_and_version`,
`test_crate_import_old_crate_without_model_version`,
`test_crate_project_json_old_job_fallback_without_url_secrets`.

## 6. Interface (frontend)

- `lib/api.ts` : `ArticlePrediction.model_identifier?/model_version?: string | null` ;
  `ModelSnapshot` (`name, backend, identifier, version` + optionnels `endpoint`,
  `parameter_size`, `quantization`, `family`, `format`, `size`, `modified_at`) ;
  `InferenceJob.model_info?: { main: ModelSnapshot | null; prefilter: ModelSnapshot | null } | null` ;
  `SearchInfo.last_job.model_version?: string | null`.
- `lib/modelVersion.ts` (nouveau) : `shortVersion(v?: string | null): string | null` —
  vide → null ; `^(sha256:)?[0-9a-f]{64}$` (insensible à la casse) → 12 premiers hex (ID
  `ollama list`) ; > 40 car. → 39 + « … » ; sinon tel quel.
- `components/ModelRef.tsx` (nouveau, évite trois copies) :
  `<ModelRef name identifier version />` → `nom (identifiant si ≠ nom) · version <abbr
  title={version complète}>{shortVersion}</abbr>` ou `version non enregistrée`
  (`useTranslations("common")`). L'`<abbr tabIndex={0}>` n'existe que si la forme abrégée
  diffère de la version ; sinon texte simple.
- Utilisé : en-tête de prédiction de `annotate/page.tsx` (remplace `{model_used}`), cartes
  de job de `inference/page.tsx` (principal + ligne « Pré-filtre » si `prefilter_model`,
  depuis `job.model_info`), `searches/page.tsx` (dernière analyse) et la carte de recherche de `projects/[id]/page.tsx`
  (`last_job.model` + `model_version`). Pas de modèle dans la
  liste des articles aujourd'hui : rien à ajouter.
- i18n (`messages/{en,fr,de,es}.json`) : `common.modelVersion` (version / version / Version
  / versión), `common.versionNotRecorded` (version not recorded / version non enregistrée /
  Version nicht erfasst / versión no registrada), `inference.prefilterShort` (Pre-filter /
  Pré-filtre / Vorfilter / Prefiltro).

**Test** : `frontend/src/lib/__tests__/modelVersion.test.ts` (formats, préfixe `SHA256:` insensible à
la casse, troncature sûre, parité 4 langues) ; `components/__tests__/ModelRef.test.tsx`.

## Hors périmètre (noté)

- Bancs d'essai (`BenchmarkRun`) : outil de choix de modèle, pas exporté ; même instantané
  (`model_info` sur le run) à ajouter si on exporte un jour leurs résultats.
- PRISMA (effectifs seulement), export RGPD (pas de prédictions) : rien à tracer.
- Détection d'un re-pull en cours de job (D3).

## Critères d'acceptation

- [ ] Tous les tests rouges listés passent ; aucun test existant cassé (suite complète verte)
- [ ] Chaque prédiction créée par un job porte identifiant + version (ou null assumé)
- [ ] Exports XLSX / CSV / RIS / BibTeX / RO-Crate lisent l'instantané ; import aller-retour
- [ ] Aucune clé API, userinfo ni query d'URL dans un instantané, un payload ou un export
- [ ] `ruff check .`, `ruff format --check .`, `tsc --noEmit`, `npm run lint`, `jest` verts

## Fichiers à modifier (implémentation)

- `backend/app/inference/__init__.py` (§1) ; `backend/app/api/v1/models.py` (import de
  `ollama_ref`, suppression de `_ollama_ref`)
- `backend/app/tasks/inference.py` (§2)
- `backend/app/db/models.py`, `backend/alembic/versions/0029_model_traceability.py` (§3)
- `backend/app/api/v1/{annotations,inference,searches}.py` (§4)
- `backend/app/api/v1/export.py`, `backend/app/crate.py`, `backend/app/tasks/crate_import.py` (§5)
- `frontend/src/lib/{api,modelVersion}.ts`, `frontend/src/components/ModelRef.tsx`,
  `frontend/src/app/projects/[id]/{annotate,inference,searches}/page.tsx`,
  `frontend/src/messages/{en,fr,de,es}.json` (§6)

Faisabilité vérifiée en passe 1 sur une copie jetable (prototype hors dépôt) : les 37 tests
backend rouges passent et les 8 fichiers de tests touchés restent verts (245 tests) ; côté jest,
les 4 tests de `shortVersion` passent (les 5 d'i18n attendent les clés).

Tests déjà écrits (passe 1) : `backend/tests/{test_inference,test_inference_task,test_models,
test_annotations_api,test_searches,test_inference_api,test_team_export,test_crate_import}.py`,
`frontend/src/lib/__tests__/modelVersion.test.ts`.
