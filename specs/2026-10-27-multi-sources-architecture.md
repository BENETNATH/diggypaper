# Multi-sources (concurrence avec PubMed) — architecture

Date : 2026-10-27 — **spec d'architecture seule** (aucune implémentation dans
cette passe ; jalons découpés pour implémentation TDD ultérieure).

## Contexte

L'import de corpus est aujourd'hui mono-source PubMed (BACKLOG.md:50–68).
Objectif : un pipeline d'import générique — interface `SourceAdapter`, PubMed en
adapter n°1, identifiant d'article généralisé `(project_id, source, native_id)`
avec dédoublonnage par DOI, choix de la source par import — et ajout de sources
concurrentes : Europe PMC (adapter évident, plein texte JATS déjà parsable),
HAL (complément thématique français, extracteur de plein texte à prévoir),
OpenAlex (métadonnées seulement). Candidates vérifiées le 2026-10-27 :
BACKLOG.md:59–68.

## État actuel

- **Structure de l'adapter PubMed** (backend/app/pubmed/__init__.py, « client
  NCBI E-utilities — Module 2 ») :
  - volume : `search_volume(query, api_key, http)` → int, esearch `retmax=0`
    (l.138–144) ;
  - identifiants : `fetch_pmids(query, api_key, http, retmax)` (l.174–182, une
    fenêtre NCBI, `PUBMED_WINDOW = 9999` l.61–63) et `fetch_all_pmids(query,
    api_key, http, count, limit)` (l.185–233) : bissection par dates de
    publication au-delà de la fenêtre, « les plus récents d'abord » (l.199–203,
    225–226), dedoublonnage, cap `limit` ;
  - métadonnées : `fetch_metadata(pmids, api_key, http)` → liste de dicts
    `{pmid, pmcid?, doi?, title, abstract, language, journal, pub_date,
    authors, pub_types}` (l.236–248, parsing XML l.348–377) ;
  - plein texte : `fetch_fulltext(pmcid, http)` → `(texte Methods, sections
    [{title, kind, text}])` via efetch `db=pmc` en JATS (l.262–269, appel
    l.272–280) ; parsing JATS existant : `_extract_methods` (l.380–397),
    `_extract_sections` (l.419–435), `_plain` (l.404–416, formules) ;
    `fetch_methods` (l.251–259) ;
  - infrastructure : `httpx.AsyncClient` injecté (doc l.3–4), throttle
    **par process** `asyncio.sleep` (l.45–47, 95–97 ; « pas de limiteur Redis »,
    l.6–8, et contrainte de déploiement M-3, l.9–13), retry 503/timeout
    `_get_with_retry` (l.100–122), erreur assainie `NCBIError` sans URL/clé
    (l.80–86, doc l.15–18), limite de taille XML 10 Mo (l.70–71, 125–128).
- **Pipeline d'import** (BACKLOG.md:51–52) : `search_volume → fetch_all_pmids →
  fetch_metadata → fetch_fulltext`, exécuté par `import_pubmed_task`
  (backend/app/api/v1/projects.py:50, appelé l.474).
- **Choix de la source** : inexistant. `ImportRequest` porte uniquement
  `query` (backend/app/api/v1/projects.py:112–114) ; `trigger_import` (l.453–
  477) : garde projet `PROCESSING` → 409 (l.471–472), `_require_no_active_job`
  (l.473), puis `import_pubmed_task.delay(str(project.id), payload.query)`
  (l.474). L'import de références (`import_references`, l.492–521) réutilise le
  même task avec une requête synthétique `references (…)`. `query_volume`
  (l.557–560) utilise aussi `ImportRequest`.
- **Identity** : `Article.pmid` (backend/app/db/models.py:278) + index unique
  partiel `uq_articles_project_pmid` sur `(project_id, pmid)` où `pmid IS NOT
  NULL` (l.264–271, commentaire « M2 : dédoublonnage au re-import »). `Article
  .doi` existe (l.280) sans contrainte. `ImportJob` (l.732–758) persiste
  `query` (l.745), `kind` (l.746), `status`, `result_json` — **pas de source**.
- **Plein texte** : `Article.fulltext_source` (l.289) accepte déjà
  `pmc | europepmc | pdf[:version]` ; `fulltext_note` documente l'absence de
  texte (l.287–288). Le parsing JATS (ci-dessus) est réutilisable tel quel.

## Spécification

### 1. Interface `SourceAdapter`

Contrat (noms et sémantique — signatures exactes choisies à l'implémentation) :

- `search_volume(query, bornes)` → int : nombre de résultats pour une requête,
  `bornes` = intervalle de dates optionnel (cf. évolution « bornes date »,
  BACKLOG.md:39–43) ;
- itération paginée des résultats : `iter_ids(query, bornes, limit)` —
  itération des `native_id` par pages (taille de page interne à l'adapter,
  bornée par le plafond de l'API source), « les plus récents d'abord », cap
  `limit` ; pour PubMed, elle absorbe la sémantique actuelle de
  `fetch_all_pmids` (bissection, fenêtre `PUBMED_WINDOW`, l.185–233)
  sans changer le comportement observable ;
- `fetch_metadata(ids)` → liste d'articles natifs : au moins
  `native_id`, `title`, `abstract`, `doi?`, `pub_date?`, `authors?` + les
  identifiants secondaires dont la source a besoin pour le plein texte
  (PubMed : `pmcid` — le plein texte courant passe par le PMCID, pas le PMID,
  backend/app/pubmed/__init__.py:262–280) ;
- `fetch_fulltext(id)` → `(texte, sections [{title, kind, text}])` ou
  « non disponible » (→ `fulltext_note`) ; une source sans plein texte
  (OpenAlex) implémente la méthode en renvoyant systématiquement
  « non disponible ».

Règles transverses (héritées de l'adapter PubMed, imposées à tous les
adapters) : `httpx.AsyncClient` injectable (mock en tests) ; erreur assainie
par source (pas d'URL ni de clé dans le message, pattern `NCBIError`,
l.80–86) ; throttle **par process et par source** (pas de limiteur Redis,
conformément au choix documenté, l.6–8) ; retry backoff sur erreur
transitoire (pattern `_get_with_retry`, l.100–122) ; limite de taille des
payloads entrants.

### 2. Adapter n°1 : PubMed (refactor, comportement inchangé)

- Le module `backend/app/pubmed` devient l'adapter n°1 de l'interface ci-dessus
  : les fonctions existantes (`search_volume` l.138, `fetch_all_pmids` l.185,
  `fetch_metadata` l.236, `fetch_fulltext` l.262) sont regroupées derrière le
  contrat ; la logique de bissection/throttle/retry/parsing est **déplacée,
  pas réécrite** ;
- les tests existants de `app/pubmed` (throttle mocké `_sleep`, http injecté,
  `test_chunking_over_100_pmids` cité par BACKLOG.md:18) restent verts ;
- le pipeline d'import pubmed observable est invariant (mêmes appels NCBI,
  même ordre, mêmes stats) — le refactor est invisible du comportement.

### 3. Identity des articles

- `Article` identifié par **`(project_id, source, native_id)`** : nouvelles
  colonnes `source` (string, non nulle) et `native_id` (string) ; unique
  partiel sur le triplet où `native_id IS NOT NULL` (remplace l'unique
  partiel actuel `uq_articles_project_pmid`, l.264–271, qui en est le cas
  particulier `source='pubmed'`) ;
- dédoublonnage par **DOI** quand présent : à l'insertion d'un article dont le
  `doi` est connu, si un article du même projet porte le même DOI (quelle que
  soit sa source), le re-import est rattaché à la ligne existante
  (compléter les champs manquants : plein texte, dates) au lieu de créer un
  doublon — la contrainte de base reste le triplet, le DOI est le
  rattachement applicatif ;
- migration des données existantes : `source = 'pubmed'`,
  `native_id = pmid` pour toutes les lignes ; la colonne `pmid` est
  conservée (rédépendue par l'import pubmed, lisible par l'existant) et
  l'unique ancien est retiré une fois l'unique triplet en place ; les
  articles sans `pmid` (import de références, `native_id` NULL) restent hors
  portée des deux contraintes, comme aujourd'hui.

### 4. Choix de la source par import

- `ImportRequest` gagne un champ `source` (défaut `'pubmed'`, valeurs validées
  contre les adapters enregistrés) ; `query_volume` (projects.py:557–560) et
  l'UI d'import le portent également ;
- `ImportJob` stocke `source` (nouvelle colonne, défaut `'pubmed'` pour les
  jobs existants) ; chaque `Article` inséré porte sa `source` ;
- la garde « un import par projet » (409 `PROCESSING` /
  `_require_no_active_job`, projects.py:471–473) reste globale à tous les
  sources (un seul import à la fois par projet) ;
- l'import de références (projects.py:492–521) reste un chemin distinct,
  non concerné par le choix de source dans cette architecture (ses articles
  n'ont pas de `native_id` source).

### 5. Plein texte par source

- **Europe PMC** (adapter n°2) : `getFullTextXML` en JATS OA — le format que
  l'app parse déjà (`_extract_sections`/`_extract_methods`,
  backend/app/pubmed/__init__.py:380–435) : aucun nouveau parseur ;
  `fulltext_source` écrit `'europepmc'` (valeur déjà prévue, models.py:289) ;
  REST gratuite sans clé, rate limit par IP (BACKLOG.md:60–62) ;
- **HAL** (adapter n°3) : recherche Solr JSON (`api.archives-ouvertes.fr/
  search/`, `rows` ≤ 10 000, pagination par curseur, `fq=submittedDate_tdate`,
  BACKLOG.md:63–65) ; plein texte TEI/HTML/PDF → **extracteur à prévoir**
  (hors périmètre de la phase Europe PMC ; scoping propre en phase 4) ;
- **OpenAlex** : `api.openalex.org/works` (filtres
  `from_publication_date`/`to_publication_date`, OA status, BACKLOG.md:66–68) —
  **métadonnées seulement** : `fetch_fulltext` systématiquement « non
  disponible » (`fulltext_note` posé) ; pas de scraping de PDF OA dans cette
  architecture.

## Jalons

Chaque jalon est une passe d'implémentation TDD indépendante (la spec TDD
détaillée sera écrite à l'ouverture de la phase ; pas de code dans ce
document).

### Phase 1 — Interface `SourceAdapter` + adapter PubMed n°1

Périmètre : définition du contrat (§ 1) ; refactor de `backend/app/pubmed`
en adapter (§ 2) ; le pipeline d'import pubmed passe par l'adapter (même
flux, mêmes stats). Aucune nouvelle source, aucun changement de schéma, aucun
changement API visible.

Critères de fin de phase : tests existants de `app/pubmed` verts sans
modification de sémantique (y compris
`test_chunking_over_100_pmids`, ordre NCBI préservé) ; l'import pubmed est
observable-identique (mêmes appels, même dédoublonnage) ; le contrat
d'adapter est testable seul (fake adapter pour le pipeline).

### Phase 2 — Identity `(project_id, source, native_id)` + migration

Périmètre : colonnes `source`/`native_id` sur `Article` + unique partiel
triplet (§ 3) ; retrait de `uq_articles_project_pmid` ; migration
`source='pubmed'`, `native_id=pmid` ; colonne `source` sur `ImportJob` ;
champ `source` (défaut `'pubmed'`) sur `ImportRequest` ; rattachement par DOI
à l'insertion. L'API reste compatible (défauts), l'UI inchangée.

Critères de fin de phase : migration idempotente vérifiée sur base de prod et
sur base de test ; re-import pubmed = mêmes lignes qu'avant (triplet, pas de
doublon) ; article DOI-doublon entre deux imports rattaché à une seule ligne ;
`query_volume` et `trigger_import` acceptent `source='pubmed'` sans effet de
bords.

### Phase 3 — Adapter Europe PMC (recherche + métadonnées + plein texte JATS)

Périmètre : adapter `europepmc` (§ 5, volume / itération paginée /
métadonnées / `getFullTextXML` JATS réutilisant le parseur existant) ;
sélection de la source côté API (payload + job + articles) ;
`fulltext_source='europepmc'`. Pas d'UI de choix dans cette phase (source
fixée dans le payload) ; HAL et OpenAlex hors périmètre.

Critères de fin de phase : import E2E Europe PMC (volume, import borné,
métadonnées, sections JATS dans `Article.sections`) ; dédoublonnage
triplet + DOI opérationnel entre un import pubmed et un europepmc du même
projet ; throttle Europe PMC par process vérifié en test.

### Phase 4 — Choix de la source en UI + adapters HAL et OpenAlex

Périmètre : UI d'import avec sélection de source (radios, défaut PubMed,
volume affiché par source) ; adapter HAL (recherche Solr + extracteur de plein
texte TEI/HTML/PDF à spécifier à l'ouverture) ; adapter OpenAlex (métadonnées
seulement, `fetch_fulltext` → « non disponible » + `fulltext_note`).

Critères de fin de phase : les 4 sources sélectionnables en UI ; un import
HAL produit des articles avec `source='hal'` et plein texte ou
`fulltext_note` ; un import OpenAlex produit des métadonnées avec
`fulltext_note` explicite ; la garde « un import par projet » inchangée pour
toutes les sources.

## Critères d'acceptation

- [ ] Contrat `SourceAdapter` (volume / itération paginée / `fetch_metadata`
      / `fetch_fulltext`) défini avec les règles transverses (http injectable,
      erreur assainie, throttle par process par source, retry, limite de
      taille)
- [ ] PubMed = adapter n°1 par refactor de `app/pubmed` sans changement de
      comportement ; tests existants préservés
- [ ] Identity `(project_id, source, native_id)` + dédoublonnage applicatif
      par DOI ; migration `source='pubmed'`, `native_id=pmid` spécifiée
      (colonne `pmid` conservée, unique ancien retiré)
- [ ] `source` sur `ImportRequest`, sur `ImportJob` et sur `Article` ;
      garde d'import globale par projet conservée
- [ ] Plein texte : Europe PMC = JATS (parseur existant, aucun nouveau
      parseur), HAL = extracteur à prévoir (phases 4), OpenAlex = métadonnées
      seulement
- [ ] 4 jalons, chacun avec périmètre + critères de fin, aucune dépendance
      inversée (1 → 2 → 3 → 4)
- [ ] Aucun code dans cette spec ; les specs TDD seront écrites à l'ouverture
      de chaque phase

## Contraintes

- **Architectural only** : cette passe ne modifie ni code, ni schéma, ni
  migration. Toute phase d'implémentation devra respecter : le plus petit
  périmètre de sa spec (ponytail) ; pas de limiteur Redis partagé (throttle
  par process, choix documenté, backend/app/pubmed/__init__.py:6–8) ; pas de
  nouveau parseur JATS (réutilisation de `_extract_sections`/
  `_extract_methods`) ; pas de scraping OpenAlex (métadonnées API uniquement,
  pricing usage-based depuis fév. 2026, BACKLOG.md:67–68) ; la colonne
  `pmid` n'est pas renommée ni supprimée (migration additive, `native_id`
  parallèle).
- Ordre des jalons imposé : l'interface (1) précède l'identity (2) qui
  précède les nouveaux adapters (3, 4) — un adapter sans le triplet
  `(project_id, source, native_id)` créerait des doublons de
  dédoublonnage.
- HAL est le jalon le plus coûteux (extracteur multi-format TEI/HTML/PDF) :
  il est volontairement isolé dans la phase 4 avec scoping propre, sans
  bloquer Europe PMC (3).
