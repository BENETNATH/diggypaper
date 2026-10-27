# Page `/models` : déplacer la recherche Hugging Face après la carte « Services »

Date : 2026-10-27 — spec d'implémentation (déplacement JSX, aucun changement de
comportement).

## Contexte

La page `/models` présente deux voies d'installation de modèles Ollama disjointes
(BACKLOG.md:33–35) : les boutons « Install » en haut (modèles recommandés dans la
carte « Services », `pullOllama`) et la recherche Hugging Face (`HfSearch.tsx`)
en bas de page, enterrée sous le formulaire de modèle externe. Fix minimal :
placer le bloc de recherche Hugging Face juste après la carte « Services »
(1 déplacement JSX) pour regrouper les deux voies d'installation.

## État actuel

- Page : `frontend/src/app/models/page.tsx` (composant client `ModelsContent`,
  lignes 30–276 ; `ModelsPage` l'emballe dans `RequireAuth`, l.278–284).
- Carte « Services » : conditionnée par `{caps && (…)}` (l.129–172) ; contient
  l'état des services, le matériel et la liste des modèles recommandés avec
  leur bouton « Install » (`pullOllama(m.tag)`, l.161–165, visible si
  `isAdmin && caps.ollama.reachable && m.fits`).
- Bloc HfSearch actuel : à l'intérieur de la carte
  `<Card title={t("addTitle")}>` (l.234–273), en bas, sous le formulaire de
  modèle externe :

  ```tsx
  {isAdmin && caps?.ollama.configured && (
  <div className="mt-6 border-t border-border pt-4">
    <HfSearch onStarted={() => void load()} onError={setError} />
  </div>
  )}
  ```

  (l.268–272). Condition : `isAdmin && caps?.ollama.configured` — affiché
  seulement pour un admin **et** une fois les capabilities chargées et Ollama
  configuré (pas simplement joignable).
- `HfSearch` (`frontend/src/components/HfSearch.tsx:13–187`) : recherche HF →
  choix de variante → installation via `installHf` ou `pullOllama` (nom
  direct) ; callbacks `onStarted` (→ `load()`, l.82–83 du composant,
  prop passée l.270 de la page) et `onError` (→ `setError`). Importé
  frontend/src/app/models/page.tsx:22.
- Ordre des blocs aujourd'hui : header (l.103–106) → `ErrorBanner` (l.108) →
  carte téléchargements, conditionnée (l.110–127) → carte « Services »,
  conditionnée (l.129–172) → carte « Modèles installés » (l.174–232) → carte
  « addTitle » avec le formulaire externe + HfSearch en bas (l.234–273).
- Callbacks utilisés par le bloc : `load` (l.42–57) et `setError` (l.37) —
  définis en haut du composant, disponibles à tout endroit du JSX.

## Spécification

Un **déplacement seul** dans `frontend/src/app/models/page.tsx`, pas de
changement de comportement, de props, de state ni de logique :

1. **Extraire** le bloc conditionnel exact (l.268–272) de la carte
   `<Card title={t("addTitle")}>`. La carte conserve alors uniquement le
   bandeau génératif (l.235–238) et le formulaire de modèle externe
   (l.240–266) ; sa fermeture `</Card>` (l.273) reste en place.
2. **Insérer** ce même bloc, **sans modification** (condition
   `isAdmin && caps?.ollama.configured`, wrapper `<div
   className="mt-6 border-t border-border pt-4">`, props
   `onStarted={() => void load()} onError={setError}`), juste après la
   fermeture de la carte « Services » (après le `)}` de l.172) et avant la
   carte « Modèles installés » (l.174).
3. Ordre de la page après le déplacement : header → `ErrorBanner` → carte
   téléchargements → carte « Services » → **bloc HfSearch** → carte « Modèles
   installés » → carte « addTitle » (bandeau + formulaire externe).
4. Le composant `HfSearch` lui-même (frontend/src/components/HfSearch.tsx) est
   **inchangé** ; l'import (page.tsx:22) reste ; les callbacks `load`/`setError`
   restent valides (mêmes définitions, même portée).
5. Cas limites de l'affichage, invariants après le déplacement :
   - non-admin : bloc absent (condition `isAdmin`) — comme aujourd'hui ;
   - `caps` non chargé (null) : bloc absent — comme aujourd'hui ;
   - `caps.ollama.configured` faux (Ollama non configuré, ex. déploiement sans
     profil `ollama`) : bloc absent — comme aujourd'hui ;
   - le bloc n'est affiché qu'une seule fois (il a été coupé de la carte
     « addTitle », pas dupliqué).
6. Aucune autre ligne de la page ni aucun autre composant n'est modifié.

## Tests à écrire (TDD)

**Pas de tests frontend à ajouter ni à modifier.** Vérification effectuée :

- `frontend/src/**` ne contient **aucun** fichier de test
  (`*.test.*` / `*.spec.*` : zéro occurrence hors `node_modules`).
- Les dépendances jest sont installées dans `package.json` devDependencies
  (`jest`, `ts-jest`, `jest-environment-jsdom`, `@testing-library/react`,
  `@testing-library/jest-dom` — frontend/package.json:20–22, 28–29, 32) mais il
  n'y a **ni script `test`** (scripts : `dev`, `build`, `start`, `lint` —
  l.6–11) ni configuration de test ni test utilisant ces dépendances.

Validation du déplacement : **vérification visuelle** (serveur dev ou build) :

1. En admin, avec Ollama configuré et reachable : le bloc de recherche HF
   s'affiche juste sous la carte « Services », au-dessus de la carte « Modèles
   installés » ; les boutons « Install » des modèles recommandés restent dans
   la carte « Services » ; le formulaire de modèle externe reste seul dans la
   carte « addTitle ».
2. Non-admin : aucun bloc de recherche HF nulle part sur la page.
3. Recherche → installation via HF : le flux complet (recherche, choix de
   variante, installation, message « ✓ démarré », rafraîchi de la liste via
   `onStarted`) fonctionne comme avant ; un échec d'appel HF remonte dans
   l'`ErrorBanner` existant.
4. Contrôle mécanique : `npm run lint` (eslint) et `npm run build` passent.

## Critères d'acceptation

- [ ] Le bloc conditionnel HfSearch (condition `isAdmin &&
      caps?.ollama.configured` + wrapper + props intactes) est coupé de la
      carte « addTitle » et collé juste après la carte « Services »
- [ ] La carte « addTitle » ne contient plus que le bandeau génératif et le
      formulaire de modèle externe
- [ ] `HfSearch.tsx` et l'import de la page sont inchangés
- [ ] Aucun autre changement dans `page.tsx` (pas de reformatage, pas de
      réorganisation des state/callbacks)
- [ ] L'affichage est identique selon les 4 cas limites (§ Spécification 5) ;
      un seul bloc HfSearch affiché
- [ ] `npm run lint` et `npm run build` passent ; vérification visuelle du §
      Tests effectuée

## Contraintes

- Un seul fichier modifié : `frontend/src/app/models/page.tsx` (1 bloc
  déplacé, zéro ligne de logique ajoutée ou supprimée — le nombre total de
  lignes JSX reste identique).
- Le wrapper `<div className="mt-6 border-t border-border pt-4">` est
  conservé tel quel : dans ce déplacement minimal il n'est plus un séparateur
  de carte mais un simple conteneur ; le bord supérieur visible est accepté
  (ponytail : ne pas rouvrir le design pour un repositionnement). Si une
  réclamation visuelle surgit, c'est un suivi à part.
- Pas de nouveau composant, pas de props modifiées sur `HfSearch`, pas de
  changement de libellés i18n (clés `searchTitle`/`searchHint` inchangées),
  pas de migration de test (pas de tests frontend existants à préserver —
  § Tests).
