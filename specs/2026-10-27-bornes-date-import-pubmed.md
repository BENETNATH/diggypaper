# Bornes date de l'import PubMed (`date_from` / `date_to`)

Date : 2026-10-27 — spec d'implémentation TDD (agent de codage).

## Contexte

L'import PubMed n'accepte qu'une chaîne de requête libre : `ImportRequest` n'a qu'un seul
champ `query`, et la bissection NCBI des gros jeux de résultats parcourt toute la plage
1800 → aujourd'hui+366 j. Le backlog « Évolutions prévues » n°1 (BACKLOG.md:38–41) demande
des `date_from`/`date_to` optionnelles, ANDées dans la requête NCBI et utilisées comme
bornes de la bissection `walk()`, pour réduire le nombre d'appels esearch.

## État actuel

- `ImportRequest` (backend/app/api/v1/projects.py:112–114) : un seul champ
  `query: str = Field(min_length=1, max_length=2000)` (m8).
- `POST /{project_id}/import` — `trigger_import` (projects.py:450–479) déclenche
  `import_pubmed_task.delay(str(project.id), payload.query)` (projects.py:474).
- Task Celery `import_pubmed_task` (backend/app/tasks/__init__.py:152–187, signature
  l.153–155) → core `_import_pubmed` (l.190–267) → `_do_import` (l.259, 270–285) :
  - compte de volume : `count = await search_volume(query, api_key, client)` (l.277),
    esearch `retmax=0` (app/pubmed/__init__.py:138–144) ;
  - `count == 0` → succès vide, projet FROZEN (tasks/__init__.py:278–284 ; test
    backend/tests/test_tasks_import.py:163–185) ;
  - `pmids = await fetch_all_pmids(query, api_key, client, count)` (l.285) ;
  - `truncated = count > _esearch_max()` (l.286) — cap `import_max_articles` (défaut 5000,
    app/pubmed/__init__.py:54–58).
- `fetch_all_pmids` (backend/app/pubmed/__init__.py:185–233) :
  - chemin `count <= PUBMED_WINDOW` (9 999, l.63) : un seul esearch
    `fetch_pmids(query, …, min(count, limit))` (l.206–208), ordre = celui de NCBI (comment
    l.207) ;
  - sinon bissection `walk()` (l.211–226) : terme des feuillets
    `({query}) AND ("{lo:%Y/%m/%d}"[dp] : "{hi:%Y/%m/%d}"[dp])` (l.214), les plus récents
    d'abord (l.225), bornes initiales `date(1800,1,1) → date.today()+366` (l.228), le cap
    `limit` garde les plus récents (l.212–213, 233).
- Garde d'ordre (BACKLOG.md:14–18) : en deçà de la fenêtre NCBI, l'ordre est celui de NCBI
  et ne doit pas être inversé — encadré par `test_chunking_over_100_pmids`
  (backend/tests/test_tasks_import.py:386–409).

## Spécification

1. **Requête.** `ImportRequest` (projects.py:112) reçoit deux champs optionnels :
   `date_from: date | None = None` et `date_to: date | None = None` (dates ISO
   `YYYY-MM-DD`, type `date` de pydantic). Un `model_validator(mode="after")` :
   `date_from > date_to` → `ValueError` → **HTTP 422** (aucun job créé). Aucune borne
   fournie → comportement identique à aujourd'hui.

2. **Plombage.** `trigger_import` (projects.py:474) passe les bornes au task :
   `import_pubmed_task.delay(str(project.id), payload.query, date_from=payload.date_from,
   date_to=payload.date_to)`. Les signatures de `import_pubmed_task` (tasks/__init__.py:
   153–155, appel l.174), `_import_pubmed` (l.190–198, appel l.259) et `_do_import`
   (l.270–285) reçoivent `date_from: date | None = None` / `date_to: date | None = None`
   et les propagent. Le chemin `refs` (`_do_import_refs`, l.257–258) et les citations sont
   hors périmètre.

3. **Terme NCBI borné (helper unique).** Nouvelle fonction de module dans
   `app/pubmed/__init__.py` (à côté de `_esearch_params`, l.131), importable depuis
   `app/tasks` comme l'est déjà `_esearch_max` (tasks/__init__.py:58) :
   `_term_bounded(query, date_from, date_to) -> str` :
   - aucune borne fournie → la query brute (zéro changement) ;
   - la query contient déjà `[dp]` (regex `\[dp\]`, insensible à la casse) → la query
     brute (**ne pas doubler le champ**) ;
   - sinon → `(query) AND ("{lo:%Y/%m/%d}"[dp] : "{hi:%Y/%m/%d}"[dp])`, format identique au
     feuillet existant (l.214), avec `lo = date_from or date(1800,1,1)` et
     `hi = date_to or date.today()+timedelta(days=366)` (une borne absente retombe sur la
     valeur par défaut actuelle).

4. **Compte de volume.** `_do_import` (l.277) : `count = await
   search_volume(_term_bounded(query, date_from, date_to), api_key, client)`. Sans bornes,
   terme = query brute (l.277 inchangée en comportement). `total_count` renvoyé = count
   borné (cohérent avec le drapeau `truncated`, l.286).

5. **Chemin direct (`count <= PUBMED_WINDOW`, l.206–208).** Un seul esearch, sans
   bissection : `fetch_pmids(_term_bounded(query, date_from, date_to), api_key, http,
   min(count, limit))` — retmax = `min(count, limit)` comme aujourd'hui, ordre = celui de
   NCBI, **aucune inversion**. Sans bornes : byte-par-byte identique à l'état actuel
   (garde dure, cf. Contraintes).

6. **Bissection (`count > PUBMED_WINDOW`, l.211–226).** Mécanique inchangée
   (recursion l.224–226, plus récents d'abord, format du terme l.214) ; seule l'appel
   initial change (l.228) : `await walk(lo, hi)` avec `lo = date_from or
   date(1800,1,1)`, `hi = date_to or date.today()+366`. Le feuillet garde son sous-intervalle
   `[dp]` même si la query en contient déjà (intersection avec les bornes propres de la
   query — mécanique existante, indispensable à la convergence de la bissection). Le cap
   `limit` garde les plus récents **dans la plage utilisateur** (l.212–213, 233).

7. **Cas limites.**
   - **Une seule borne** : l'autre retombe sur le défaut (`1800-01-01` ou `today+366`)
     — items 3 et 6.
   - **Bornes inversées** (`date_from > date_to`) : 422 (item 1) ; pas de job.
   - **Borne dans le futur** : acceptée, aucun clamp. `date_to > today` : hi = date_to
     (NCBI n'a pas d'articles futurs). `date_from > today` : count 0 → import vide,
     FROZEN (l.278–284, test test_tasks_import.py:163–185).
   - **Cap `import_max_articles`** : quand le jeu (borné) dépasse le cap, on garde les plus
     récents — garanti sur le chemin de bissection (visite plus récents d'abord + arrêt
     précoce, l.212–213, 225) ; sur le chemin direct, le cap s'applique côté NCBI via
     `retmax = min(count, limit)` (l.208), ordre NCBI conservé (pas d'inversion, garde
     dure).
   - **Query contenant déjà `[dp]`** : pas de doublon du champ — compte et chemin direct
     utilisent la query brute (item 3). Conséquence (cas d'angle documenté, volontaire) :
     sur le chemin direct, les bornes structurées ne s'appliquent pas à une query
     auto-bornée ; sur le chemin de bissection, les feuillets croisent les bornes
     utilisateur et propres (item 6).

## Tests à écrire (TDD)

Compatibles avec le pytest asyncio existant du repo (tests `async def` nus,
`monkeypatch`, `httpx.MockTransport` ; fixture autouse `_no_sleep` en
backend/tests/test_pubmed.py:11–18).

Dans `backend/tests/test_pubmed.py` (niveau module ; `search_volume`/`fetch_pmids`
monkeypatchés comme aux l.432–439) :

1. `test_fetch_all_pmids_bisection_uses_user_date_bounds` —
   `monkeypatch.setattr(pubmed, "PUBMED_WINDOW", 5)` (cf. l.421) ; 10 articles dans
   2021-01-10 → 2021-01-19 ; `fetch_all_pmids("q", None, None, count=10, limit=10**9,
   date_from=date(2021,1,5), date_to=date(2021,1,20))`. Assertions : les 10 pmids renvoyés,
   `got[0]` = le plus récent ; chaque terme `[dp]` vu par `search_volume`/`fetch_pmids` a
   ses bornes dans `["2021/01/05", "2021/01/20"]` (hi de racine = 2021/01/20, pas
   today+366).
2. `test_fetch_all_pmids_only_date_from_keeps_default_hi` — `date_from` seul : le terme de
   racine contient `"2021/01/05"[dp]` en borne basse et la borne haute vaut
   `date.today()+366` (≥ today).
3. `test_fetch_all_pmids_only_date_to_keeps_default_lo` — `date_to` seul : la borne basse
   de racine reste `1800/01/01`.
4. `test_fetch_all_pmids_user_dates_cap_keeps_most_recent` — count > fenêtre
   (PUBMED_WINDOW=5), 30 articles dans la plage, `limit=12` : `len(got) == 12` et
   `got[0]` = le plus récent **de la plage** (même pattern que l.443–444).
5. `test_fetch_all_pmids_no_dates_unchanged_bounds` — sans bornes : le terme de racine
   contient `"1800/01/01"[dp]` et `(date.today()+timedelta(days=366)):%Y/%m/%d` ; tous les
   articles récupérés (verrouille le comportement actuel).
6. `test_fetch_all_pmids_shortcut_with_dates_single_bounded_esearch` — `count=40` ≤
   PUBMED_WINDOW ; `fetch_pmids` monkeypatché capturant (terme, retmax) et renvoyant une
   liste fixe `["a", "b", "c"]`. Assertions : terme ==
   `(query) AND ("{from:%Y/%m/%d}"[dp] : "{to:%Y/%m/%d}"[dp])` (format l.214),
   retmax == `min(count, limit)`, retour == `["a", "b", "c"]` **sans inversion** (ordre
   NCBI — BACKLOG.md:17–18).
7. `test_fetch_all_pmids_shortcut_with_dates_no_dp_doubling_when_query_has_dp` — query
   `cancer AND 2020/01/01[dp] : 2020/12/31[dp]`, `count=5` ≤ fenêtre, bornes données :
   `fetch_pmids` est appelé avec la query brute (aucun `[dp]` ajouté).
8. `test_fetch_all_pmids_bisection_query_with_dp_keeps_leaf_subrange` — query avec `[dp]`,
   PUBMED_WINDOW=5, count=8 > 5 : les feuillets restent au format l.214
   `({query}) AND ("{sub}"[dp] : "{sub}"[dp])`, sous-intervalle dans les bornes
   utilisateur ; convergence bornée (assertions sur le nombre d'appels, ex. < 100) ; tous
   les articles de l'intersection sont renvoyés.

Dans `backend/tests/test_tasks_import.py` (intégration, fixture `engine`) :

9. `test_import_pubmed_count_term_bounded_by_dates` —
   `_import_pubmed(str(project.id), "cancer", session_factory=factory, http=http,
   date_from=date(2021,1,1), date_to=date(2021,12,31))` ; handler : l'esearch
   `retmax=0` (compte) reçoit un `term` contenant `"2021/01/01"[dp]` et
   `"2021/12/31"[dp]` ; count=2 → 2 articles importés ; `result["total_count"] == 2`.
10. `test_import_pubmed_without_dates_count_term_unchanged` — sans bornes : le `term` de
    l'esearch compte est la query brute `"cancer"` (pas de `[dp]`) — verrouille l.277.
11. `test_import_pubmed_task_wrapper_passes_dates` — même pattern que l.572–593
    (`AsyncMock` sur `app.tasks._import_pubmed`) :
    `import_pubmed_task("proj-id", "q", date_from=date(2024,1,1), date_to=date(2024,6,30))`
    → `mock_core.await_args.kwargs` contient `date_from` et `date_to`.

`ImportRequest` (à placer dans le fichier de tests existant qui couvre l'endpoint
d'import ; test pydantic pur, sans TestClient) :

12. `test_import_request_dates_optional_and_reversed_rejected` —
    `ImportRequest(query="q")` → `date_from is None and date_to is None` ;
    `ImportRequest(query="q", date_from="2024-01-15", date_to="2024-06-30")` →
    `(date(2024,1,15), date(2024,6,30))` ;
    `pytest.raises(ValidationError)` pour `date_from="2024-05-01", date_to="2024-01-01"`
    (→ 422 via FastAPI, aucun job).

## Critères d'acceptation

- [ ] `ImportRequest` : `date_from`/`date_to` optionnelles (ISO), bornes inversées → 422,
      aucune borne → comportement identique à aujourd'hui
- [ ] compte esearch (`retmax=0`) et chemin direct utilisent le terme borné quand les
      bornes sont données et `[dp]` absent de la query ; `total_count` = count borné
- [ ] la bissection démarre de `walk(date_from or 1800-01-01, date_to or today+366)`
- [ ] plus récents d'abord + cap `import_max_articles` : les plus récents de la plage sont
      gardés (bissection) ; ordre NCBI non inversé sur le chemin direct
- [ ] query contenant `[dp]` : aucun doublon du champ dans le terme du compte / du chemin
      direct
- [ ] `test_chunking_over_100_pmids` (test_tasks_import.py:386–409) et
      `test_fetch_all_pmids_splits_queries_over_the_ncbi_window` (test_pubmed.py:413–444)
      passent **sans modification**
- [ ] tous les tests existants de `test_pubmed.py` / `test_tasks_import.py` passent
- [ ] aucune migration DB, aucune nouvelle colonne `import_jobs`, aucun changement frontend
      (champs optionnelles, compatible descendant)

## Contraintes

- Surface minimale (style ponytail) :
  - `projects.py` : 2 champs + 1 `model_validator` sur `ImportRequest` (l.112–114) ; 1
    ligne dans `trigger_import` (l.474) ;
  - `tasks/__init__.py` : pass-through de kwargs optionnels (l.153–155, l.174, l.190–198,
    l.259) + terme borné au compte (l.277) + kwargs transmis à `fetch_all_pmids` (l.285) ;
  - `app/pubmed/__init__.py` : 1 helper `_term_bounded` (terme + détection `[dp]`) +
    signature de `fetch_all_pmids` (2 kwargs optionnels, défaut `None`) + terme du chemin
    direct (l.208) + bornes du `walk` (l.228) ; le format du feuillet (l.214) et la
    recursion (l.224–226) sont inchangés.
- **Garde dure** : le chemin `count <= PUBMED_WINDOW` (retour direct, sans bissection)
  doit rester intact quand aucune borne n'est donnée — un seul esearch, ordre NCBI ;
  `test_chunking_over_100_pmids` encadre cet ordre (BACKLOG.md:14–18). Seul le chemin de
  bissection (feuillets) est modifié, et seulement dans ses bornes initiales.
- Détection `[dp]` : `re.search(r"\[dp\]", query, re.IGNORECASE)`.
- Pas de changement du traitement des erreurs NCBI (C-1), du throttle, des retries (m5) ni
  de la limite XML (I5).
- Pas de changement de l'import `refs` ni des citations (chemins séparés,
  tasks/__init__.py:253–258).
