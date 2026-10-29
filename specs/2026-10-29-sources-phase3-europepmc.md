# Phase 3 multi-sources — adapter Europe PMC + rattachement DOI + exports qui ne mentent plus

Date : 2026-10-29 — spec d'implémentation TDD (agent de codage). Passe 1 = spec + tests
rouges ; passe 2 = les rendre verts.
Prérequis : phases 1-2 implémentées (contrat `SourceAdapter`, migration **0030** : identité
`(project_id, source, native_id)`). La migration de cette phase est **0032**
(`down_revision = "0031"`).

## Contexte

Jalon « Phase 3 » de `specs/2026-10-27-multi-sources-architecture.md` (§ 3, § 5) : adapter
Europe PMC (recherche + métadonnées + plein texte JATS via le parseur existant), sélection
de la source côté API, `fulltext_source='europepmc'` ; **pas d'UI de choix dans cette phase**
(source fixée dans le payload — l'UI est le jalon 4a). Périmètre validé avec l'utilisateur
(2026-10-29) : + le **rattachement DOI avec complétion des champs manquants** (archi § 3,
écarté en phase 2 D5 « YAGNI » — l'utilisateur a demandé de l'inclure ici) ; + les
**corrections d'exports/UI « qui mentent »** dès qu'un article non-PubMed existe (PRISMA,
`search_blob`, RIS `AN`, RO-Crate `article_key`/`pubmed_queries`, description du layout,
placeholder de recherche).

Faites API Europe PMC vérifiées en direct (2026-10-29, IP datacenter) :
- `GET /search?query=…&format=json&resultType=core|lite&pageSize=N&sort=FIRST_PDATE+desc&cursorMark=*`
  → `{hitCount, resultList: {result: […]}, nextCursorMark, nextPageUrl}` ; le curseur suivant
  se lit dans **`nextCursorMark`** (pas « nextCursorMarker ») ; `pageSize` ≤ 1 000 ;
  bornes dates fonctionnelles : `FIRST_PDATE:[YYYY-MM-DD TO YYYY-MM-DD]` (AND dans la query) ;
  la syntaxe requête PubMed passe telle quelle (les tags `[mesh]` sont acceptés, le volume
  diffère de PubMed — c'est une autre base, attendu).
- Les champs d'une notice `resultType=core` (vérifiés sur des réponses réelles) : `id`
  (l'id du record EPMC — `PMC…` pour les records indexés PMC, `W…` pour les records
  natifs EPMC, numéro brut pour certains — **c'est l'id canonique du record, unique par
  record**), `pmid`, `pmcid`, `doi`, `title`, `abstractText`,
  `authorList.author[].fullName`, `firstPublicationDate` (`YYYY-MM-DD`), `dateOfPublication`,
  `pubYear`, `journalInfo.title`, `language`, `fullTextIdList.fullTextId[]`
  (ids pour lesquels le plein texte est disponible ; absent si non-OA),
  `fullTextUrlList.fullTextUrl[].reader.url` (URLs des lecteurs).
- `GET /{id}/fullTextXML` → XML JATS (DTD `JATS-archivearticle`, balises
  `article/front/body/sec/title/abstract/p`) — le parseur JATS existant
  (`app.pubmed._extract_methods`/`_extract_sections`) l'ingère tel quel (vérifié sur une
  réponse réelle, 110 Ko).
- Rate limit par IP non documenté : ~5 requêtes rapides depuis une IP datacenter → 503
  pendant plusieurs minutes (observé). → throttle conservateur par process + retry.

Aujourd'hui (état post-phase-2) : `_do_import` **saute** la notice dont le DOI normalisé
appartient à une autre identité (`duplicates_doi += 1`, rien n'est écrit — écart D5 assumé) ;
`query_volume` (api/v1/projects.py:586) est codé PubMed en dur (`search_volume` de
`app.pubmed` + `NCBIError` → 502 `ncbi_unavailable`) ; `search_blob()` (projects.py:639) =
titre/journal/`pmid`/doi/auteurs, indexée par l'index trigramme 0028 (PG-only, l'expression
doit rester identique) ; RIS `AN` = `PMID:{pmid}` seulement (references.py:45) ;
`crate.article_key` = pmid → doi → `art-<uuid8>` (un article `W…` sans DOI retombe sur
l'UUID) ; `crate.project.json` exporte `pubmed_queries` (nom trompeur dès qu'un job
`europepmc` existe) ; PRISMA `identified` = libellé statique « (PubMed, imported lists) »
×4 langues (prisma.py:16/56/96/136) ; `layout.tsx` décrit l'app « (PubMed/PMC) » ;
`articles.searchPlaceholder` cite « PMID » (4 langues).

## Décisions

**D1. Adapter** `EuropePMCAdapter` dans `app/europepmc/__init__.py` (nouveau module, même
forme que `app/pubmed`) : fabrique `(http, api_key)` (clé ignorée — API sans clé),
`http` injecté (tests), `EpmcError` assainie (message SANS l'URL, pattern `NCBIError`),
retry backoff sur 503/timeout (pattern `_get_with_retry`), limite de taille 10 Mo sur le
XML (I5). **Throttle par process : 1 appel/s** (constant `_INTERVAL = 1.0`, `asyncio.sleep`
mocké en tests) — le plafond par IP est faible et non documenté ; contrainte de déploiement
(documentée comme M-3 PubMed : worker d'ingestion dédié, un import à la fois par projet).
Pas de limiteur Redis (choix d'archi).
- `search_volume(query, bornes)` : `GET /search` `resultType=lite`, `pageSize=1` →
  `hitCount` (int). Bornes : `(query) AND FIRST_PDATE:[lo TO hi]` (lo/hi absents →
  `1800-01-01`/aujourd'hui+366j, ANDées même si déjà dans la query — même sémantique que
  `_term_bounded` PubMed).
- `iter_ids(query, bornes, limit)` : pagination par curseur — `cursorMark=*` puis le
  `nextCursorMark` de chaque réponse ; `pageSize=1000` ; `sort=FIRST_PDATE desc`
  (les plus récents d'abord) ; cap `limit` (défaut import : `import_max_articles`) ;
  dédoublonnage par `id` (dict, ordre de première occurrence). Le count de
  `search_volume` est caché et réutilisé si disponible (pattern `PubMedAdapter`) ; un
  `iter_ids` sans count préalable fait un `/search` `pageSize=1` en plus.
- `fetch_metadata(ids)` : par tranches de 100, `GET /search`
  `query=id:(«id1» OR «id2» …)` (ids entre guillemets — les ids EPMC sont des chaînes
  libres), `resultType=core`, `pageSize` = taille de tranche. `ids == []` → `[]`. Mapping
  (champs vérifiés) :
  - `native_id` = `id` (l'id canonique du record, tel quel) ;
  - `pmid` (str, absente si null), `pmcid`, `doi` (absente si null) ;
  - `title`, `abstract` (`abstractText`), `authors` = `fullName` des auteurs
    (triangles absents → None), `journal` = `journalInfo.title`,
    `pub_date` = `firstPublicationDate` ?? `dateOfPublication` ?? `str(pubYear)` (None si
    aucun), `language` ;
  - `fulltext_id` = 1ᵉʳ `fullTextIdList.fullTextId` (absent si null) : id du plein texte ;
  - `fulltext_url` = 1ᵉʳ `fullTextUrlList.fullTextUrl[].reader.url` (absent si null) ;
  - `fulltext_source` = `'europepmc'`.
- `fetch_fulltext(id)` : `GET /{id}/fullTextXML` → `_extract_methods`/`_extract_sections`
  **importés depuis `app.pubmed`** (réutilisation imposée par l'archi — pas de nouveau
  parseur). `None` si 404/403 (pas de texte) ; `(methods, sections)` sinon.
- `SOURCE_NAMES["europepmc"] = "Europe PMC"` (module `app.sources`, pur) ; pas
  d'`_ID_KINDS` → `source_label` = « Europe PMC W123 » (l'id est affiché tel quel).
- Enregistrement : `ADAPTERS = {"pubmed": PubMedAdapter, "europepmc": EuropePMCAdapter}`
  (app/tasks) → `ImportRequest.source` (validation `in ADAPTERS`) et `query_volume`
  l'acceptent sans autre changement de code. iCite/chaînage de citations : inchangé,
  PubMed-only (API iCite = PMID ; les articles sans PMID ne peuvent pas être graines —
  déjà le cas, D8 phase 2).

**D2. Plein texte du pipeline par clé `fulltext_id`** (`tasks._insert_article`) : l'id du
plein texte devient `meta.get("fulltext_id") or meta.get("pmcid")` (PubMed : `fulltext_id`
absent → `pmcid` → **comportement inchangé** ; EPMC : id EPMC/PMC de la notice). Le chemin
`pmcid` (préprint non archivable, `fulltext_source='pmc'`, URL PMC) reste le seul chemin
touchant la heuristique `pmc_not_archived`/`long_enough` ; le chemin `fulltext_id` pose
`fulltext_source`/`fulltext_url` depuis la notice (`'europepmc'` + URL lecteur, absentes si
la notice n'a pas de lecteur). `fulltext_note` : inchangé (aucune note posée ici — la
complétion `complete_fulltext_task` traite l'existant).

**D3. Rattachement DOI avec complétion (archi § 3)** — `_do_import`, à la place du saut D5 :
une notice dont le DOI normalisé appartient déjà à une **autre identité** du projet
(`doi_owners` préchargé + identités du même import, logique existante) **rattache** la
notice à la ligne existante au lieu de créer un doublon :
- cible : la **première** ligne existante (ordre `Article.id`) parmi les identités
  porteuses du DOI (plusieurs sources peuvent porter le même DOI : pubmed + file…) ;
- **complète uniquement les champs vides** de la ligne existante avec la notice entrante :
  `pmid`, `pmcid`, `title`, `abstract`, `journal`, `pub_date`/`pub_year`, `authors`,
  `original_language` ; une valeur existante non vide n'est **jamais** écrasée (pas de
  fusion de valeurs, pas de merge de listes) ; le DOI existant est laissé tel quel
  (déjà le même, normalisé) ;
- **plein texte** : si la ligne n'a pas de `sections`, il est cherché avec le
  `fulltext_id`/`pmcid` de la notice entrante (même mécanique que D2) ; ligne déjà
  pourvue → **aucun appel** plein texte ;
- identité de la ligne existante intacte : `source`/`native_id`/`origin`/`origin_ref`/
  `origin_seeds` ne sont jamais touchés ;
- compté `duplicates_doi += 1` (le payload de stats est inchangé — clé absente si 0 ;
  PRISMA `identified += duplicates_doi` déjà en place) ;
- chemin de références (`_do_import_refs`) : inchangé (son propre dédoublonnage, archi § 4
  l'exclut du choix de source).
- Cas particulier : re-import même identité (`ON CONFLICT` sur le triplet) → chemin
  actuel (ni importé, ni doublon DOI, ni rattachement) — inchangé.

**D4. `query_volume` par source** (api/v1/projects.py) : l'adapter vient de
`ADAPTERS[payload.source]` (fabrique `(client, api_key)`, `api_key` = `ncbi_api_key` —
ignoré par Europe PMC), client `_make_http_client()`,
`adapter.search_volume(payload.query, DateBounds(payload.date_from, payload.date_to))`.
Erreur assainie de la source → 502 : `{"error": "ncbi_unavailable"}` **conservé pour
`pubmed`** (contrat existant, frontend), `{"error": "europepmc_unavailable"}` pour
`europepmc` (le frontend affiche un message générique pour tout code inconnu — vérifié).
Docstring « Volume de résultats de la source (défaut PubMed) ».

**D5. `search_blob` + index (0032)** : `search_blob()` ajoute `native_id` **après
`pmid`** (titre, journal, pmid, **native_id**, doi, auteurs) ; docstrings `search_blob`/
`text_conditions` mis à jour (« identifiant » au lieu de « PMID »). Migration
`0032_search_native_id.py` (`revision "0032"`, down `"0031"`, PG-only comme 0028) :
`DROP INDEX ix_articles_search_trgm` puis `CREATE INDEX` sur la nouvelle expression
(identique à `search_blob()`, sinon l'index ne sert plus). SQLite : rien (l'index est
PG-only ; l'expression est testée tel quel en SQLite via les tests de recherche).

**D6. Exports qui ne mentent plus** :
- **RIS** (`references.to_ris`) : `AN` = `PMID:{pmid}` (inchangé) si `pmid`, sinon
  `AN - {source_label(source, native_id)}` si `native_id`, sinon rien (inchangé).
- **RO-Crate** (`crate.py`) :
  - `article_key` : `pmid-…` (inchangé) → `doi-…` (inchangé) → `{source}-{native_id}`
    (source = code, `art` si source null) sanitisée (`[^0-9A-Za-z]` → `-`, 60 caractères,
    bords retirés) si `native_id` → `art-<uuid8>` (repli final inchangé). Le DOI reste
    préféré à l'id natif (identité inter-sources plus stable).
  - `project.json` + `metadata.json` : clé `pubmed_queries` **gardée** (compat lecture
    crate) + clé **`imports`** en alias (même contenu) ; README : « requêtes PubMed
    exactes » → « requêtes d'import (source, requête, bornes) ».
- **PRISMA** (`prisma.py`) : libellé `identified` construit dynamiquement par langue :
  sources de recherche réellement présentes (jobs COMPLETED, `job.source` non nulle,
  `source_name`, ordre d'apparition : pubmed d'abord, puis l'ordre des jobs) +
  « listes importées »/« imported lists »/« importierte Listen »/« listas importadas » si
  des jobs `references`/`crate` existent ou des articles sans source. Cas pubmed seul +
  refs → « (PubMed, listes importées) » **identique à l'actuel** (tests statiques
  existants verts sans changement) ; un job `europepmc` → « (PubMed, Europe PMC, listes
  importées) » ; sans refs → pas de partie « listes importées ». Les 3 autres cas
  (`retrieved`, `included`…) sont inchangés.
- **`layout.tsx`** : description « (PubMed/PMC) » → « (PubMed, Europe PMC et al.) ».
- **`articles.searchPlaceholder`** (4 langues) : « PMID » → « identifiant » (fr :
  « Rechercher un article : titre, auteur, journal, identifiant, DOI… » ; en : « Search
  articles: title, author, journal, identifier, DOI… » ; de : « Artikel suchen: Titel,
  Autor, Zeitschrift, Identifikator, DOI… » ; es : « Buscar artículos: título, autor,
  revista, identificador, DOI… »). `ResultRow` : rien à faire (phase 2 a mis
  `sourceLabel` ; l'item « repli PMID » est clos).

## 1. Adapter Europe PMC
Fichiers : `app/europepmc/__init__.py` (nouveau), `app/sources/__init__.py`
(`SOURCE_NAMES`), `app/tasks/__init__.py` (`ADAPTERS`).
Tests (`tests/test_europepmc_adapter.py`, http mocké, `_sleep` mocké) :
- `test_search_volume_params_and_count` (params : `format=json`, `resultType=lite`,
  `pageSize=1`, query bornée ; `hitCount` → int) ;
- `test_search_volume_date_bounds` (`FIRST_PDATE:[… TO …]` dans la query, bornes
  ouvertes → non bornée) ;
- `test_iter_ids_cursor_pagination` (`cursorMark=*` → `nextCursorMark` → 2ᵉ page →
  fin ; les plus récents d'abord (`sort=FIRST_PDATE desc`) ; dédoublonnage d'un id
  renvoyé deux fois) ;
- `test_iter_ids_limit_and_cached_count` (cap `limit` ; si `search_volume` a passé avant
  → aucun 3ᵉ `/search` de count ; sinon un fallback `pageSize=1`) ;
- `test_fetch_metadata_mapping` (`id` → `native_id`, `pmid`, `pmcid`, `doi`, `title`,
  `abstract`, `authors` (`fullName` joinés), `journal`, `pub_date`
  (`firstPublicationDate` → repli `dateOfPublication` → repli `pubYear`), `language`,
  `fulltext_id`, `fulltext_url`, `fulltext_source='europepmc'` ; query
  `id:(… OR …)` ; une notice sans `doi`/`pmcid`/`fullTextIdList` → clés absentes) ;
- `test_fetch_metadata_chunking_and_empty` (250 ids → 3 appels de 100/100/50 ;
  `[]` → `[]` sans appel) ;
- `test_fetch_fulltext_jats` (`/{id}/fullTextXML` ; parseur existant : `(methods,
  sections)` non vides sur un JATS minimal) ;
- `test_fetch_fulltext_unavailable_404` → `None` ;
- `test_fetch_fulltext_size_limit` (XML > 10 Mo → `EpmcError`, jamais le XML) ;
- `test_throttle_and_retry_503` (sleep entre chaque appel ; 503 → retry backoff →
  succès ; 503 persistant → `EpmcError('HTTP 503')`) ;
- `test_errors_sanitized` (403 → `EpmcError('HTTP 403')`, SANS l'URL dans le message) ;
- `test_registered_in_adapters_and_source_names` (`ADAPTERS['europepmc']` est la fabrique
  ; `SOURCE_NAMES['europepmc'] == 'Europe PMC'` ; `source_label('europepmc', 'W123') ==
  'Europe PMC W123'`).

## 2. Pipeline : plein texte par `fulltext_id` + rattachement DOI
Fichiers : `app/tasks/__init__.py` (`_insert_article`, `_do_import`, nouveau
`_link_doi_article`).
Tests (`tests/test_europepmc_pipeline.py`, fake adapter EPMC ou adapter réel + http mocké,
conventions `test_tasks_import`/`test_source_dedup`) :
- `test_europepmc_import_sets_identity_and_fulltext` (`source='europepmc'`,
  `native_id='W…'`, `pmid` posé quand la notice en porte un, `fulltext_source='europepmc'`,
  `fulltext_url` de la notice, sections JATS dans `Article.sections`) ;
- `test_europepmc_non_oa_no_fulltext_call` (notice sans `fulltext_id` → aucun appel
  `/fullTextXML`, pas de `fulltext_note`) ;
- `test_pubmed_insert_path_unchanged` (gardes : `pmcid` → `fulltext_source='pmc'` + URL
  PMC + heuristique préprint — chemin existant inchangé) ;
- (`tests/test_source_link_doi.py`) `test_doi_duplicate_other_identity_links_and_fills`
  (ligne `pubmed` existante sans `abstract`/`journal`/`pmcid`/sections ; notice
  `europepmc` du même DOI → ligne existante : champs vides remplis, sections posées,
  `fulltext_source='europepmc'` ; `source`/`native_id`/`origin` de la ligne existante
  intacts ; aucune nouvelle ligne ; `duplicates_doi == 1` dans le payload) ;
- `test_link_never_overwrites_existing_values` (titre/abstract existants non vides →
  conservés tels quels, pas écrasés par la notice entrante) ;
- `test_link_skips_fulltext_when_row_has_sections` (ligne existante déjà pourvue → zéro
  appel plein texte) ;
- `test_link_prefers_smallest_article_id` (deux lignes porteuses du DOI → celle au plus
  petit `id` est complétée) ;
- `test_reimport_same_identity_not_linked` (gardes : même triplet → `ON CONFLICT`,
  `duplicates_doi` absent du payload, pas de rattachement) ;
- `test_link_no_doi_plain_insert` (notice sans DOI → insertion normale,
  `duplicates_doi` absent) ;
- `test_doi_duplicate_same_identity_within_import_still_skipped` (gardes phase 2 : deux
  notices du même DOI **même source** dans un import → la 2ᵉ est sautée
  `duplicates_doi`, pas rattachée — le rattachement n'est qu'inter-identités).

## 3. API
Fichiers : `app/api/v1/projects.py` (`query_volume`).
Tests (`tests/test_source_api.py`, ajouts) :
- `test_query_volume_europepmc` (`source='europepmc'` → `/search` EPMC appelé (host
  `ebi.ac.uk`), aucun appel `eutils.ncbi.nlm.nih.gov`, `DateBounds` porté) ;
- `test_query_volume_europepmc_error_502` (503 EPMC persistant → 502
  `{"error": "europepmc_unavailable"}`, SANS l'URL) ;
- `test_query_volume_pubmed_contract_unchanged` (gardes : défaut → `ncbi_unavailable`,
  déjà testé dans `test_projects_api` — rappel, rien à ajouter).

## 4. Recherche par identifiant natif
Fichiers : `app/api/v1/projects.py` (`search_blob`, docstrings),
`alembic/versions/0032_search_native_id.py`.
Tests (`tests/test_search_native_id.py`) :
- `test_search_blob_includes_native_id` (article `source='europepmc'`, `native_id='W123'`,
  sans `pmid`/`doi` → trouvé par `q='W123'` via `text_conditions`) ;
- `test_search_pmid_still_works` (gardes : un PMID reste trouvable) ;
- `test_migration_0032_recreates_trgm_index_on_pg` (sur SQLite : no-op vérifiable —
  `upgrade`/`downgrade` sans erreur, `get_heads() == ["0032"]` ; l'expression recreate est
  strictement l'expression 0028 + `coalesce(native_id, '')` après `pmid` — assertion sur
  le SQL de la migration).

## 5. Exports
Fichiers : `app/references.py` (RIS `AN`), `app/crate.py` (`article_key`, alias
`imports`, README), `app/prisma.py` (libellé `identified`).
Tests (`tests/test_source_exports3.py` ou ajouts aux fichiers existants — au choix de
l'implémenteur, 1 fichier) :
- `test_ris_an_for_native_id` (article sans `pmid`, `source='europepmc'`,
  `native_id='W123'` → ligne `AN - Europe PMC W123` ; article `pmid` → `AN - PMID:…`
  inchangé ; article sans rien → pas de ligne `AN`) ;
- `test_crate_article_key_native_id` (article `W…` sans pmid/doi → clé
  `europepmc-W…` ; avec doi → `doi-…` inchangé ; pmid → `pmid-…` inchangé ;
  `source=None` + `native_id` → `art-…`) ;
- `test_crate_exports_imports_alias` (`project.json` : clés `pubmed_queries` ET
  `imports`, mêmes valeurs ; `metadata.json` idem) ;
- `test_prisma_identified_label_lists_sources` (jobs pubmed + europepmc + references →
  « (PubMed, Europe PMC, listes importées) » (fr) ; en/de/es analogues ; pubmed seul +
  refs → libellé actuel inchangé ; sans refs → sans « listes importées »).

## 6. Interface (frontend, minimum)
- `layout.tsx` : description (D6) ;
- `messages/{en,fr,de,es}.json` : `articles.searchPlaceholder` (D6).
Test : `frontend/src/lib/__tests__/source.test.ts` (ajout : les 4 `searchPlaceholder`
contiennent « identifier/identifiant/Identifikator/identificador » et ne contiennent plus
« PMID » seul comme identifiant).

## Modifications de tests EXISTANTS (faites)
- `tests/test_source_dedup.py::test_doi_duplicate_other_identity_skipped_counted_no_fulltext`
  → renommé `test_doi_duplicate_other_identity_linked_fills_and_fulltext` : l'ancien saut D5
  est remplacé par le rattachement (champs vides remplis, plein texte demandé pour la ligne,
  identité/origine intactes) ;
- `tests/test_source_exports.py::test_source_label_and_name` : `europepmc` devient source
  connue (« Europe PMC » / « Europe PMC W1 ») ; le cas « inconnu : code brut » passe sur
  `openalex` ;
- `tests/test_source_identity.py` : `get_heads() == ["0031"]` → `["0032"]` ;
- `tests/test_projects_api.py` : inchangé (D4 passe par `_make_http_client` — vert au vert) ;
- Les tests PRISMA statiques (`test_team_export.py`) : inchangés (D6 — vert au vert) ;
- `frontend/src/lib/__tests__/source.test.ts` : « source inconnue » → `openalex`/`hal` ;
  ajout Europe PMC (« Europe PMC W123 ») + `searchPlaceholder` (4 langues, sans « PMID »).

## Multilingue (contrainte de l'utilisateur, 2026-10-29)
- Libellés PRISMA `identified_base`/`identified_lists` : 4 langues (fr/en/de/es) ;
- `articles.searchPlaceholder` : 4 langues (test jest par langue) ;
- « Europe PMC » = nom propre identique dans les 4 langues (backend `SOURCE_NAMES` +
  frontend `lib/source.ts` NAMES) ;
- Les erreurs source (`ncbi_unavailable` / `europepmc_unavailable`) sont affichées via un
  message générique i18n (aucune chaîne durcie côté frontend).

## Hors périmètre (jalons 4a/4b, specs propres)
- Sélecteur de source en UI (radios, défaut PubMed, volume par source), assistant de
  requête masqué hors PubMed, libellés d'import « PubMed » en dur (`import.*`,
  `project.howBody`) ;
- Adapters HAL (extracteur multi-format TEI/HTML/PDF) et OpenAlex (métadonnées seulement) ;
- Renommage `pubmed_queries` → `imports` (l'alias existe, le renommage propre est 4a) ;
- Rattachement DOI sur le chemin de références (`_do_import_refs`) ;
- `complete_fulltext_task` : cherche déjà le plein texte des articles existants
  (PubMed/EPMC par PMID/DOI) — inchangé dans cette phase.

## Faisabilité vérifiée (implémentation, 2026-10-29)
- API live : `hitCount`, `nextCursorMark`, `FIRST_PDATE:[… TO …]`, JATS `getFullTextXML`
  confirmés sur de vraies réponses (section Contexte) ; le 404/403 de `fullTextXML` est géré
  (`None` → « non disponible ») — vérifié sur la doc + le comportement de l'API.
- Tous les tests (adapter, pipeline, rattachement, recherche, exports, API) : 100 % offline
  (httpx.MockTransport / fake adapter, `_sleep` mocké) — aucune dépendance au rate limit
  live (IP datacenter : 503 après ~5 requêtes rapides, d'où le throttle 1 req/s).
- Les deux index SQL de la migration 0032 (upgrade/downgrade) vérifiés : expressions
  équilibrées, identiques à `search_blob()` (upgrade) et à l'expression 0028 (downgrade).

## Critères d'acceptation
- [x] Tests rouges listés verts ; tests existants verts (après les modifications listées)
- [x] Import `europepmc` E2E (mock) : volume, itération paginée, métadonnées, sections JATS
      dans `Article.sections`, `source='europepmc'`, `pmid` quand dispo
- [x] DOI inter-sources : la ligne existante est complétée (champs vides + plein texte si
      absent), identité intacte, compté `duplicates_doi`, pas de doublon
- [x] `query_volume` parle à la source demandée ; contrat `ncbi_unavailable` conservé
- [x] Recherche : un `W…` (native_id) est trouvable ; index trigramme recréé (0032, PG)
- [x] Exports : RIS `AN`, RO-Crate (`article_key`, alias `imports`), PRISMA, layout,
      placeholder — rien ne ment pour un article non-PubMed
- [x] `ruff check .`, `ruff format --check .`, `tsc --noEmit`, `npm run lint`, `jest` verts

## Fichiers à modifier (implémentation)
`backend/app/europepmc/__init__.py` (nouveau), `backend/app/sources/__init__.py`,
`backend/app/tasks/__init__.py`, `backend/app/api/v1/projects.py`,
`backend/app/references.py`, `backend/app/crate.py`, `backend/app/prisma.py`,
`backend/alembic/versions/0032_search_native_id.py` (nouveau) ;
`frontend/src/app/layout.tsx`, `frontend/src/messages/{en,fr,de,es}.json`.
Tests (passe 1) : `backend/tests/test_europepmc_adapter.py`,
`backend/tests/test_europepmc_pipeline.py`, `backend/tests/test_source_link_doi.py`,
ajouts `backend/tests/test_source_api.py` / `test_source_identity.py`,
`backend/tests/test_search_native_id.py`, `backend/tests/test_source_exports3.py`,
`frontend/src/lib/__tests__/source.test.ts` (ajout).
