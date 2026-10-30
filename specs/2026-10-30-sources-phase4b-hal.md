# Phase 4b — Adapter HAL (recherche Solr + plein texte PDF)

Date : 2026-10-30 — dernier jalon de `2026-10-27-multi-sources-architecture.md`
(phase 4 : « adapter HAL (recherche Solr + extracteur de plein texte TEI/HTML/PDF
à spécifier à l'ouverture) »). Cette spec est l'ouverture.

## Contexte (vérifié live, 2026-10-30)

API Solr JSON gratuite sans clé, `https://api.archives-ouvertes.fr/search/` :

- volume : `q=…&wt=json&rows=0` → `response.numFound` (89803 pour `cancer`) ;
- bornes date : `fq=producedDate_tdate:[2024-01-01T00:00:00Z TO *]` → 15956
  (filtre date de production/écriture, l'analogue HAL de `[dp]` PubMed) ;
- pagination profonde : `cursorMark=*` + `sort=producedDate_tdate desc,docid desc`
  → `nextCursorMark` (Solr exige la clé unique `docid` en tie-break du tri) ;
  `rows=1000` accepté (BACKLOG : « rows ≤ 10 000 », 1000 vérifié) ;
- lecture par identifiants : `q=halId_s:("hal-00478278" OR "hal-04167683")` ;
- métadonnées (multivaluées, `_s` = listes) : `halId_s`, `title_s`, `abstract_s`,
  `authFullName_s`, `language_s` (codes ISO), `doiId_s`, `journalTitle_s`,
  `producedDate_tdate` (ISO), `publicationDateY_i` (année seule) ;
- plein texte : `fileMain_s` = URL du fichier principal
  (`https://hal.science/hal-00478278/document` → `application/pdf`, 327 Ko,
  vérifié). Les fichiers non-PDF (TEI, HTML) existent mais sont rares.

## Décisions

### D1 — Adapter HAL (`backend/app/hal/__init__.py`, source n°4)

Mêmes règles transverses que `app.europepmc`/`app.openalex` (archi § 1) :
`httpx.AsyncClient` injecté, `_sleep` mocké en tests, throttle **par process**
1 appel/s, retry backoff sur 503/timeout, erreur `HalError` propre SANS l'URL,
`api_key` capté mais ignoré (API sans clé).

- `search_volume(query, bornes)` → `numFound` (`rows=0`), caché et réutilisé par
  `iter_ids` ;
- `iter_ids(query, bornes, limit)` : curseur (`cursorMark=*` → `nextCursorMark`),
  `sort=producedDate_tdate desc,docid desc` (les plus récents d'abord, tri stable
  par clé unique), page 1000, cap `limit` (défaut `_esearch_max()`), dédoublonnage
  `halId_s` ; bornes → `fq` **toujours ANDé** (même sémantique que
  `app.pubmed._term_bounded`) ;
- `fetch_metadata(ids)` : tranches de 100, `q=halId_s:("…" OR …)`,
  `rows=len(tranche)` ;
- `fetch_fulltext(id)` : `id` = `fulltext_id` de la notice (URL `fileMain_s`,
  cf. D2).

`native_id` = `halId_s` (« hal-00478278 ») : stable, lisible dans les exports
(« HAL hal-00478278 »), interrogé directement par la lecture par identifiants.

### D2 — Plein texte : PDF via l'extracteur EXISTANT, aucun nouveau parseur

L'archi prévoyait « extracteur multi-format TEI/HTML/PDF » (le jalon le plus
coûteux). Réalité vérifiée : le fichier principal HAL est écrasamment du PDF, et
l'app a déjà `app.fulltext.pdf_to_sections` (PDF → sections IMRaD) et
`app.fulltext._download_pdf` (SSRF : chaque saut de redirection doit rester
public, plafond 25 Mo, magie `%PDF`). Réutilisation tel quel :

- PDF téléchargé + sectionné → `(methods=None, sections)` ; sections vides →
  « non disponible » ;
- fichier non-PDF (TEI/HTML) ou téléchargement refusé → `None` (résumé seul,
  comme un article sans copie ouverte) ;
- **sans** `fileMain_s` → `fulltext_note="hal_no_file"` posé par la notice
  (l'UI affiche le repli « résumé seul » existant, aucun message dédié).

shortcut: pas d'extracteur TEI/HTML (fichiers rares) ; à écrire si un corpus
en dépend vraiment.

### D3 — Pipeline / registre (aucune migration)

- `ADAPTERS["hal"] = HalAdapter` (`app.tasks`) ; `_SOURCE_KEY_SETTING` : HAL
  n'a pas de clé → le fallback actuel (`ncbi_api_key`, lu et ignoré) est le
  comportement déjà en place pour Europe PMC ;
- `Article.source='hal'` + `native_id` (triplet, dédoublonnage DOI inter-sources
  = phase 2, rien à refaire) ; `fulltext_source='hal'` (colonne VARCHAR libre) ;
- `fulltext_id` = URL `fileMain_s` → le chemin `fulltext_id` du pipeline
  (`_fulltext_for`, phase 3) appelle `adapter.fetch_fulltext(url)` et pose
  `fulltext_source`/`fulltext_url` de la notice.

### D4 — UI (page import)

Le sélecteur de la 4a est déjà générique : union `"pubmed" | "europepmc" |
"openalex"` + tableau des codes + `sourceLabel()` → ajouter `"hal"` aux trois.
`app.sources.SOURCE_NAMES` + `frontend/src/lib/source.ts` : « HAL ». Aucune
clé i18n nouvelle : `queryHintGeneric` (« texte libre, opérateurs AND/OR »)
couvre la syntaxe Solr, le repli « résumé seul » couvre `hal_no_file`, HAL ne
prend pas de clé (pas de bloc réglages).

## Tests ROUGES (écrits avant l'implémentation, 100 % offline)

`backend/tests/test_hal_adapter.py` (httpx.MockTransport, `_sleep` mocké) :

- volume : `q`, `wt=json`, `rows=0`, `sort` posé, pas de `fq` sans bornes ;
- bornes → `fq` `producedDate_tdate:[… TO …]` ;
- `iter_ids` : `cursorMark=*` puis `nextCursorMark`, cap `limit` respecté, tri
  « les plus récents d'abord » porté par `sort` ;
- `fetch_metadata` : requête `halId_s:("…" OR …)`, `rows=len(tranche)`,
  parsing — première valeur des listes (titre, résumé), `doiId_s`, année de
  secours (`publicationDateY_i`) quand `producedDate_tdate` absent,
  `fulltext_id`/`fulltext_url` quand `fileMain_s`, `fulltext_note="hal_no_file"`
  sinon ;
- `fetch_fulltext` : PDF → `(None, sections)` ; non-PDF/échec → `None` ;
  sections vides → `None` (pas de `fulltext_source` sans texte) ;
- erreurs : HTTP 400 → `HalError("HTTP 400")` sans URL ; 503 → retry puis
  réussit ; timeout → `HalError("timeout")`.

`backend/tests/test_hal_pipeline.py` (fake adapter, pattern 4a) :

- import `source='hal'` : articles `source='hal'` + `native_id`, plein texte
  posé (`sections`, `fulltext_source='hal'`, `fulltext_url`), note explicite pour
  la notice sans fichier, stats `{imported, failed, fulltext}` ;
- ré-import même identité : 0 inséré (ON CONFLICT) ;
- `ADAPTERS["hal"] is HalAdapter`.

## Hors périmètre

- extracteur TEI/HTML (shortcut D2) ;
- clé API HAL (n'existe pas), réglages associés ;
- import HAL dans le crate / exports : rien à faire (la `source` est déjà
  exportée génériquement depuis la phase 2, `source_label` inclut HAL
  automatiquement).

## Critères d'acceptation

- [ ] Les tests rouges listés passent ; aucun test existant cassé (suite
      complète verte, lancée depuis `backend/`)
- [ ] Volume + import borné + métadonnées + sections PDF vérifiés E2E sur la
      vraie API (recherche manuelle, article avec `fileMain_s`)
- [ ] « HAL » sélectionnable en UI ; `fulltext_note` explicite sans fichier
- [ ] `ruff check .`, `ruff format --check .`, `tsc --noEmit`, `npm run lint`,
      `jest` verts
