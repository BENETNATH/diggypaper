# Spec — Page Modèles : réorganisation (liste d'abord, ajout en bas)

- **Date :** 2026-10-28
- **Repo :** diggypaper (Next.js)
- **Périmètre :** `frontend/src/app/models/page.tsx`, `frontend/src/messages/{fr,en,de,es}.json`
- **Statut :** implémentée (run direct, pas d'opencode — réorganisation de layout sans logique)

## 1. Contexte — demande produit

La page « Modèles » était incohérente : les boutons Installer (modèles
recommandés) en haut, la recherche Hugging Face en bas, et la liste des
modèles installés au milieu.

Demande utilisateur : **d'abord la liste des modèles installés, puis une
partie « ajout » avec deux sections — modèles internes (Ollama : recherche +
recommandations) et modèles externes (API)**.

## 2. Nouvel ordre des blocs

1. En-tête (titre + intro) — inchangé
2. ErrorBanner — inchangé
3. Card « Téléchargements en cours » (conditionnel) — inchangé, reste en haut
   (le message `started` renvoie vers le « haut de page »)
4. **Card « Modèles installés »** — remontée en premier (contenu inchangé :
   liste du registre + badge `offBadge` + modèles détectés Ollama + boutons
   Partager/Supprimer/Utiliser)
5. **Card « Ajouter un modèle »** avec deux sections :
   - **Interne — Ollama** (affiché si `caps` chargé) :
     - ligne de statut Ollama (badge actif/injoignable/non déployé + hint
       `svcHow` / `svcHowOff`)
     - bloc matériel + recommandations (bouton Installer : `isAdmin &&
       caps.ollama.reachable && m.fits`)
     - `HfSearch` (recherche Hugging Face) : gating inchangé
       `isAdmin && caps.ollama.configured`
   - **Externe — API** (toujours affiché) :
     - ligne de statut externe
     - encadré descriptif (génératif) + formulaire modèle externe — contenu
       inchangé

Gating de visibilité identique à l'état précédent : un non-admin voit la
liste, les recommandations (sans bouton Installer), et le formulaire
externe.

## 3. i18n

- Nouveaux : `addInternal`, `addExternal` (4 langues)
- `searchTitle` : préfixe « B. » retiré (la structure A/B n'existe plus)
- `emptyHint` : « ci-dessus » → « ci-dessous » (les recommandations sont
  désormais sous la liste)
- `servicesTitle` supprimé (plus utilisé)
- `intro` inchangé (« Ajoutez-en en bas de page » devient vrai)

## 4. Vérification

- `tsc --noEmit` : zéro erreur
- Suite pytest backend : inchangée (aucun code backend modifié) — 516 passés
- Vue navigateur : liste en haut, ajout en bas avec les deux sections
