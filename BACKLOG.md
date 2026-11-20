# Backlog

Mise à jour : 2026-11-20. Commits cités : `git log --oneline`.

## Corrigé le 2026-11-19

- **Fenêtre DNS check→connect fermée (SSRF rebinding)** : l'URL est résolue une
  fois, tous les enregistrements doivent être publics, et la connexion est
  **épinglée sur l'IP validée** pour tout l'appel (Host/SNI préservés ; téléchargement
  PDF : chaque hop de redirect re-vérifié et ré-épinglé). Plus besoin de filtrer la
  sortie comme palliatif principal (recommandation README maintenue en défense
  en profondeur). Validé par l'audit sécurité run-4 (harnais empirique : rebind
  reproduit → 0 hit interne, le contrôle sans épinglage réussit).
- **Export « blind » : fuite d'orphelins corrigée** (finding HIGH audit run-4) :
  le filtre `false()` exclut désormais les orphelins (articles sans revue) du
  listing ; avertissement log au lieu du silence (`dd00842`).
- **Gate commits : les skip de vérification n'appliquent plus aux merge commits**
  (finding LOW audit run-4) : la convention est vérifiée sur le corps des commits
  non-merge ; un merge (≥ 2 parents) est ignoré comme avant, un commit normal
  n'échappe plus au préfixe « Merge » (`302e298`, propagé au repo harness `73df5ee`).
- **`is_active=false` = révocation d'exécution** (décision owner sur la NV run-4) :
  un modèle désactivé est refusé sur les 6 surfaces d'exécution (chat, inference,
  benchmarks, searches, query assistant, veille) y compris pour son owner ; les
  jobs déjà en file échouent avec une raison claire au lieu de tourner ; un job
  déjà démarré termine. Les prédictions déjà produites sont conservées (`eb2af94`).
- **Chat SSE : cap total sur la réponse streamée** (NV run-4 « accumulation sans
  borne ») : bornes taille totale (200 k caractères) et durée totale (600 s) dans
  le moteur de streaming ; dépassement → event SSE `error` propre, pas d'accumulation
  illimitée dans le conteneur API (`9a9d05b`).

## Corrigé le 2026-10-30

- **Suite de tests lançable depuis la racine du repo** : `Settings` lit `./.env`
  relatif au CWD → lancé depuis la racine, pytest chargeait le `.env` de production
  (secrets réels, cap, langue) et 12 tests échouaient à tort. `backend/tests/
  conftest.py` se place maintenant dans `backend/` (le CWD documenté du README)
  avant tout import de `app` — aucun test ne dépendait de l'ancien CWD.
- **Phase 4b multi-sources : adapter HAL** (spec 2026-10-30-sources-phase4b-hal.md)
  : recherche Solr (curseur `cursorMark`, les plus récents d'abord via
  `producedDate_tdate desc,docid desc`, bornes date en `fq`), métadonnées
  (`halId_s` en identifiant natif, DOI, rattachement inter-sources par DOI =
  phase 2), plein texte PDF via l'extracteur **existant**
  (`fileMain_s` → `_download_pdf` + `pdf_to_sections`, aucun nouveau parseur) ;
  sans fichier → `fulltext_note="hal_no_file"`. « HAL » sélectionnable en UI.
  E2E vérifié sur la vraie API : 89 803 résultats, itération 20 ids, PDF → 3
  sections (87 k caractères).
- **`GET /projects/{id}` ne renvoyait pas `role_in_project`** (bug 2026-10-30) :
  seul `list_projects` l'ajoutait → `isProjectLead()` faux dans l'UI pour tout
  non-admin. Un LEAD **invité** (ni créateur ni admin) voyait « Seul un chef de
  projet peut créer des recherches » sur la page Recherches. Le champ est
  maintenant dans le détail projet (test : LEAD invité + admin non membre).
  À noter, pas un bug : la page *Administration → Utilisateurs* affiche « Invité »
  pour tout compte `can_create_projects=false` (niveau compte, distinct du rôle
  projet) ; et le bouton « retirer un membre » était légitime — le membre est bien
  LEAD (garde backend `_require_lead`).
- **Vocabulaire des rôles unifié** (4 langues + README) : un terme par concept —
  « Administrateur », « Créateur de projets », « Invité », « Responsable de projet »
  (au lieu de chef de projet / responsable / LEAD), « Annotateur ». « sans créateur »
  (projet orphelin) devient « sans auteur » pour ne plus collisionner avec le droit
  de créer des projets.

## État (2026-10-30)

- **Multi-sources : phases 1–4 terminées** (PubMed, Europe PMC, OpenAlex, HAL ;
  sélecteur de source en UI, dédoublonnage DOI inter-sources, exports conformes).
- **Validation E2E Ollama local : acquise** (2026-10-30, qwen3:1.7b) — la section
  validation du manuscrit (claim RGPD/100 % local) est débloquée.
- Timeout inférence paramétrable, warm-up anti cold start, ordre des PMIDs,
  suppression des poids Ollama, lock par modèle + file visible, bornes date
  d'import : corrigés (2026-10-27 → 29 ; specs `2026-10-2*` et `git log`).
- **Réorganisation corpus/analyses** (specs `2026-11-20-analyses-runs-1/-2`,
  maquette validée 2026-11-20) : en cours — corpus de questions (Search) séparés
  des analyses/runs (InferenceJob + `name` + `definition_hash` figée, écraser,
  supprimer, `?run=` sur les résultats). Run backend lancé le 2026-11-20.

## Bugs / gaps connus

1. **HAL : fichiers non-PDF (TEI, HTML) non extraits** — shortcut documenté dans
   `backend/app/hal/__init__.py` : la notice reste en résumé seul (comme un article
   sans copie ouverte). Le fichier principal HAL est écrasamment du PDF (vérifié
   2026-10-30) ; extracteur à écrire seulement si un corpus réel en dépend.

(2. « Fenêtre de réécriture DNS sortante non fermée » — corrigé le 2026-11-19 :
épinglage de l'IP validée sur tout l'appel, see section ci-dessus.)

## Évolutions prévues

1. **Questions conditionnelles (skip logic)** — analyse de faisabilité
   validée le 2026-11-20, **aucune migration de données** (les champs sont du
   JSONB dans `Search.fields`). Un champ porte `condition: {field, equals}` ;
   le champ référencé doit être défini avant (évaluation transitive dans
   l'ordre des champs, pas de cycle). Décisions arbitrées :
   (1) v1 = égalité sur un seul champ antérieur, booléen/choice uniquement ;
   (2) `primary` (critère d'auto-accept) interdit sur un champ conditionnel ;
   (3) tolérance modèle : une valeur rendue alors que la condition n'est pas
   remplie est conservée ; (4) filtre « non posée » dans les résultats → v2.
   Touchpoints : `check_fields` / `build_system_prompt` / `validate_decision`
   (`backend/app/schema.py`), benchmarks comptant le champ conditionnel que si
   la condition est remplie, `QuestionEditor` (sélecteur de condition),
   relecture masquant le champ quand la condition n'est pas remplie, i18n
   4 langues. Le tableau de résultats rend déjà « — » pour une valeur absente
   (`ResultsTable.tsx`). Séquençage : **après** la réorganisation
   corpus/analyses (réécriture de QuestionEditor) ; spec + maquette
   QuestionEditor à faire ensuite.

## Contexte publication

- Latences mesurées (par article, cold start, par modèle) pour la section
  « coût CPU » du papier : Xeon E5-2609 v2, qwen3:4b > 15 min/article →
  qwen3:1.7b pour le gate (job 906766d9, 3 articles, seed 42).
- Journals cibles (Couperin) : RSM (0 €, CUP illimité) n°1, SysRevs n°2 (fit exact).

## À arbitrer (2026-11-20)
- **Refresh full-text « Relancer la recherche du texte intégral » : membre ou lead ?**
  `search_fulltext` (annotations.py:182) n'exige que le rôle MEMBRE (rate-limit 10),
  comportement existant depuis e521e05 ; l'UI scopée l'expose déjà à tous les membres.
  Audit feature fiche-corpus (2026-11-20) : si lead-only est attendu, ajouter
  `_require_lead_or_admin` dans un commit dédié (pas de régression dans le diff).
