# Phase 2 multi-sources — identité `(project_id, source, native_id)` + dédoublonnage DOI

Date : 2026-10-29 — spec d'implémentation TDD (agent de codage). Passe 1 = spec + tests
rouges (déjà écrits, NE PAS les modifier hors lint/format) ; passe 2 = les rendre verts.
Prérequis : spec `2026-10-29-tracabilite-modele-version` implémentée (migration **0029**) ;
la migration de cette phase est **0030** (`down_revision = "0029"`).

## Contexte

Jalon « Phase 2 » de `specs/2026-10-27-multi-sources-architecture.md` (§ 3, § 4) ; Phase 1
faite (`app/sources` : `SourceAdapter`, `DateBounds` ; `app.pubmed.PubMedAdapter` ;
`tasks._do_import` passe par l'adapter). Aujourd'hui : identité = `Article.pmid` + index
unique partiel `uq_articles_project_pmid` ; `_do_import` indexe les notices par `pmid`
(`by_pmid`) ; `_insert_article` lit `meta["pmid"]` et cible `ON CONFLICT (project_id, pmid)` ;
aucune colonne `source` (ni `Article`, ni `ImportJob`) ; `ImportRequest` sans `source`.
Le DOI ne dédoublonne que l'import de références (`_do_import_refs`, clés `pmid`,
`doi.lower()`, titre → `duplicates`). `Article.origin` existe déjà mais dit **comment**
l'article est entré (`pubmed` = recherche, `references`, `citation`, `crate`) — à ne pas
confondre avec `source` = **base** qui a fourni la notice et son identifiant.

## Décisions

**D1. Schéma** (migration additive, `pmid` ni renommé ni supprimé — contrainte d'archi) :
- `Article.source` (String, NULL) et `Article.native_id` (String, NULL). NULL = donnée
  ancienne sans PMID (références Crossref/fichier, crate) dont la base n'a jamais été
  notée → « non enregistrée », comme `model_version`. Toute insertion nouvelle pose
  `source` (tests).
- Index unique partiel `uq_articles_project_source_native` sur
  `(project_id, source, native_id)` `WHERE native_id IS NOT NULL` (PG + SQLite).
- **On GARDE `uq_articles_project_pmid`** (écart assumé à l'archi, qui le retirait) : le PMID
  est un identifiant universel que d'autres sources fournissent (Europe PMC, OpenAlex) ; le
  garder donne un dédoublonnage inter-sources **garanti par la base**, sans coût (index
  existant), et rend la migration purement additive (downgrade trivial, pas de fenêtre sans
  unicité). Pour `source='pubmed'` le triplet lui est équivalent.
- **Pas d'index sur le DOI normalisé** : un seul import à la fois par projet (garde
  globale) → l'import précharge les DOI du projet en mémoire (comme `_do_import_refs` le
  fait déjà) ; aucune requête par article, aucun index d'expression PG/SQLite à maintenir.
- `ImportJob.source` (String, NULL) : base interrogée. `'pubmed'` pour la recherche et le
  chaînage de citations (iCite + PubMed) ; NULL pour un import de références (sources
  mêlées, détail par article + `result_json.sources`), un crate, le plein texte.
- Valeurs de `source` : `pubmed` (`native_id` = PMID), `crossref` (`native_id` = DOI
  normalisé), `file` (`native_id` NULL) ; les phases 3-4 ajoutent `europepmc`, `hal`,
  `openalex`.

**D2. Migration `0030_article_source_identity.py`** (`revision "0030"`, down `"0029"`) :
upgrade = 3 `add_column` ; `UPDATE articles SET source='pubmed', native_id=pmid WHERE pmid IS
NOT NULL` ; `UPDATE import_jobs SET source='pubmed' WHERE kind IS NULL OR kind IN ('import',
'citations')` ; `create_index` du triplet (`postgresql_where` + `sqlite_where`). Sûre sur
données existantes : l'unique `(project_id, pmid)` garantit l'absence de doublon du triplet
backfillé ; les lignes sans PMID restent hors index. Downgrade = `drop_index` puis
`drop_column` ×3 (ordre : l'index avant ses colonnes, exigé par SQLite). SQL portable
PG/SQLite (testé sur SQLite réel, upgrade → downgrade → upgrade).

**D3. Insertion** (`tasks._insert_article`, point de passage de TOUTES les insertions
d'import) : `values["source"] = meta.get("source") or ("pubmed" if meta.get("pmid") else
None)` ; `values["native_id"] = meta.get("native_id") or (meta.get("pmid") if source ==
"pubmed" else None)` ; `pmid = meta.get("pmid")` (plus de `KeyError` sans PMID). Conflit :
`.on_conflict_do_nothing()` **sans cible** : toute violation d'unicité — PMID ou triplet —
est ignorée (SQLite : vérifié en prototype ; PG : « conflict_target omis → toutes les
contraintes et index uniques », doc `INSERT … ON CONFLICT`).

**D4. Recherche par source** (`_import_pubmed(…, source="pubmed")`, `_do_import`) :
- registre unique `ADAPTERS = {"pubmed": PubMedAdapter}` dans `app/tasks/__init__.py`
  (fabrique `(client, api_key) -> SourceAdapter`) ; adapter injecté (tests) prioritaire ;
  source inconnue sans adapter → `LookupError("unknown source")` → job FAILED ;
- `job.source = None if refs is not None else source` ;
- notices indexées par `native_id` (contrat Phase 1) au lieu de `pmid` ;
  `meta |= {"source": source, "native_id": …, "origin": source, "origin_ref": …}` (origin
  « pubmed » inchangé pour PubMed ; phase 3 : `europepmc`).
- wrapper Celery `import_pubmed_task(…, source="pubmed")` propage `source=` au core (nom
  de tâche inchangé).

**D5. Dédoublonnage DOI** (`_do_import`) : normalisation = `refimport.clean_doi` existant
(minuscules, préfixes `https://doi.org/`, `dx.doi.org`, `doi:` retirés, ponctuation finale).
Avant l'insertion (donc **avant** l'appel plein texte), une notice dont le DOI normalisé
appartient déjà à un article du projet d'**une autre identité** `(source, native_id)` — y
compris dans le même import — n'est pas insérée : `duplicates_doi += 1`, l'article existant
garde son origine et ses champs (pas de fusion : YAGNI, la complétion du plein texte
`complete_fulltext_task` traite l'existant). Même identité (ré-import) → chemin actuel
(`ON CONFLICT`, ni importé ni doublon DOI). Préchargement : une requête
`(doi, source, native_id)` du projet. `result_json.duplicates_doi` n'apparaît **que s'il est
> 0** : payload de stats inchangé pour tous les imports existants (une dizaine de tests
d'égalité exacte dans `test_tasks_import.py`). `_do_import_refs` : `clean_doi` au lieu de
`doi.lower()` dans `keys()`, `meta |= {"source": source, "native_id": pmid | DOI | None}`
(sa variable `source` ∈ pubmed/crossref/file) ; ses doublons restent dans `duplicates`.
PRISMA (`prisma_counts`) : `identified += duplicates_doi` (les doublons DOI deviennent des
« doublons retirés » ; jobs anciens sans la clé : inchangé).

**D6. API** : `ImportRequest.source: str = "pubmed"`, validé `in ADAPTERS` (422 sinon) — vaut
pour `POST /import` et `POST /query-volume` (même modèle ; le volume reste PubMed tant
qu'il n'y a qu'un adapter) ; `trigger_import` passe `source=payload.source` à `.delay`.
`GET /jobs` : `+ "source"`. `biblio_payload` (partagé liste + détail d'article) :
`+ "source", "native_id"`. Pas d'info de l'IA → rien à masquer en aveugle.

**D7. Libellés** (`app/sources/__init__.py`, module pur) :
`SOURCE_NAMES = {"pubmed": "PubMed", "crossref": "Crossref", "file": "Fichier importé"}`,
`source_name(code)` (inconnu → code brut, None → None),
`source_label(code, native_id)` → « PubMed PMID 123 », « Crossref DOI 10.1/x »,
« Fichier importé », `europepmc` inconnu → « europepmc W1 » ; None si pas de source.

**D8. iCite / chaînage de citations : reste PubMed-only** (API iCite = PMID) : graines =
articles avec `pmid` (inchangé, `api/v1/citations.py`), candidats résolus via PubMed →
`source='pubmed'`. Les articles d'autres sources sans PMID ne peuvent pas être graines (déjà
le cas des articles sans PMID ; l'UI le dit : « article(s) de départ avec PMID »).

**D9. Lecteurs de `Article.pmid` (grep exhaustif, inchangés)** : `fulltext.py`
(`refresh_article_metadata`, `complete_article` : PubMed/EPMC par PMID/DOI),
`tasks.backfill_metadata`, `citations.py` + `api/v1/citations.py`, `crate.py`
(`article_key`, graphe, `article_record`), `references.py`, `export.build_workbook`,
`api/v1/projects.py` (liste, `search_blob`), `annotations._article_payload`,
`query_assistant` (PubMed seul). `pmid` reste posé pour PubMed → tous continuent de
marcher ; ils ignorent simplement les articles sans PMID, comme aujourd'hui.

## 1. Modèle + migration
Fichiers : `app/db/models.py` (`Article`, `ImportJob`, commentaire `origin` : « <source>
(recherche) »), `alembic/versions/0030_article_source_identity.py`.
Tests (`tests/test_source_identity.py`) : `test_article_and_import_job_source_columns`,
`test_article_source_native_unique_index`, `test_triplet_unique_enforced_null_native_free`,
`test_pmid_unique_index_kept` (garde, vert), `test_migration_0030_chain_after_0029`
(rouge tant que 0029 **et** 0030 n'existent pas : `ScriptDirectory.get_heads() == ["0030"]`),
`test_migration_0030_backfill_and_downgrade_on_sqlite`.

## 2. Import (tasks)
Tests (`tests/test_source_identity.py`) : `test_pubmed_import_sets_source_native_id_and_job_source`,
`test_non_pubmed_source_keyed_on_native_id_without_pmid`,
`test_reimport_same_triplet_without_pmid_no_duplicate`,
`test_same_native_id_other_source_is_distinct`,
`test_unknown_source_without_adapter_fails_job`,
`test_refs_import_sets_source_per_resolution`, `test_task_wrapper_forwards_source`.
(`tests/test_source_dedup.py`) : `test_doi_duplicate_other_identity_skipped_counted_no_fulltext`,
`test_doi_duplicate_within_same_import`,
`test_reimport_same_identity_with_doi_is_not_doi_duplicate` (garde),
`test_import_without_doi_duplicate_keeps_stats_payload` (garde),
`test_clean_doi_normalizes_prefixes_and_case` (garde), `test_prisma_counts_doi_duplicates`.

## 3. API
Tests (`tests/test_source_api.py`) : `test_import_source_defaults_to_pubmed_and_is_forwarded`,
`test_import_unknown_source_422`, `test_query_volume_unknown_source_422`,
`test_jobs_list_exposes_source`, `test_article_payloads_expose_source_and_native_id`.

## 4. Exports (même conventions que modèle/version)
- **XLSX** `build_workbook` « Résultats » : colonnes « Source » (`source_label`) et
  « Identifiant source » (`native_id`) **juste après « Lien texte intégral / PDF »** (les
  indices codés en dur 4, 5, 6, 9, 12, 13 — largeurs, retour à la ligne, liens — ne
  bougent pas) ; vides pour l'ancien. « Projet » : en-tête de section
  `["Imports", "Date · source · articles importés"]` (au lieu de « Requêtes PubMed ») ;
  valeur `f"{date} · {libellé} · {imported}"`, libellé = `source_name(j.source)`, sinon
  « sources par article » si `kind in ("references", "crate")` (sources mêlées, voir la
  colonne Source), sinon « non enregistrée ».
- **RIS** (`references.to_ris`) : `DB  - {source_name}` (tag RIS « base de données ») ;
  **BibTeX** : champ `source = {source_label}` (comme les champs `pmid`/`pmcid` déjà
  présents) ; omis sans source.
- **RO-Crate** (`crate.py`) : `metadata.json` `+ "source", "native_id"` ;
  `extraction.csv` colonnes `source, native_id` **après `doi`** (l'en-tête commence
  toujours par `key,pmid`) ; `project.json` `pubmed_queries[i] + "source"` (clé gardée pour
  compat ; renommage → phase 4) ; graphe `SearchAction.name = f"Import {source_name}"`
  (« Import » seul sans source).
- **Import crate** (`crate_import.read_article`) : `source = _clean(meta["source"], 50)`,
  `native_id = _clean(meta["native_id"], 200)` ; crate ancien sans `source` → `pubmed` +
  PMID si `pmid`, sinon None/None.
Tests (`tests/test_source_exports.py`) : `test_xlsx_results_source_columns`,
`test_xlsx_projet_sheet_import_sources`, `test_ris_db_tag_and_bibtex_source_field`,
`test_crate_exports_source_and_job_source`,
`test_crate_import_restores_source_and_defaults_old_crates`,
`test_source_label_and_name` (pur).

## 5. Interface (frontend, minimal — pas les libellés « PubMed » en dur)
- `lib/api.ts` : `Article.source?/native_id?: string | null` ; `ImportJob.source?`.
- `lib/source.ts` (nouveau) : `sourceLabel(source, nativeId, fileLabel): string | null`
  (mêmes règles que D7 ; `file` → libellé traduit fourni).
- `components/ResultRow.tsx` : `a.pmid ? PMID … : doi` → `sourceLabel(…) ?? a.doi ?? ""` ;
  page d'annotation : ligne de source sous la ligne bibliographique,
  `sourceLabel(…) ?? t("sourceNotRecorded")`.
- i18n `articles.sourceFile` (imported file / fichier importé / importierte Datei /
  archivo importado), `articles.sourceNotRecorded` (source not recorded / source non
  enregistrée / Quelle nicht erfasst / fuente no registrada).
Test : `frontend/src/lib/__tests__/source.test.ts` (5 cas de `sourceLabel` + parité et
traduction des 2 clés).

## Modifications de tests EXISTANTS à faire par l'implémenteur (non faites en passe 1)
- `tests/test_models.py::test_articles_columns` : `+ "source", "native_id"` ;
  `::test_import_job_columns` : `+ "source"` (le fichier est aussi touché par la spec
  traçabilité).
- `tests/test_projects_api.py::test_import_triggers_task` :
  `assert_called_once_with(project_id, "cancer AND mice", date_from=None, date_to=None,
  source="pubmed")`.
- Aucun autre : `test_tasks_import` (payload inchangé sans doublon DOI), `test_sources`
  (fakes avec `native_id`), `test_refimport` (sources/doublons inchangés), RO-Crate
  (`key,pmid` en tête), RIS/BibTeX (ajouts), PRISMA (clé absente des anciens jobs).

## Phase 3 / 4 — ce qui reste pour que l'UI et les exports ne mentent pas
- Import (`import/page.tsx`, messages `import.*`) : `modePubmed`, `queryLabel` « Requête
  PubMed », `queryHint` (syntaxe PubMed), `phaseSearching` « identifiants PubMed »,
  `intro`, `refsHint` « complétée via PubMed », `project.howBody` → nom de la source choisie ;
  sélecteur de source (radios) + `source` dans `importPubmed`/`queryVolume`.
- Assistant de requête (`QueryAssistant`, `qa*`) : PubMed seul → masqué pour une autre source.
- `query_volume` : appeler l'adapter de la source (aujourd'hui `search_volume` PubMed).
- `articles.searchPlaceholder` (« PMID ») et `search_blob` (0028) : inclure `native_id`.
- PRISMA `identified` « (PubMed, imported lists) » (`prisma.py`, 4 langues) → sources réelles.
- RO-Crate : `pubmed_queries` → `imports` (alias), README « requêtes PubMed exactes »,
  `article_key` `<source>-<native_id>` hors PubMed, RIS `AN` pour un `native_id` non PMID.
- `layout.tsx` description « (PubMed/PMC) » ; `ResultRow` repli « PMID ».
- Rattachement DOI avec complétion des champs manquants (archi § 3) : non fait (D5).

Faisabilité vérifiée en passe 1 sur une copie jetable (prototype hors dépôt, supprimé) : les
26 tests backend rouges passent, et avec les 3 modifications listées plus haut 240 tests de
`test_{tasks_import,sources,refimport,projects_api,team_export,crate_import,models,citations,
pubmed,searches,annotations_api,fulltext,gdpr}.py` restent verts ; jest : les 5 cas de
`sourceLabel` passent (les 5 d'i18n attendent les clés). NB : les DOI de test sont réalistes
(`10.1000/…`) — `clean_doi` exige 4 à 9 chiffres après `10.`.

## Critères d'acceptation
- [ ] Tests rouges listés verts ; tests existants verts après les 3 modifications listées
- [ ] Ré-import PubMed : mêmes lignes, même payload de stats ; doublon DOI d'une autre
      identité non inséré, compté `duplicates_doi`, origine de la première conservée
- [ ] Migration 0030 réversible, backfill `pubmed`/PMID, sûre (pas de doublon possible)
- [ ] Source visible : XLSX, RIS/BibTeX, RO-Crate (+ import), API liste/détail/jobs, UI
- [ ] `ruff check .`, `ruff format --check .`, `tsc --noEmit`, `npm run lint`, `jest` verts

## Fichiers à modifier (implémentation)
`backend/app/db/models.py`, `backend/alembic/versions/0030_article_source_identity.py`,
`backend/app/sources/__init__.py`, `backend/app/tasks/__init__.py`,
`backend/app/api/v1/projects.py` (`ImportRequest`, `trigger_import`, `list_jobs`,
`biblio_payload`), `backend/app/prisma.py`, `backend/app/api/v1/export.py`,
`backend/app/references.py`, `backend/app/crate.py`, `backend/app/crate_import.py` ;
`frontend/src/lib/{api,source}.ts`, `frontend/src/components/ResultRow.tsx`,
`frontend/src/app/projects/[id]/annotate/page.tsx`, `frontend/src/messages/{en,fr,de,es}.json`.
Tests (passe 1) : `backend/tests/test_source_{identity,dedup,api,exports}.py`,
`frontend/src/lib/__tests__/source.test.ts`.
