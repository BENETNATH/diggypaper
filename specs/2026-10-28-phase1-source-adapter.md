# Phase 1 — contrat `SourceAdapter` + adapter PubMed n°1

Date : 2026-10-28
Spécification TDD — **Phase 1 uniquement** (de la spec d'architecture
`specs/2026-10-27-multi-sources-architecture.md`, § 1 + § 2 + jalon « Phase 1 »).

Garde-fous : on écrit **uniquement** ce fichier dans `specs/`. Aucun code,
aucune DB, aucun endpoint. C'est une spec, pas du code.

## Contexte

Aujourd'hui, importer = toujours PubMed. Le pipeline d'import
(`backend/app/tasks/__init__.py`) appelle directement les fonctions du module
`backend/app/pubmed/__init__.py` (`search_volume`, `fetch_all_pmids`,
`fetch_metadata`, `fetch_fulltext`). La spec d'architecture (2026-10-27)
introduit un contrat `SourceAdapter` commun pour découpler « la logique
pipeline » de « le fait de parler à une source », et positionne **l'adapter
PubMed comme la n°1**, refactor du module existant **sans changer le
comportement observable** (ponytail : on déplace, on ne réécrit pas).

Phase 1 ne doit rien changer à l'observable : mêmes appels NCBI, même ordre
des PMIDs, mêmes stats d'import, même schéma DB, mêmes endpoints. Le contrat
est **testable seul** (aucune NCBI, aucun DB, aucune session) grâce à un
**fake adapter**.

Périmètre de la phase (issu du § 1 + § 2 + jalon Phase 1 de l'architecture) :

1. Placer le contrat `SourceAdapter` (module dédié) + ses 4 opérations à
   signatures exactes + les règles transverses.
2. Refactorer `backend/app/pubmed` derrière le contrat (adapter PubMed n°1),
   en absorbant `fetch_all_pmids` dans `iter_ids` **sans changer le
   comportement observable**.
3. Faire passer le pipeline `import_pubmed_task` par l'adapter (même flux,
   mêmes stats).
4. Fournir un **fake adapter** de test pour que le contrat soit testable seul.

## État actuel (références fichier : ligne)

### Module PubMed — `backend/app/pubmed/__init__.py` (452 lignes)

- Doc module : httpx injecté (l.3–4), throttle par process sans Redis
  (l.6–8 ; note M-3 deploy l.9–13), C-1 `NCBIError` sans URL/clé (l.15–18),
  throttle `oa.fcgi` → efetch `db=pmc` (l.20–21, l.272–273), retry
  503/timeout (l.23–25), limite taille payload 10 Mo (l.27–29).
- Constantes : `EUTILS_BASE` l.42 ; `PMC_OA_URL` l.43 ; `_INTERVAL_NO_KEY`
  l.46 ; `_INTERVAL_WITH_KEY` l.47 ; `UNLIMITED = 10**9` l.51.
- `_esearch_max()` l.54–58 (lit `get_settings().import_max_articles` ; `0`
  → `UNLIMITED`).
- `PUBMED_WINDOW = 9999` l.63 (fenêtre esearch NCBI).
- `_RETRIES = 2` l.67 ; `_RETRY_BACKOFF_SECONDS = 1.0` l.68 ;
  `_MAX_XML_BYTES = 10 MiB` l.71 ; `_sleep = asyncio.sleep` l.74 (point de
  mock du throttle).
- `NCBIError` l.80–86 ; `_raise_for_status` l.89–92 ; `_throttle` l.95–97 ;
  `_get_with_retry` l.100–122 (retry sur 503/timeout, l.110–120) ;
  `_check_xml_size` l.125–128.
- **`_term_bounded(query, date_from, date_to)`** l.131–138 : construit le
  terme borné `(query) AND ("YYYY/MM/DD"[dp] : "YYYY/MM/DD"[dp])`. C'est
  **aujourd'hui** le porteur des bornes date — le pipeline l'appelle
  lui-même avant de passer la query à `search_volume` (l.299 de
  `tasks/__init__.py`).
- `_esearch_params` l.141–145.
- **`search_volume(query, api_key, http)`** l.148–154 → `int`. **Aucun
  paramètre de bornes date** : le terme est déjà borné par l'appelant
  (`_term_bounded`).
- `explain_query` l.157–181 (non consommé par le pipeline d'import).
- `fetch_pmids(query, api_key, http, retmax)` l.184–192 (une fenêtre
  `retmax=min(retmax or _esearch_max(), PUBMED_WINDOW)`).
- **`fetch_all_pmids(query, api_key, http, count, limit=None,
  date_from=None, date_to=None)`** l.195–250 : `count` est **requis en
  position** (l.199) ; `limit` optionnel (défaut `_esearch_max()`, l.220) ;
  `date_from`/`date_to` optionnels (l.201–202). Le terme est borné
  **intérieurement** via `_term_bounded` (l.221). Cas court-circuit
  `count <= PUBMED_WINDOW` → un seul esearch (l.222–224) ; sinon bissection
  par `PUBMED_WINDOW` (l.227–242), les **plus récents d'abord** (l.241),
  dédoublonnage par `found: dict[str, None]` (l.225), renversement dans les
  feuilles (l.237–238), bornes utilisateur (l.244–245), cap `limit` (l.250).
- **`fetch_metadata(pmids, api_key, http)`** l.253–265 : `[]` si liste vide
  (l.257–258) ; efetch XML par id ; `_check_xml_size` (l.264). Parse par
  article → dict `{pmid, pmcid?, doi?, title, abstract, language, journal,
  pub_date, authors, pub_types}` (via `_parse_pubmed_xml`, cf.
  `test_fetch_metadata_parses_xml` l.269 de `test_pubmed.py`). L'identifiant
  secondaire plein texte est **`pmcid`**.
- `fetch_methods(pmcid, http)` l.268–276 (hors contrat).
- **`fetch_fulltext(pmcid, http)`** l.279–286 →
  **`tuple[str | None, list[dict]]`** (toujours un tuple) ; `_fetch_pmc_xml`
  l.289–297 (throttle `None` = `oa.fcgi` intervalle, l.292 ; efetch
  `db=pmc`, l.293–295). Si pas de XML : `(None, [])` (l.284–285).
  Parseurs JATS : `_parse_pubmed_xml`, `_extract_methods`, `_plain`,
  `_extract_sections` (cf. `test_sections_drop_latex_and_mark_display_formulas`
  l.396).

### Pipeline — `backend/app/tasks/__init__.py` (818 lignes)

- Imports pubmed l.56–64 (`NCBIError`, `_esearch_max`, `_term_bounded`,
  `fetch_all_pmids`, `fetch_fulltext`, `fetch_metadata`, `search_volume`) ;
  imports fulltext l.55 (`complete_article`, `long_enough`,
  `pmc_not_archived`). `_EFETCH_BATCH = 100` l.90. `_session_factory_override`
  l.96. `_API_KEY_RE` l.101 ; `_sanitize_error` l.104–114 (C-1).
- `import_pubmed_task` (wrapper sync Celery) l.153–201 ; enchaîne
  `complete_fulltext_task` en cas de succès (l.199–200).
- **`_import_pubmed`** (core async) l.204–283 : ouvre session (l.228–229),
  client http (l.234–235), garde `PROCESSING` (l.248–249), crée `ImportJob`
  (l.254–266), dispatch `refs`/`citations`/`_do_import` (l.269–275),
  `_record_failure` (l.277).
- **`_do_import`** l.286–378 :
  - `api_key = await get_setting(session, "ncbi_api_key")` (l.296)
  - `count = await search_volume(_term_bounded(query, date_from, date_to),
    api_key, client)` (l.299)
  - `count == 0` → `_finish_success` (l.300–306)
  - `pmids = await fetch_all_pmids(query, api_key, client, count,
    date_from=date_from, date_to=date_to)` — **sans `limit` explicite** →
    défaut `_esearch_max()` (l.307–309)
  - `truncated = count > _esearch_max()` (l.310)
  - `job.result_json` progress (l.320–338)
  - `for batch in _chunks(pmids, _EFETCH_BATCH)` (l.340)
  - `metas = await fetch_metadata(batch, api_key, client)` (l.342)
  - `except` par batch → `failed += len(batch)` (l.343–348)
  - `by_pmid = {m["pmid"]: m for m in metas if m.get("pmid")}` (l.349)
  - `_insert_article(session, project.id, meta, client)` (l.356–357)
  - `_finish_success` (l.367–378)
- `_insert_article` l.596–641 :
  - `meta.get("pmcid")` (l.617)
  - `methods, sections = await fetch_fulltext(meta["pmcid"], client)`
    (l.619)
  - `long_enough(sections)` (l.622) → set `methods_text`/`sections`/
    `fulltext_source`
  - `pmc_not_archived(sections)` (l.626) → `fulltext_note =
    "preprint_not_archived"`
  - `ON CONFLICT (project_id, pmid) WHERE pmid IS NOT NULL` (l.632–641)
- `_finish_success` l.644–653 ; `_record_failure` l.656–678.
- Chemins **hors** périmètre Phase 1 (restent branchés sur `fetch_metadata`
  direct) : `_do_import_refs` l.381–498 (appel `fetch_metadata` l.448),
  `_do_import_citations` l.501–572, `backfill_metadata` l.684–709
  (l.698).

### API visible — `backend/app/api/v1/projects.py` (888 lignes)

- `ImportRequest` l.112–124 : `query` (l.114), `date_from` (l.117), `date_to`
  (l.118), `model_validator` (l.120–124). **Aucun champ `source`** (Phase 2).
- `trigger_import` l.465–491 : `import_pubmed_task.delay(str(project.id),
  payload.query, date_from=payload.date_from, date_to=payload.date_to)`
  (l.484–486) ; retour `{"job_id", "status"}` (l.491).
- `query_volume` l.569–589 : `search_volume(payload.query, api_key, client)`
  (l.584, **sans** `_term_bounded` — le volume est le count brut de la
  query) ; `NCBIError` → 502 `ncbi_unavailable` (l.585–588) ; retour
  `{"count", "max"}` (l.589).

### Modèle — `backend/app/db/models.py`

`Article` l.258+ : `pmid` l.278, `pmcid` l.279, `doi` l.280,
`fulltext_note` l.288, `fulltext_source` l.289 ; unique partiel
`uq_articles_project_pmid` l.264–271. **Aucune colonne `source`** (Phase 2).
`fulltext.py` : `complete_article` l.312, `pmc_not_archived` l.184,
`long_enough` l.191.

### Tests existants — `backend/tests/test_pubmed.py` (736 lignes)

Tous appellent les fonctions publiques du module (signatures publiques
invariantes). Le throttle est neutralisé par l'fixture autouse `_no_sleep`
(l.11–18, patche `app.pubmed._sleep`) ; le `httpx.AsyncClient` est
**injecté** (`_client()` l.21–22). Couvrages clés :
- `search_volume` : `test_search_volume_returns_count` l.28,
  `test_search_volume_sends_api_key` l.41.
- `fetch_pmids` : `test_fetch_pmids_default_retmax_is_configured_cap` l.54,
  `test_fetch_pmids_never_asks_more_than_the_ncbi_window` l.66,
  `test_fetch_pmids_sends_api_key` l.80.
- retry/backoff 503 & timeout : `test_esearch_retry_on_503_then_success`
  l.92, `test_esearch_retry_exhausted_raises` l.107,
  `test_timeout_raises_ncbi_error` l.128,
  `test_fetch_methods_503_raises_ncbi_error` l.143,
  `test_efetch_retry_on_timeout` l.156, `test_no_retry_on_parse_error` l.172.
- limite taille XML : `test_xml_size_limit_metadata` l.189,
  `test_xml_size_limit_methods` l.201.
- `fetch_metadata` : `test_fetch_metadata_sends_api_key` l.259,
  `test_fetch_metadata_parses_xml` l.269, `test_fetch_metadata_empty_list`
  l.295.
- `fetch_methods` / parse biblio : `test_fetch_methods_extracts_methods_section`
  l.329, `test_fetch_methods_none_without_pmcid` l.345,
  `test_fetch_methods_none_when_no_methods_section` l.353,
  `test_parse_biblio_metadata` l.373,
  `test_sections_drop_latex_and_mark_display_formulas` l.396.
- **`fetch_all_pmids`** :
  `test_fetch_all_pmids_splits_queries_over_the_ncbi_window` l.413,
  `test_fetch_all_pmids_bisection_uses_user_date_bounds` l.447,
  `test_fetch_all_pmids_only_date_from_keeps_default_hi` l.492,
  `test_fetch_all_pmids_only_date_to_keeps_default_lo` l.529,
  `test_fetch_all_pmids_user_dates_cap_keeps_most_recent` l.565,
  `test_fetch_all_pmids_no_dates_unchanged_bounds` l.597,
  `test_fetch_all_pmids_shortcut_with_dates_single_bounded_esearch` l.633,
  `test_fetch_all_pmids_shortcut_with_dates_no_dp_doubling_when_query_has_dp`
  l.658, `test_fetch_all_pmids_bisection_query_with_dp_keeps_leaf_subrange`
  l.686.
- `test_import_cap_zero_means_unlimited` l.729.

### Tests existants — `backend/tests/test_tasks_import.py` (712 lignes)

Pipeline d'import, NCBI mocké via handler. `_no_sleep` l.22–29.
`_ncbi_handler` l.77–93 (esearch + efetch). Tests `_import_pubmed` :
`test_import_pubmed_inserts_articles_and_freezes` l.135,
`test_import_pubmed_count_zero_frozen` l.163,
`test_import_pubmed_partial_error_continues` l.188,
`test_import_pubmed_unknown_project_raises` l.207,
`test_import_pubmed_total_error_raises` l.218,
`test_import_failure_error_sanitized_no_key_no_url` l.241,
`test_import_creates_import_job` l.268,
`test_import_processing_project_raises` l.299,
`test_import_completes_frozen_project` l.315, `test_reimport_no_duplicates`
l.330, `test_truncation_count_over_10000` l.355,
**`test_chunking_over_100_pmids`** l.386–409 (150 PMIDs → efetch par lots de
100 (100 + 50) — c'est un test **pipeline**, pas pubmed ; il reste inchangé
et vert car le chunking `_chunks(pmids, _EFETCH_BATCH)` reste dans le
pipeline),
`test_import_pubmed_count_term_bounded_by_dates` l.415,
`test_import_pubmed_without_dates_count_term_unchanged` l.444,
`test_import_pubmed_task_wrapper_passes_dates` l.464,
`test_sanitize_error_ncbi_error_passthrough` l.484,
`test_sanitize_error_filters_api_key` l.490,
`test_sanitize_error_truncates_500` l.501,
`test_sanitize_error_empty_falls_back_to_type` l.506,
`test_map_language_unknown_keeps_raw` l.514,
`test_import_unknown_language_stored_raw` l.525,
`test_import_creates_own_engine` l.558,
`test_import_real_path_eager_no_delay_mock` l.599,
`test_import_pubmed_task_wrapper_calls_core` l.641.

**Deux tests touchent la signature de `fetch_fulltext` via
`monkeypatch.setattr(tasks, "fetch_fulltext", stub)` avec un
`stub(pmcid, client)`** :
`test_pmc_stub_body_is_not_kept_as_fulltext` l.665 (stub l.671–672) et
`test_pmc_license_notice_sets_fulltext_note` l.688 (stub l.700–701). Ils
sont les **seuls** tests existants dont la signature de stub peut évoluer
(voir § Spécification, point 8 « Tests existants »).

## Spécification

### 1. Où placer le contrat

**Choix : un module dédié `backend/app/sources/__init__.py`** qui contient
le contrat (le « porteur » du contrat) et le type de bornes. Raisons
(ponytail, le plus petit périmètre qui suffit) :

- Le contrat n'a **aucune logique commune** à implémenter (les 4 opérations
  sont des façades sur des implémentations source-spécifiques). Un proto/
  dataclass de contrat suffit ; **pas de base de classe** avec méthodes à
  partager (pas d'héritage forcé, pas de logique centrale).
- Un module `app/sources` isolé (sans implémentation source) rend le contrat
  **importable et testable seul** sans tirer `app.pubmed` (httpx, settings)
  dans l'import. C'est le plus petit périmètre : un fichier, deux
  définitions (un dataclass de bornes + un `Protocol`).
- L'adapter PubMed **reste dans `backend/app/pubmed`** : il EST le module
  PubMed. On ne duplique pas le code : l'adapter expose les fonctions
  existantes derrière les 4 signatures du contrat (logique **déplacée**, pas
  réécrite).

Forme du contrat : un **`typing.Protocol`** (structural, `async`). Une classe
satisfait le contrat si elle expose les 4 méthodes — aucune contrainte
d'héritage. Le fake de test et `PubMedAdapter` le satisfont tous les deux.

### 2. Le porteur de bornes `DateBounds`

Aujourd'hui les bornes date sont portées par **`_term_bounded` appelé par le
pipeline** avant d'invoker les fonctions pubmed (`_do_import` l.299). C'est
un porteur **implicite** (query déjà bornée) : l'adapter ne peut pas
« re-borner ». Le contrat doit porter les bornes **explicitement** pour que
`search_volume`/`iter_ids` soient autoréférentiels (le pipeline ne pré-borne
plus la query, l'adapter le fait via `_term_bounded` **à l'intérieur** de
l'opération, exactement comme aujourd'hui — pas de double-borne).

```python
# backend/app/sources/__init__.py  (référence — non exhaustif)
from dataclasses import dataclass
from datetime import date
from typing import AsyncIterator, Protocol

@dataclass(frozen=True)
class DateBounds:
    date_from: date | None = None
    date_to: date | None = None
```

Le pipeline construit `bounds = DateBounds(date_from, date_to)` (mêmes
valeurs qu'aujourd'hui, cf. `trigger_import` l.484–486) et le passe tel quel
aux 4 opérations ; `bornes=None` ≡ `DateBounds()` ≡ bornes ouvertes.

### 3. Le contrat `SourceAdapter` — 4 signatures EXACTES

```python
# backend/app/sources/__init__.py  (référence)
class SourceAdapter(Protocol):
    async def search_volume(
        self, query: str, bornes: DateBounds | None
    ) -> int: ...

    async def iter_ids(
        self, query: str, bornes: DateBounds | None, limit: int | None = None
    ) -> AsyncIterator[str]: ...   # génère les native_id, les plus récents d'abord

    async def fetch_metadata(self, ids: list[str]) -> list[dict]: ...
    # liste d'articles natifs : native_id, title, abstract, doi?, pub_date?,
    #   authors? + identifiants secondaires plein texte (ex. pmcid)

    async def fetch_fulltext(self, id: str) -> tuple[str, list[dict]] | None: ...
    # (texte, sections) ; None = « non disponible » → fulltext_note
```

Conventions de signatures (à respecter mot pour mot) :

- `search_volume(query, bornes) -> int` : le `query` est le terme **non
  borné** ; l'adapter borne via `_term_bounded(query, bornes.date_from,
  bornes.date_to)` (PubMed) ou équivalent source, **avant** l'appel NCBI.
  `None` pour `bornes` = bornes ouvertes.
- `iter_ids(query, bornes, limit) -> AsyncIterator[str]` : **génère** les
  `native_id` (PubMed : PMIDs) page par page, **les plus récents d'abord** ;
  `limit` est le cap global (défaut `None` → cap `UNLIMITED`/`_esearch_max()`,
  même défaut qu'aujourd'hui). Le pipeline fait `async for native_id in
  adapter.iter_ids(query, bounds)`.
- `fetch_metadata(ids) -> list[dict]` : `ids` = `native_id` (PMIDs). Chaque
  dict natif porte au minimum `native_id`, `title`, `abstract`, `doi`,
  `pub_date`, `authors` et l'identifiant secondaire plein texte
  (`pmcid` pour PubMed). `ids == []` → `[]`.
- `fetch_fulltext(id) -> tuple[str, list[dict]] | None` : pour PubMed le
  `id` servi au plein texte est le **PMCID** (l'identifiant secondaire de la
  notice, cf. `_insert_article` l.617) ; renvoie `(methods, sections)` ou
  `None` quand le texte n'est pas disponible (→ `fulltext_note`, cf.
  `_insert_article` l.626).

### 4. Adapter PubMed n°1 — refactor de `backend/app/pubmed`

Un objet `PubMedAdapter` (classe mince) expose les 4 opérations du contrat
en **délegant** aux fonctions existantes du module (logique **déplacée**,
pas réécrite). Il capture son `http` et son `api_key` une fois, et les
réutilise pour chaque opération.

**Point clé — le `count` pour `fetch_all_pmids`** : la fonction
`fetch_all_pmids` (l.195) exige `count` en position (l.199) pour décider du
court-circuit (un seul esearch) vs la bissection. Le contrat `iter_ids(query,
bornes, limit)` ne porte pas de `count`. Pour rester **observable-identique**
(2 esearch au total : un pour le count, un pour les ids — cf. l.299 et
l.307–309 de `_do_import`), l'adapter **cache le résultat de
`search_volume`** et le réutilise dans `iter_ids`, au lieu de relancer
`search_volume` (qui serait un 3ᵉ esearch). Le pipeline appelle toujours
`search_volume` avant `iter_ids` avec la même `(query, bornes)`.

```python
# backend/app/pubmed/__init__.py  (référence — signatures exactes, corps = délégation)
class PubMedAdapter:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None) -> None:
        self._http = http
        self._api_key = api_key
        self._last_count: int | None = None  # caché de search_volume → réutilisé par iter_ids

    async def search_volume(self, query, bornes) -> int:
        b = bornes or DateBounds()
        term = _term_bounded(query, b.date_from, b.date_to)
        self._last_count = await search_volume(term, self._api_key, self._http)
        return self._last_count

    async def iter_ids(self, query, bornes, limit=None) -> AsyncIterator[str]:
        b = bornes or DateBounds()
        count = self._last_count  # réutilisation du count caché (pas de 3ᵉ esearch)
        if count is None:
            term = _term_bounded(query, b.date_from, b.date_to)
            count = await search_volume(term, self._api_key, self._http)
        pmids = await fetch_all_pmids(
            query, self._api_key, self._http, count, limit, b.date_from, b.date_to
        )
        for pmid in pmids:
            yield pmid

    async def fetch_metadata(self, ids) -> list[dict]:
        if not ids:
            return []
        return await fetch_metadata(ids, self._api_key, self._http)

    async def fetch_fulltext(self, id):
        if not id:
            return None
        methods, sections = await fetch_fulltext(id, self._http)
        if methods is None and not sections:
            return None  # « non disponible »
        return methods, sections
```

Points d'ancrage du refactor :

- **`fetch_all_pmids` est absorbé par `iter_ids` SANS changer le
  comportement observable** : même bissection (`PUBMED_WINDOW`, l.227–242),
  même ordre (plus récents d'abord, l.241), même dédoublonnage (`found`,
  l.225), même cap (`limit` → défaut, l.250), mêmes appels NCBI. La fonction
  `fetch_all_pmids` **reste** dans le module (les tests `test_pubmed.py`
  l'appellent encore) ; `iter_ids` est une **délégation** dessus, pas une
  réimplémentation.
- Le **chunking par lot de 100** reste dans le **pipeline**
  (`_chunks(pmids, _EFETCH_BATCH)`, l.340) et **non** dans `fetch_metadata` :
  c'est pourquoi `test_chunking_over_100_pmids` (pipeline, l.386) reste vert
  et `fetch_metadata` (pubmed) continue de traiter une liste entière en un
  `efetch` (l.253–265).
- `native_id` (PubMed) = **PMID** ; l'identifiant secondaire plein texte =
  **PMCID** (portée par la notice, cf. `_parse_pubmed_xml` et
  `_insert_article` l.617). `fetch_fulltext` prend le PMCID.
- `explain_query` et `fetch_methods` ne sont **pas** dans le contrat
  (pas consommés par le pipeline d'import ; hors périmètre Phase 1).

### 5. Pipeline `import_pubmed_task` passe par l'adapter (même flux, mêmes stats)

Le pipeline ne change que **la façon d'appeler la source** (adapter au lieu
de fonctions nues), pas le flux ni les stats. Concrètement :

- `_import_pubmed` (l.204), `_do_import` (l.286) et `_insert_article`
  (l.596) prennent un **`adapter: SourceAdapter | None = None`** en fin de
  signature. L'`adapter` est **construit une fois dans `_do_import`** (seul
  endroit où client et clé sont réunis : `api_key` résolu l.296, `client`
  l.296) : si `adapter is None`, on fait `adapter =
  PubMedAdapter(client, api_key)`. `_import_pubmed` se contente de le
  **transmettre** à `_do_import` (qui le transmet à `_insert_article`).
  Aucun changement de la signature **Celery** `import_pubmed_task` (wrapper
  sync, l.153) ni de l'endpoint.
- `_do_import` :
  - `count = await adapter.search_volume(query, bounds)` (remplace l.299)
  - `ids = [nid async for nid in adapter.iter_ids(query, bounds)]` —
    **sans** `limit` explicite → même défaut `_esearch_max()` qu'aujourd'hui
    (remplace l.307–309)
  - le reste (truncated l.310, `result_json` l.320–338, `for batch in
    _chunks(ids, _EFETCH_BATCH)` l.340, `metas = await
    adapter.fetch_metadata(batch)` l.342, `except` par batch l.343–348,
    `_insert_article(session, project.id, meta, client, adapter)`
    l.356–357, `_finish_success` l.367–378) est **inchangé**
- `_insert_article` (l.596) reçoit l'`adapter` (ajouté à sa signature) ; la
  ligne `methods, sections = await fetch_fulltext(pmcid, client)` (l.619)
  devient :
  ```python
  if adapter is not None:
      result = await adapter.fetch_fulltext(pmcid)
      methods, sections = (None, []) if result is None else result
  else:  # chemin défaut (tests existants, pas d'adapter injecté)
      methods, sections = await fetch_fulltext(pmcid, client)
  ```
  Le reste de la fonction (l.615–641 : `long_enough`, `pmc_not_archived`,
  `ON CONFLICT`) est **inchangé**. L'appel plein texte passe par
  l'`adapter` **quand l'adapter est injecté** ; le module-level
  `fetch_fulltext` reste le chemin par défaut quand il ne l'est pas (c'est
  la raison pour laquelle les 2 tests stub de l.665/l.688 restent verts
  sans modification — voir § 8).
- Les chemins `refs`/`citations`/`backfill_metadata` et `query_volume`
  (endpoint l.569) **ne sont pas touchés** en Phase 1 (ils restent sur
  `fetch_metadata` direct ; la source est encore PubMed partout).

**Invariants observables (à préserver)** : séquence d'appels NCBI identique
(esearch volume → esearch bissection/court-circuit → efetch par lot de 100) ;
même ordre des PMIDs (plus récents d'abord) ; mêmes clés de stats dans
`result_json` (`imported`, `failed`, `fulltext`, `truncated`, `total_count`,
`duplicates`) ; mêmes `fulltext_note` (`preprint_not_archived`, etc.) ;
dédoublonnage par `ON CONFLICT (project_id, pmid)` inchangé (l.632–641).

### 6. Le fake adapter (contrat testable seul)

Classe de référence **`FakeSourceAdapter`** (dans `backend/tests/`, ex.
`tests/fake_sources.py`) — **aucune NCBI, aucun DB, aucune session** ; elle
satisfait le `Protocol` `SourceAdapter` et **enregistre ses appels** pour les
assertions. Comportement minimal requis :

- `search_volume(query, bornes)` → un entier configurable
  (`self.volume`), et enregistre `("search_volume", query, bornes)`.
- `iter_ids(query, bornes, limit)` → un `async for` sur une liste de
  `native_id` configurables (`self.ids`), respectant le cap `limit` (si
  `limit` est fourni, borné à `limit`) ; enregistre
  `("iter_ids", query, bornes, limit)`.
- `fetch_metadata(ids)` → une liste de dicts natifs configurables
  (`self.meta_by_id` / `self.meta_list`), `ids == []` → `[]` ; enregistre
  `("fetch_metadata", ids)`.
- `fetch_fulltext(id)` → `(methods, sections)` ou `None` configurables
  (`self.fulltext_by_id`) ; enregistre `("fetch_fulltext", id)`.

Exposition du flux (ordre d'appels attendu) : le test d'intégration contrat
(§ Tests, `test_contract_fake_drives_import_pipeline_full_flow`) drive le
**vrai** pipeline `_import_pubmed(..., adapter=fake)` (aucun NCBI) et
assert l'**ordre** d'invocations : `search_volume` → `iter_ids` →
`fetch_metadata` (par lot) → `fetch_fulltext` (par article avec
identifiant secondaire), et les **stats** (`imported`/`fulltext`/`failed`/
`total_count`) dans le résultat.

### 7. Règles transverses (imposées par le contrat, observables)

Ces règles existent déjà dans `backend/app/pubmed` (doc module l.3–29) et
doivent rester **observables** à travers le contrat (adapter PubMed) :

- **httpx.AsyncClient injectable** : le client est injecté dans
  `PubMedAdapter.__init__` (jamais créé dans les opérations) ; le contrat
  s'implémente avec un client injecté (tests : `_client()` l.21–22 de
  `test_pubmed.py`, handler injecté dans `test_tasks_import.py`).
- **Erreur assainie par source** : toute erreur réseau/parse est levée en
  `NCBIError` **sans URL ni clé** (C-1, `NCBIError` l.80–86 ;
  `_raise_for_status` l.89–92). Le pipeline ne log jamais l'URL/clé
  (`_record_failure` l.656–678, `_sanitize_error` l.104–114).
- **Throttle par process et par source** : `_throttle` (l.95–97, intervalles
  l.46–47) est appelé entre les requêtes NCBI et pour `oa.fcgi` (l.20–21,
  l.292) ; il est neutralisé en test par `_no_sleep` (patch `app.pubmed._sleep`
  l.11–18 / l.22–29).
- **Retry backoff** : `_get_with_retry` (l.100–122) retrye 503/504/timeout
  (`_RETRIES` l.67, backoff `_RETRY_BACKOFF_SECONDS` l.68, via `_sleep`
  l.74) — observable par l'incrémentation du compteur `_sleep` et de
  `len(http.requests)`.
- **Limite de taille des payloads** : `_check_xml_size` (l.125–128,
  `_MAX_XML_BYTES` l.71) refuse les XML > 10 Mo → `NCBIError` 413.

Ces règles sont « observables à travers le contrat » car `PubMedAdapter`
les expose sans les modifier : un appel `adapter.fetch_metadata([...])`
sur un 503 retrye (retry), et une XML > 10 Mo lève `NCBIError` (limite),
avec le même `NCBIError` assaini (C-1) que la fonction nue.

### 8. Tests existants — ce qui change, ce qui reste intact

**Reste VERT SANS MODIFICATION (semantique et signature)** :

- **`backend/tests/test_pubmed.py` : TOUS les tests** (31 tests) — ils
  appellent les fonctions publiques du module (`search_volume`,
  `fetch_pmids`, `fetch_all_pmids`, `fetch_metadata`, `fetch_methods`,
  `fetch_fulltext`, `explain_query`) dont les signatures **publiques ne
  changent pas** (elles restent au module, l'adapter les délègue). Le
  refactor est purement additionnel (une classe + un module) ; rien n'est
  retiré du module.
- **`backend/tests/test_tasks_import.py` : TOUS les tests du pipeline
  `import_pubmed_task`/`_import_pubmed`**, dont **`test_chunking_over_100_pmids`
  (l.386)** (chunking par lot de 100, qui reste dans le pipeline l.340) et
  `test_import_pubmed_task_wrapper_passes_dates` (l.464).
- `test_query_volume*` (endpoint) — `query_volume` n'est pas touché.

**Design choisi pour `_insert_article` (ponytail, zéro test modifié)** :
l'`adapter` est **optionnel** et **nul par défaut**
(`adapter: SourceAdapter | None = None`). Quand il est nul (chemin défaut),
`_insert_article` appelle le `fetch_fulltext` module-level (l.619) comme
aujourd'hui ; quand il est fourni, il appelle `adapter.fetch_fulltext`.
Conséquence directe : **les 2 tests qui stubbent
`monkeypatch.setattr(tasks, "fetch_fulltext", stub)` restent inchangés**,
car ils appellent `_insert_article(session, project.id, meta, client)`
**sans adapter** (l.678–680, l.707–709) → chemin défaut → stub
module-level toujours appliqué.

**Ces 2 tests sont les SEULS candidats à une évolution de signature**
(`test_pmc_stub_body_is_not_kept_as_fulltext` l.665, stub l.671–672 ;
`test_pmc_license_notice_sets_fulltext_note` l.688, stub l.700–701), et
**uniquement si** l'implémentation choisit de **toujours** passer le plein
texte par l'`adapter` (adapter construit par défaut dans `_insert_article`,
jamais nul). Dans ce cas : le stub `async def stub(pmcid, client)` devient un
objet satisfaisant `SourceAdapter` (ex.
`FakeSourceAdapter(fulltext_by_id={"PMC9": (None, notice)})`) et l'appel
devient `_insert_article(session, project_id, meta, client, adapter=fake)`
— c'est le **seul** changement de signature ; l'assertion (`fulltext_note
== "preprint_not_archived"`, absence de sections, absence de
`fulltext_source`) reste **identique**. La spec **préconise** le premier
choix (zéro test modifié) ; le second est **autorisé** si l'on accepte de
toucher les 2 stubs en signature.

## Tests à écrire (TDD)

Convention : tests du contrat/fake dans `backend/tests/test_sources.py`
(nouveau fichier) ; tests de l'adapter PubMed dans `backend/tests/test_pubmed.py`
(additionnels, **sans toucher** aux tests existants) ; tests pipeline par
l'adapter dans `backend/tests/test_tasks_import.py` (additionnels). Les
« fixture » sont les objets existants : `_client()` (l.21–22, test_pubmed),
`_no_sleep` (autouse l.11–18), `_ncbi_handler` (l.77–93,
test_tasks_import), `engine` (conftest), `_make_project` (l.96),
`FakeSourceAdapter`.

### A. Contrat seul, avec le fake adapter (aucune NCBI, aucun DB)

1. **`test_contract_fake_drives_import_pipeline_full_flow`**
   Fixture : `FakeSourceAdapter(volume=3, ids=["3","2","1"], meta_by_id={
   "3": {"native_id":"3","title":"t3","pmcid":"PMC3"}, ...},
   fulltext_by_id={"PMC3": ("methods", [s1]), ...})` +
   `engine`/`_make_project`. Drive le **vrai** `_import_pubmed(...,
   adapter=fake)`.
   Assertion clé : l'ordre des appels est `search_volume` → `iter_ids` →
   `fetch_metadata` (par lot) → `fetch_fulltext` (par article) ; le
   résultat porte `imported == 3`, `fulltext == 2`, `failed == 0`,
   `total_count == 3` ; les `Article` créés ont `fulltext` (méthodes) et
   `sections` pour les 2 articles avec fulltext, et `fulltext_note ==
   "preprint_not_archived"` pour l'article dont `fulltext_by_id` renvoie
   une notice de licence (cf. `pmc_not_archived`).

2. **`test_contract_fake_volume_zero_empty_success`**
   Fixture : `FakeSourceAdapter(volume=0, ids=[])`. Drive
   `_import_pubmed(..., adapter=fake)`.
   Assertion clé : `iter_ids` n'est **jamais** appelé (pas d'appel
   `iter_ids` dans `fake.calls` après `search_volume`) ; résultat
   `imported == 0`, `total_count == 0`, `truncated is False`.

3. **`test_contract_fake_truncated_by_limit`**
   Fixture : `FakeSourceAdapter(volume=50, ids=[f"P{i}" for i in range(50)],
   meta_by_id=...)`. Drive `adapter.iter_ids("q", None, limit=30)`.
   Assertion clé :
   `len([nid async for nid in fake.iter_ids("q", None, limit=30)]) == 30` ;
   les `native_id` servis sont les **30 premiers** de la liste (ordre
   préservé) ; le reste n'est pas généré.

4. **`test_contract_fake_error_sanitized_job_failure`**
   Fixture : `FakeSourceAdapter` dont `search_volume` lève une exception
   custom (pas `NCBIError`) ; `_import_pubmed(..., adapter=fake)`.
   Assertion clé : le job passe en `FAILED` (non `PROCESSING`) ; le
   `error_message` du job **ne contient ni l'URL ni la clé API** (pas de
   `ncbi.nlm.nih.gov`, pas de `api_key=` dans `error_message`) —
   `search_volume`/`fetch_*` de l'adapter doit assainir en `NCBIError` (ou
   l'équivalent source) et le pipeline doit le convertir (C-1).

### B. Adapter PubMed via le contrat — `iter_ids` équivalent `fetch_all_pmids`
(les 4 tests utilisent `PubMedAdapter(_client(), "key")`, avec le
`monkeypatch` de `app.pubmed._sleep` et un `httpx` mocké comme dans
`test_pubmed.py` existants)

5. **`test_iter_ids_under_window_equals_fetch_all_pmids`**
   Fixture : esearch `retcount=50`, `retmax=9999` ; efetch 50 ids ;
   `PubMedAdapter.iter_ids("q", None)`.
   Assertion clé : `async for` produit exactement les 50 `native_id` de
   `fetch_all_pmids` **dans le même ordre** (1 esearch, 1 efetch) ;
   équivalence `list(ids_from_iter) == ids_from_fetch_all_pmids`.
   Nombre d'appels NCBI : **2** (esearch count + esearch ids), identique au
   court-circuit `fetch_all_pmids` (l.222–224).

6. **`test_iter_ids_bisection_order_and_calls`**
   Fixture : `retcount=12000` ; efetch avec ids `12000,11999,…` (les plus
   récents d'abord). Drive `adapter.iter_ids("q", None)`.
   Assertion clé : même séquence d'appels que
   `test_fetch_all_pmids_splits_queries_over_the_ncbi_window` (l.413) —
   `retmax=9999` puis bissection par moitiés ; les appels esearch sont
   identiques (mêmes termes bornés, mêmes `retmax`) ; l'ordre des
   `native_id` générés est strictement décroissant dans chaque feuille, et
   les feuilles sont dans l'ordre « plus récents d'abord » (l.241 de
   `fetch_all_pmids`). Nombre d'appels NCBI identique à
   `fetch_all_pmids` avec le même count.

7. **`test_iter_ids_user_bounds`**
   Fixture : `retcount=50` ; `adapter.iter_ids("q", DateBounds(
   date_from=date(2024,1,1), date_to=date(2025,1,1)))`.
   Assertion clé : l'appel `esearch` porte le terme borné
   `(q) AND ("2024-01-01"[dp] : "2025-01-01"[dp])` (cf.
   `_term_bounded` l.131–138) ; **identique** au comportement de
   `test_fetch_all_pmids_bisection_uses_user_date_bounds` (l.447) — la
   borne n'apparaît **que** dans le terme, pas dans `datetype` / `mindate`
   / `maxdate`.

8. **`test_iter_ids_limit_cap_most_recent`**
   Fixture : `retcount=20000` ; efetch 15000 ids. Drive
   `adapter.iter_ids("q", None, limit=1500)`.
   Assertion clé : `len([nid async for nid in ...]) == 1500` ; les ids
   servis sont les **1500 premiers** (les plus récents) de la liste ; les
   appels NCBI sont identiques à `fetch_all_pmids` avec `limit=1500` (cf.
   `test_fetch_all_pmids_user_dates_cap_keeps_most_recent` l.565).

### C. Règles transverses (observables à travers le contrat)

9. **`test_adapter_503_retries_and_error_sanitized`**
   Fixture : `httpx` mocké qui renvoie deux `503` puis une `200` (esearch) ;
   `adapter = PubMedAdapter(_client(), "key")`.
   Assertion clé : `len(http.requests) == 3` (retry sur 503, cf.
   `test_esearch_retry_on_503_then_success` l.92 de `test_pubmed.py`) ; le
   `sleep_mock` est appelé `_RETRIES` fois (l.67) avec un backoff croissant
   ; en cas d'échec définitif, l'exception est une `NCBIError` **sans**
   l'URL dans `str(e)` (C-1, cf.
   `test_esearch_retry_exhausted_raises` l.107).

10. **`test_adapter_timeout_retries_and_sanitized`**
    Fixture : `httpx` mocké qui lève `httpx.ConnectError` deux fois puis
    `200`. Drive `adapter.search_volume("q", None)`.
    Assertion clé : `len(http.requests) == 2` (timeout épuisé) ; l'exception
    est une `NCBIError` avec un message **ne contenant pas** l'URL
    (`"timeout"`, sans `"ncbi.nlm.nih.gov"`) — cf.
    `test_timeout_raises_ncbi_error` l.128.

11. **`test_adapter_metadata_oversize_payload_rejected`**
    Fixture : `httpx` mocké qui renvoie une XML de 11 Mo (200) pour
    `efetch`. Drive `adapter.fetch_metadata(["1"])`.
    Assertion clé : lève `NCBIError` avec un message contenant
    `"too large"` (cf. `_check_xml_size` l.125–128,
    `test_xml_size_limit_metadata` l.189). L'URL/clé ne sont
    pas dans le message (C-1).

12. **`test_adapter_throttle_intervals`**
    Fixture : `httpx` mocké (200) ; `_sleep` mocké. Drive
    `adapter.search_volume("q", None)` puis `adapter.iter_ids("q", None)`
    (2 esearch + 1 efetch).
    Assertion clé : le nombre d'appels `http.requests` est exactement
    celui d'un `fetch_all_pmids` équivalent (cf.
    `test_fetch_all_pmids_splits_queries_over_the_ncbi_window` l.413) ;
    les intervalles `_throttle` sont respectés (cf.
    `test_search_volume_returns_count` l.28 — le throttle est appelé avant
    chaque esearch) ; `oa.fcgi` (fetch_fulltext) est throttlé
    avec l'intervalle `None` (cf. `_fetch_pmc_xml` l.292).

### D. Conformité au contrat (fake + adapter)

13. **`test_pubmed_adapter_is_source_adapter`**
    Fixture : `PubMedAdapter(_client(), None)` (aucun appel réseau).
    Assertion clé : l'objet expose les 4 méthodes
    (`search_volume`, `iter_ids`, `fetch_metadata`, `fetch_fulltext`)
    **et** leur signature (via `inspect.signature`) est compatible avec le
    `Protocol` `SourceAdapter` (même nom, mêmes paramètres positionnels,
    mêmes annotations de retour). Aucun appel NCBI.

14. **`test_fake_is_source_adapter`**
    Fixture : `FakeSourceAdapter(volume=1, ids=["1"], meta_by_id={"1": {
    "native_id": "1", "title": "t", "pmcid": "PMC1"}}, fulltext_by_id={
    "PMC1": ("m", [])})`.
    Assertion clé : l'objet satisfait le `Protocol` (4 méthodes, signatures
    compatibles) ; un appel `async for nid in fake.iter_ids("q", None)`
    produit `["1"]` ; `await fake.fetch_metadata([]) == []` ;
    `await fake.fetch_fulltext("nope") is None` (cas « non disponible »).

15. **`test_datebounds_defaults`**
    Fixture : `DateBounds()`.
    Assertion clé : `DateBounds() == DateBounds(None, None)` ;
    `DateBounds().date_from is None` et `DateBounds().date_to is None`
    ; `DateBounds(date(2024,1,1), date(2025,1,1))` est
    `frozen` (immutabilité, `frozen=True` du dataclass).

### E. Tests existants qui DOIVENT rester inchangés et verts

- **`backend/tests/test_pubmed.py` : TOUS les tests** (31 tests, listés en
  § État actuel) — en particulier
  `test_fetch_all_pmids_splits_queries_over_the_ncbi_window` (l.413),
  `test_fetch_all_pmids_bisection_uses_user_date_bounds` (l.447),
  `test_fetch_all_pmids_user_dates_cap_keeps_most_recent` (l.565). Ils
  appellent les fonctions **publiques du module** dont les signatures ne
  changent pas.
- **`backend/tests/test_tasks_import.py` : TOUS les tests du pipeline
  d'import**, dont **`test_chunking_over_100_pmids` (l.386)** (chunking
  par lot de 100, qui reste dans le pipeline l.340) et
  `test_import_pubmed_task_wrapper_passes_dates` (l.464).
- `test_query_volume*` (endpoint) — `query_volume` n'est pas touché.
- **À basculer si l'adapter modifie le chemin `fetch_fulltext` dans
  `_insert_article`** : `test_pmc_stub_body_is_not_kept_as_fulltext`
  (l.665) et `test_pmc_license_notice_sets_fulltext_note` (l.688) — le
  stub devient un `FakeSourceAdapter` (ou objet satisfaisant
  `SourceAdapter`) et l'appel `_insert_article(..., adapter=fake)` est
  adapté ; **l'assertion** (`fulltext_note == "preprint_not_archived"`,
  absence de sections, absence de `fulltext_source`) reste **identique**.

## Critères d'acceptation (phase 1)

1. **`backend/app/sources/__init__.py`** contient le `Protocol`
   `SourceAdapter` (4 signatures exactes du § 3) et le dataclass
   `DateBounds` (frozen, 2 champs `date | None`, défaut `None`).
2. **`backend/app/pubmed/__init__.py`** contient la classe
   `PubMedAdapter` (4 méthodes délégant aux fonctions existantes du module,
   `http` et `api_key` injectés via `__init__`, cache du count de
   `search_volume` réutilisé par `iter_ids`). Les fonctions publiques
   (`search_volume`, `fetch_all_pmids`, `fetch_metadata`, `fetch_fulltext`,
   `fetch_methods`, `explain_query`, `_term_bounded`, `_esearch_max`,
   `NCBIError`) restent **telles quelles** (le module n'est pas réécrit).
3. **`backend/app/tasks/__init__.py`** : `_import_pubmed`, `_do_import`,
   `_insert_article` prennent un `adapter: SourceAdapter | None = None`
   (défaut → `PubMedAdapter(client, api_key)`). Le flux observable
   (NCBI, stats, dédoublonnage) est **inchangé**. Le chunking par lot de
   100 reste dans `_do_import` (l.340). La signature Celery
   `import_pubmed_task` et l'endpoint `trigger_import` sont **inchangés**.
4. **`backend/tests/test_sources.py`** (nouveau) : les 15 tests du § Tests
   (A.1–A.4, B.5–B.8, C.9–C.12, D.13–D.15) passent.
5. **`backend/tests/test_pubmed.py`** : TOUS les tests existants passent
   **sans modification** (les tests de la fonction `fetch_all_pmids`, dont
   les tests de bissection l.413, l.447, l.529, l.597, restent verts car la
   fonction est intacte).
6. **`backend/tests/test_tasks_import.py`** : TOUS les tests existants
   passent ; `test_chunking_over_100_pmids` (l.386) est **inchangé** ;
   `test_pmc_stub_body_is_not_kept_as_fulltext` (l.665) et
   `test_pmc_license_notice_sets_fulltext_note` (l.688) passent, avec
   éventuellement un stub adapté (changement de signature du stub, **pas**
   de l'assertion) si l'adapter est passé à `_insert_article`.
7. **Aucun endpoint modifié** : `POST /projects/{id}/import`
   (`trigger_import`, l.465) et `POST /projects/{id}/query-volume`
   (`query_volume`, l.569) ont le même contrat (`ImportRequest`
   l.112–124, retour `{job_id, status}` / `{count, max}`). Aucune nouvelle
   colonne DB. Aucune autre source n'est implémentée.
8. **Le contrat est testable seul** : les tests A.1–A.4 utilisent un
   `FakeSourceAdapter` et **ne font aucun appel NCBI** (aucun
   `httpx` réel ; le pipeline est drivé avec l'adapter injecté).

## Contraintes

- **Ponytail (le plus petit périmètre qui suffit)** : ne rien réécrire,
  déplacer le code pubmed **derrière** le contrat ; l'adapter est une
  délégation mince (4 méthodes + `__init__` + cache du count). Pas de base
  de classe, pas de factory, pas de registre de sources (Phase 2+).
- **Le comportement observable est un invariant** : mêmes appels NCBI,
  même ordre des `native_id`, mêmes stats, mêmes `fulltext_note`, même
  dédoublonnage `ON CONFLICT`. Un test existant de `test_pubmed.py` qui
  passe aujourd'hui doit passer demain **sans modification** (sauf les 2
  stubs `fetch_fulltext` du § 8, qui peuvent évoluer **en signature
  seulement**).
- **Pas de code d'implémentation dans cette spec** : signatures exactes +
  extraits de code de référence uniquement. Le fichier de spec est le seul
  livrable.
- **Les 2 chemins `refs`/`citations` et `backfill_metadata` ne sont pas
  touchés** en Phase 1 (ils restent sur `fetch_metadata` direct ; l'adapter
  n'y est pas passé). Ce sont des candidats Phase 2 (source mixte dans le
  corpus, cf. BACKLOG item 39).
- **Le throttle, le retry, la limite de payload, l'assainissement d'erreur
  sont hérités du module pubmed** (doc module l.3–29) ; ils ne sont **pas**
  réimplémentés dans l'adapter (qui délègue). Ils doivent rester
  **observables** via le contrat (tests C.9–C.12).
- **Le count de `search_volume` est caché par l'adapter et réutilisé par
  `iter_ids`** : le pipeline appelle `search_volume` avant `iter_ids` avec
  la même `(query, bornes)` ; l'adapter ne relance pas `search_volume` dans
  `iter_ids` (pas de 3ᵉ esearch). Si `iter_ids` est appelé sans
  `search_volume` préalable (cas hors pipeline), l'adapter fait le
  fallback (1 esearch supplémentaire, acceptable hors pipeline).
- **Garde-fous de la tâche** : ne modifier **que**
  `specs/2026-10-28-phase1-source-adapter.md`. Aucun autre fichier.

## Hors périmètre (phase 1)

- **Aucun changement de schéma DB** : pas de nouvelle colonne `source`, pas
  de nouvelle table `sources` (cf. BACKLOG item 38, § schéma de phase 2 de
  l'architecture l.161–173).
- **Aucune nouvelle API visible** : pas de nouveau endpoint, pas de
  modification de `trigger_import` (l.465) ni de `query_volume` (l.569),
  pas de champ `source` dans `ImportRequest` (l.112–124) ni dans
  `Article` (l.258+).
- **Aucune autre source** (l'adapter Crossref/DOI est le n°2, Phase 2 ;
  cf. § 3 de l'architecture).
- **Aucun changement de UI** (frontend Next.js, cf. BACKLOG item 37 pour
  la source dans l'UI).
- **Aucun changement du comportement du pipeline** (flux, stats,
  dédoublonnage, chunking, `fulltext_note`) : le refactor est
  purement **interne** au backend.
- **Aucun changement des chemins `refs`/`citations`/`backfill_metadata`**
  (restent branchés sur `fetch_metadata` direct ; l'adapter n'y est pas
  passé en Phase 1).

## Addendum (2026-10-28) — liste canonique des tests de `test_pubmed.py`

Les noms de tests cités plus haut dans cette spec (sections « État actuel » et
« Tests ») peuvent diverger des noms réels du fichier : **cette liste est
canonique** (extraite du fichier, 31 tests). Les références par **numéro de
ligne** sont stables et prioritaires ; en cas de divergence de nom, s'en tenir
à cette liste. Le test « l.559 » cité plus haut n'existe pas — l'ignorer.

- l.28 `test_search_volume_returns_count`
- l.41 `test_search_volume_sends_api_key`
- l.54 `test_fetch_pmids_default_retmax_is_configured_cap`
- l.66 `test_fetch_pmids_never_asks_more_than_the_ncbi_window`
- l.80 `test_fetch_pmids_sends_api_key`
- l.92 `test_esearch_retry_on_503_then_success`
- l.107 `test_esearch_retry_exhausted_raises`
- l.128 `test_timeout_raises_ncbi_error`
- l.143 `test_fetch_methods_503_raises_ncbi_error`
- l.156 `test_efetch_retry_on_timeout`
- l.172 `test_no_retry_on_parse_error`
- l.189 `test_xml_size_limit_metadata`
- l.201 `test_xml_size_limit_methods`
- l.259 `test_fetch_metadata_sends_api_key`
- l.269 `test_fetch_metadata_parses_xml`
- l.295 `test_fetch_metadata_empty_list`
- l.329 `test_fetch_methods_extracts_methods_section`
- l.345 `test_fetch_methods_none_without_pmcid`
- l.353 `test_fetch_methods_none_when_no_methods_section`
- l.373 `test_parse_biblio_metadata`
- l.396 `test_sections_drop_latex_and_mark_display_formulas`
- l.413 `test_fetch_all_pmids_splits_queries_over_the_ncbi_window`
- l.447 `test_fetch_all_pmids_bisection_uses_user_date_bounds`
- l.492 `test_fetch_all_pmids_only_date_from_keeps_default_hi`
- l.529 `test_fetch_all_pmids_only_date_to_keeps_default_lo`
- l.565 `test_fetch_all_pmids_user_dates_cap_keeps_most_recent`
- l.597 `test_fetch_all_pmids_no_dates_unchanged_bounds`
- l.633 `test_fetch_all_pmids_shortcut_with_dates_single_bounded_esearch`
- l.658 `test_fetch_all_pmids_shortcut_with_dates_no_dp_doubling_when_query_has_dp`
- l.686 `test_fetch_all_pmids_bisection_query_with_dp_keeps_leaf_subrange`
- l.729 `test_import_cap_zero_means_unlimited`
