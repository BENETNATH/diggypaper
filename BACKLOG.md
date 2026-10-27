# Backlog

Mise à jour : 2026-10-27. Commits cités : `git log --oneline`.

## Corrigé le 2026-10-27

- **Timeout inférence paramétrable** (580e992) : `LLM_TIMEOUT_SECONDS` (env, backend + worker, défaut 120 s)
  + clé `inference_timeout` en base (override). Avant : 120 s codé en dur dans
  `OpenAICompatibleService`, incompatible avec le cold start Ollama (~118 s) et l'inférence
  CPU (~15 min/article sur un Xeon E5-2609 v2).
- **Warm-up anti cold start** (580e992) : `warmup()` dans le task inférence — un ping avant la
  boucle charge les poids Ollama dans le budget du timeout, au lieu de le faire payer au 1er
  article (job f19773de : FAILED done=0/22, cold start 117,7 s > timeout 120 s).
- **`fetch_all_pmids` : les plus récents d'abord, même sous le cap `limit`** (c1095ce) :
  les identifiants d'une tranche de bissection étaient insérés dans l'ordre esearch
  (croissant par date) → le cap `limit` écartait les articles les plus récents. Feuille inversée.
  Attention : en deçà de la fenêtre NCBI (un seul esearch), l'ordre est celui de NCBI
  (le test `test_chunking_over_100_pmids` encadre ce chemin — ne pas y inverser).

## Bugs / gaps connus

1. **`DELETE /models/{id}` ne libère pas le disque** — supprime seulement la ligne registre ;
   les poids restent dans Ollama. Attendu : pour un modèle `backend_type=OLLAMA`, appeler
   l'API Ollama `/api/delete` (ou `ollama rm`) avant de retirer la ligne registre.
   (Suppression manuelle faite le 2026-10-27 : qwen3:4b, 2,5 Go libérés.)
2. **Pas de gestion de pipeline multi-utilisateurs sur le LLM local** — seule garde :
   « un job PENDING/RUNNING par projet ». Worker celery `concurrency=3` → plusieurs
   utilisateurs (projets différents) peuvent frapper le même Ollama en parallèle ;
   `num_parallel` Ollama non configuré (défaut 1) → file de serialisation côté serveur,
   **invisible** : le job reste « RUNNING » en attente, consomme le budget du timeout par
   appel, occupe un slot prefork. Voir évolution 2.
3. **Page `/models` : deux voies d'install disjointes** — boutons Install en haut de page,
   recherche Hugging Face en bas (`frontend/src/app/models/page.tsx`, `HfSearch.tsx`).
   Fix minimal : déplacer HfSearch après la carte Services (1 déplacement JSX).

## Évolutions prévues

1. **Bornes date de recherche PubMed** (petit, ~quelques h) — `ImportRequest` n'a que
   `query` (chaîne brute). Ajouter `date_from`/`date_to` optionnelles :
   (a) ANDées dans la requête NCBI, (b) utilisées comme bornes de la bissection
   `walk()` au lieu de `1800-01-01 → today+366` → moins d'appels esearch.
2. **Lock par modèle + file visible** (demi-journée) — lock Redis
   `SET NX` + TTL par identifiant de modèle au démarrage du job ; modèle occupé →
   requeue (retry countdown 30-60 s) + phase `waiting_model` dans `result_json`
   (l'UI affiche « en file »). GPU plus tard : `OLLAMA_NUM_PARALLEL=n` → compteur de
   n slots au lieu du lock. Argument pour le papier : ordonnancement multi-utilisateurs
   équitable sur un LLM local partagé.
3. **Multi-sources (concurrence avec PubMed)** (feature) — architecture actuelle :
   pipeline `search_volume → fetch_all_pmids → fetch_metadata → fetch_fulltext`, article
   identifié par `(project_id, pmid)` (unique partiel). Plan :
   - interface `SourceAdapter` (volume / résultats / métadonnées / plein texte) ;
     PubMed = adapter n°1 ;
   - identity généralisée `(project_id, source, native_id)` + dédoublonnage par DOI ;
   - choix de la source par import (UI) ;
   - plein texte : Europe PMC livre du JATS OA = le format que l'app parse déjà (aucun
     travail) ; HAL = TEI/HTML/PDF → extracteur à prévoir.
   Candidates vérifiées (2026-10-27) :
   - **Europe PMC** (EBI) : REST gratuite sans clé (rate limit par IP), 33 M+ docs
     (superset PubMed : preprints, brevets EPO, NICE), 10,2 M de pleins textes dont
     6,5 M OA, `getFullTextXML` JATS. → adapter n°1 évident.
   - **HAL / CCSD** : `api.archives-ouvertes.fr/search/` (Solr, JSON, `rows` ≤ 10 000,
     pagination par curseur, `fq=submittedDate_tdate:[...]`), plein texte souvent OA,
     thématique française unique (thèses, HDR, rapports INSERM/CNRS) → complément.
   - **OpenAlex** : `api.openalex.org/works`, filtres `from_publication_date`/
     `to_publication_date`, OA status ; pas de plein texte (URLs PDF OA seulement) et
     pricing usage-based depuis fév. 2026 → moindre fit pour l'extraction de sections.

## Contexte E2E / publication

- Seule validation E2E restante : **Ollama local** (Qwen API déjà validée en E2E).
  C'est la section validation du manuscrit (claim RGPD/100 % local).
- Matériel du test : serv-ics-testing, Xeon E5-2609 v2 @ 2,5 GHz (2012, sans AVX2) —
  latences réelles : qwen3:4b > 15 min/article (job bb5ed30a : timeout 900 s, done=0/3) ;
  passage à qwen3:1.7b pour le gate (job 906766d9, 3 articles, seed 42).
- Les latences mesurées (par article, cold start, par modèle) alimentent la section
  « coût CPU » du papier.
- Journals cibles (Couperin) : RSM (0 €, CUP illimité) n°1, SysRevs n°2 (fit exact).
