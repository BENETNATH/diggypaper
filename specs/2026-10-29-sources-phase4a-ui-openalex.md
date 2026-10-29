# Phase 4a — Sélecteur de source en UI + adapter OpenAlex

Date : 2026-10-29 · TDD : spec → tests rouges validés → implémentation → vert → commit unique.
Préquis : Phase 3 livrée (`0efbff8`) — contrat `SourceAdapter`, registre `ADAPTERS`,
`ImportRequest.source` validé contre `ADAPTERS`, `query-volume`/`import` acceptant déjà
`source`, `fulltext_note` (colonne + UI « preprint non archivé » / « résumé seul »).
Jalon 4b (HAL) hors périmètre.

## Contexte (vérifié live, 2026-10-29)

- API `https://api.openalex.org/works` : **usage-based depuis fév. 2026** — sans clé,
  budget IP partagé gratuit ($0.10/jour, message `meta.notice` dans chaque réponse).
- `meta.count`/`meta.page`/`meta.per_page` présents dans **chaque** réponse
  (pas d'appel count séparé) ; `per_page` max **200** ; `search=` = recherche texte
  intégral ; `sort=publication_date:desc` validé.
- Les bornes dates ne sont **pas** des paramètres de requête (`from_publication_date`
  seul → 400) : elles vont dans `filter` (`from_publication_date:YYYY-MM-DD` /
  `to_publication_date:YYYY-MM-DD`, coexistant avec `search=`).
- **Pas de filtre par identifiant de work** : `filter=id:…` et `filter=ids.openalex:…`
  → 400 (« id is not a valid field », 224 champs listés, aucun id de work). En revanche
  `GET /works/{W…}` (objet unique) fonctionne → `fetch_metadata` par id un à un.
- Record work : `id` = `https://openalex.org/W…` (URL), `doi` = `https://doi.org/…`
  (URL), `display_name`, `publication_date` (YYYY-MM-DD), `language`, `type`,
  `abstract_inverted_index` (mot → positions **0-based**, à réordonner),
  `authorships[].author.display_name`, `primary_location.source.display_name`,
  `ids` : `doi` (URL), `pmid` (URL PubMed, ex.
  `https://pubmed.ncbi.nlm.nih.gov/23925242`), `pmcid` (URL PMC, selon les works).
- 429 = rate limit ; retry + throttle par process requis (pas de limiteur Redis).

## Décisions

### D1 — Adapter OpenAlex (`app/openalex/__init__.py`, source n°3)

Même squelette que `app/europepmc` (httpx injecté, `_sleep` mockable, retry backoff,
erreur assainie) :

- `OpenAlexError` propre (message SANS l'URL), retry backoff sur **429/503/5xx** +
  timeout (EPMC retrye 503 ; OpenAlex signale le rate limit par 429).
- Throttle **par process** 1 appel/s (`_INTERVAL = 1.0`) — budget IP partagé sans clé ;
  un import à la fois par projet rend le cadencement par process suffisant.
- `openalex_api_key` : nouvelle **config sécrète** (ajoutée à `SECRET_KEYS` de
  `app/settings/keys.py` — chiffrée en base, jamais renvoyée en clair) ; le caller
  (`_do_import`, `query_volume`) choisit la clé par source (`openalex_api_key` pour
  `openalex`, `ncbi_api_key` sinon — EPMC ignore la clé, `api_key` restant None) et
  la passe via la signature de fabrique commune `(client, api_key)` ; l'adapter l'envoie
  en paramètre `api_key=` (budget dédié, plus de pool IP partagé) ; sans clé :
  comportement inchangé (pool gratuit).
- Panneau de settings : nouveau groupe « OpenAlex » (champ sécrète, label + aide en
  4 langues : sans clé, budget IP partagé gratuit ; avec clé gratuite, budget dédié
  plus généreux).
- `search_volume(query, bornes)` : `GET /works?search=<q>&per_page=1` (+ `filter` bornes)
  → `int(meta.count)`.
- Bornes : ANDées dans `filter` (`from_publication_date:…` / `to_publication_date:…`),
  même sémantique que `_term_bounded` (toujours ANDées, même si la query les contient).
- `iter_ids(query, bornes, limit)` : `search=` + `filter` bornes +
  `sort=publication_date:desc` + `page=N` / `per_page=min(200, restant)` ;
  `native_id` = id work **moins** le préfixe `https://openalex.org/` (`W…`) ;
  dédoublonné (dict `seen`) ; arrêt au `limit` (cap d'import par défaut, comme EPMC)
  ou page vide / au-delà de `meta.count`.
- `fetch_metadata(ids)` : **un `GET /works/{id}` par id** (pas de batch — vérifié,
  D1) ; throttle par appel ; `ids == []` → `[]`.
- `fetch_fulltext(id)` : **toujours `None`** (source métadonnées seulement) →
  `fulltext_note` côté pipeline (D2).
- Parse work → meta (clés du contrat) :
  - `native_id` : id moins le préfixe openalex.org
  - `title` : `display_name` ; `language` ; `pub_date` : `publication_date`
  - `abstract` : reconstruction de `abstract_inverted_index` (tri par position
    0-based, mots joints par une espace) ; `None` si absente
  - `journal` : `primary_location.source.display_name` (si présente)
  - `authors` : `authorships[].author.display_name` (non vides)
  - `doi` : **sans** le préfixe `https://doi.org/` (pipeline `clean_doi` tolère les
    deux, mais la colonne reste propre)
  - `pmid` / `pmcid` : dernier segment de chemin de `ids.pmid` / `ids.pmcid`
    (URL → valeur nue, pour l'identité et le rattachement DOI) ; clés absentes si
    inconnues
  - `fulltext_note` : `"metadata_only"` (D2)
- Enregistrement : `ADAPTERS["openalex"] = OpenAlexAdapter` (registre `app.tasks`) +
  `SOURCE_NAMES["openalex"] = "OpenAlex"` (`app.sources`). L'API accepte alors
  `source="openalex"` sans autre changement (validateur `ImportRequest`).

### D2 — Pipeline : `fulltext_note = "metadata_only"`

- `_insert_article` : quand `_fulltext_for` renvoie `None` **et** que la notice porte
  `fulltext_note`, la poser sur la ligne (aucun autre effet : pas de
  `fulltext_source`/`url`).
- `_link_doi_article` : idem pour une ligne existante sans plein texte —
  `fulltext_note` ajouté au dictionnaire `fills` (rempli seulement si vide,
  jamais d'écrasement).
- `complete_fulltext_task` : inchangé (peut plus tard trouver un plein texte par
  `pmcid`/`pmid` — comportement existant conservé).
- UI relecture (`annotate/page.tsx`) : `fulltext_note === "metadata_only"` →
  `t("metadataOnlyNote")` (nouvelle clé i18n, 4 langues) ; les branches
  `preprint_not_archived` et « résumé seul » sont inchangées.

### D3 — Sélecteur de source en UI (page import)

- Onglet « Requête » : groupe de **radios** « Source » au-dessus du champ requête —
  `pubmed` (défaut), `europepmc`, `openalex` ; libellés = noms propres des sources
  (identiques dans les 4 langues, convention `sourceLabel` — pas de clé i18n par nom).
- `api.ts` : `queryVolume(projectId, query, source = "pubmed")` et
  `triggerImport(projectId, query, source = "pubmed")` — `source` ajouté au body
  (`{query, source}`) ; le backend l'accepte déjà (Phase 3).
- Volume : « Vérifier le volume » interroge la **source sélectionnée** ; affichage
  inchangé (count + cap note).
- Assistant de requête (`<details>` + `QueryAssistant`) : rendu **seulement** si
  `source === "pubmed"` (masqué Europe PMC / OpenAlex).
- Libellés « PubMed » en dur :
  - `import.queryLabel` « Requête PubMed » → « Requête » (générique, 4 langues) ;
  - `import.queryHint` : la syntaxe PubMed reste affichée pour `pubmed`, un hint
    générique (texte libre, opérateurs AND/OR) pour les autres sources (4 langues × 2) ;
  - `import.intro` et `project.howBody` : « requête PubMed » → « requête (PubMed,
    Europe PMC ou OpenAlex) » (4 langues) ;
  - `import.modePubmed` (onglet) inchangé.
- **Bug retry** : `handleRetry` appelait `triggerImport(projectId, failed.query)` —
  `source` repartait à défaut (pubmed). Correction :
  `triggerImport(projectId, failed.query, failed.source)` (`ImportJob.source` est
  déjà exposé par l'API jobs, null pour refs/citations/crate/fulltext → repli
  `"pubmed"` seulement pour `kind === "import"` ; les autres kinds ne repassent pas
  par `triggerImport`).

### D4 — RO-Crate : renommage propre `pubmed_queries` → `imports`

- `project_info` (`crate.py`) : suppression de la clé `pubmed_queries` (rédundante —
  l'alias `imports` posé en Phase 3 était le pont de compat) ; seule `imports` reste.
- Lecteur interne `crate.py` (`info["pubmed_queries"]` → `info["imports"]`) ;
  le nom `SearchAction` est déjà générique (`"Import " + source_name(source)`) —
  inchangé.
- Pas de migration : clé de fichier JSON, non versionnée côté stockage.

### D5 — Multilingue (contrainte utilisateur)

- Toutes les chaînes nouvelles en **4 langues** (fr/en/de/es) :
  `import.sourceLabel` (libellé du groupe radios), `import.queryLabel` (générique),
  `import.queryHint` (2 variantes), `import.intro`, `project.howBody`,
  `annotate.metadataOnlyNote`, `settings.openalex` + `settings.openalexKey` +
  `settings.openalexHelp` (nom des sources : noms propres, pas de traduction) ;
- Tests : jest i18n par langue (convention Phase 3, cf. `source.test.ts`) couvrant
  l'existence des nouvelles clés dans les 4 fichiers de messages.

## Tests ROUGES (à écrire avant l'implémentation)

- `backend/tests/test_openalex_adapter.py` (nouveau, MockTransport + `_sleep` mocké,
  conventions `test_europepmc_adapter.py`) :
  1. `search_volume` : params (`search`, `per_page=1`, `filter` bornes si datées) →
     `meta.count` ;
  2. `iter_ids` : pagination page 1→2 (`page`, `per_page=200`,
     `sort=publication_date:desc`), `native_id` sans préfixe, dédoublonné, arrêt au
     `limit` ; bornes dans `filter` ;
  3. `fetch_metadata` : un `GET /works/{W}` par id ; parse complet (abstract
     inversée → texte, doi sans préfixe, journal, auteurs, pub_date, language,
     `pmid`/`pmcid` nus depuis `ids`, `fulltext_note == "metadata_only"`) ;
  4. `fetch_fulltext` → `None` ;
  5. 429 → backoff + retry (sleeps ≥ intervalle) ; throttle avant chaque appel ;
  6. `OpenAlexError` sans URL (assertion sur le message) ;
  7. `api_key` posée → paramètre `api_key` sur la requête ; absente → pas de param.
- `backend/app/settings/keys.py` : `openalex_api_key` dans `SECRET_KEYS` (test :
  `is_secret("openalex_api_key") is True`, clé acceptée par l'API settings).
- `backend/tests/test_openalex_pipeline.py` (nouveau, conventions
  `test_europepmc_pipeline.py` / `test_sources`) :
  1. import `source="openalex"` (fake adapter avec meta `fulltext_note`) → articles
     `source="openalex"` / `native_id=W…`, `fulltext_note="metadata_only"`, pas de
     sections, `fulltext == 0` ;
  2. réimport même identité → 0 inséré (ON CONFLICT), note non écrasée ;
  3. rattachement DOI : work OpenAlex portant le DOI d'une ligne existante sans
     fulltext → `fulltext_note` posé, identité existante intacte.
- `backend/tests/test_source_api.py` :
  1. `query-volume` `source="openalex"` → 200 count (transport mocké, convention
     EPMC) ;
  2. 429/erreur persistante → 502 `{"error": "openalex_unavailable"}` (contrat
     `<source>_unavailable`, sans URL).
- `backend/tests/test_source_exports.py::test_source_label_and_name` : `openalex`
  devient source connue (« OpenAlex » / « OpenAlex W123 ») ; le cas « inconnu :
  code brut » passe sur `hal`.
- `backend/tests/test_source_exports3.py::test_crate_exports_imports_alias` →
  `test_crate_exports_imports` : `project.json` porte `imports` et **plus**
  `pubmed_queries` ; `test_source_exports.py:156` `info["pubmed_queries"]` →
  `info["imports"]`.
- Frontend (`frontend/src/lib/__tests__/source.test.ts` + messages) :
  1. `sourceLabel("openalex", …)` → « OpenAlex … » ; cas inconnu → `hal` ;
  2. clés i18n présentes dans les **4** fichiers : `import.sourceLabel`,
     `import.queryLabel`, `import.intro`, `project.howBody`,
     `annotate.metadataOnlyNote` (test par langue, convention Phase 3).

## Modifications de tests EXISTANTS (faites)
- `tests/test_source_exports.py::test_source_label_and_name` : `openalex` devient
  source connue (« OpenAlex » / « OpenAlex W123 ») ; le cas « inconnu : code brut »
  passe sur `hal` (4b) ;
- `tests/test_source_exports3.py::test_crate_exports_imports_alias` →
  `test_crate_exports_imports` : `imports` seul, `pubmed_queries` absent ;
- `tests/test_source_exports.py:156` : `info["pubmed_queries"]` → `info["imports"]` ;
- `tests/test_source_api.py` : fixture autouse `_no_openalex_sleep` (throttle) en plus
  du fixture EPMC existant ;
- `frontend/src/lib/__tests__/source.test.ts` : « source inconnue » → `hal` ;
  `openalex` connu (« OpenAlex W… ») ; nouveau describe « i18n phase 4a » (clés par
  langue, 4 fichiers).

## Multilingue (contrainte utilisateur, 2026-10-29)
- Chaînes nouvelles en 4 langues (fr/en/de/es) : `import.sourceLabel`,
  `import.queryLabel` (générique), `import.queryHintGeneric`, `import.intro`,
  `project.howBody`, `annotate.metadataOnlyNote`, `settings.openalex` /
  `openalexKey` / `openalexHelp` ;
- Noms de sources = noms propres identiques dans les 4 langues (PubMed / Europe PMC
  / OpenAlex), même convention backend (`SOURCE_NAMES`) et frontend (`sourceLabel`) ;
- Jest i18n par langue : présence + non-vide des clés, `queryLabel` sans « PubMed »
  en dur, `intro`/`howBody` citant les 3 sources, réellement traduites
  (« Source » excepté : mot identique fr/en, assumé).

## Faisabilité vérifiée (implémentation, 2026-10-29)
- API live : `meta.count` dans chaque réponse, `per_page` ≤ 200,
  `sort=publication_date:desc`, bornes dates dans `filter` (400 en paramètre de
  requête), `filter=id:` / `filter=ids.openalex:` → 400 (pas de batch par id) →
  `GET /works/{id}` par id pour `fetch_metadata`, `ids.pmid` / `ids.pmcid` = URLs
  (slash final inclus) → dernier segment après `rstrip("/")`.
- Tous les tests (adapter, pipeline, API, settings, exports) : 100 % offline
  (httpx.MockTransport / fake adapter, `_sleep` mocké).
- 429 = rate limit OpenAlex (pas de 503 comme EPMC) → retry 429/5xx conservé.

## Hors périmètre (jalons suivants)

- Adapter HAL (4b : recherche Solr + extracteur TEI/HTML/PDF) ;
- Rattachement DOI sur le chemin de références (`_do_import_refs`) ;
- Affinement des bornes dates (BACKLOG « bornes date ») ; assistant de requête
  multi-sources (l'assistant reste PubMed-only, masqué sinon) ; volume/cap spécifique
  par source (cap global d'import conservé).

## Critères d'acceptation

- [x] 3 sources sélectionnables en UI (défaut PubMed), volume et import passés sur la
      source choisie ; assistant masqué hors PubMed ; libellés sans « PubMed » en dur
      ; retry relance avec la `source` du job
- [x] Adapter OpenAlex : métadonnées par `GET /works/{id}`, throttle par process,
      retry 429/5xx, erreur assainie sans URL, `fetch_fulltext` → `None`
- [x] Un import OpenAlex produit des articles `source="openalex"` avec
      `fulltext_note="metadata_only"` explicite (UI i18n 4 langues)
- [x] `pubmed_queries` disparaît du RO-Crate au profit de `imports`
- [x] Multilingue : toutes les chaînes nouvelles en 4 langues, testées par langue
- [x] Garde « un import par projet » inchangée pour toutes les sources
- [x] Suite backend + frontend (pytest/ruff/tsc/jest/lint) verte ; commit unique
      spec + code + tests ; push `origin/main`
