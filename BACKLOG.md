# Backlog

Mise à jour : 2026-10-30. Commits cités : `git log --oneline`.

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

## Bugs / gaps connus

1. **HAL : fichiers non-PDF (TEI, HTML) non extraits** — shortcut documenté dans
   `backend/app/hal/__init__.py` : la notice reste en résumé seul (comme un article
   sans copie ouverte). Le fichier principal HAL est écrasamment du PDF (vérifié
   2026-10-30) ; extracteur à écrire seulement si un corpus réel en dépend.
2. **Fenêtre de réécriture DNS sortante non fermée** (SSRF) — la vérification
   d'URL et la connexion ne sont pas atomiques ; pour une instance exposée à des
   utilisateurs non fiables, filtrer la sortie des conteneurs (cf. README
   « Réseau sortant »).

## Contexte publication

- Latences mesurées (par article, cold start, par modèle) pour la section
  « coût CPU » du papier : Xeon E5-2609 v2, qwen3:4b > 15 min/article →
  qwen3:1.7b pour le gate (job 906766d9, 3 articles, seed 42).
- Journals cibles (Couperin) : RSM (0 €, CUP illimité) n°1, SysRevs n°2 (fit exact).
