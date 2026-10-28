# Spec — Annotation UX : cellules lisibles + scroll évidence → document

## Contexte

Sur la page d'annotation (`/projects/[id]/annotate`), deux défauts rapportés
par l'utilisateur :

1. **Cellules résultats illisibles** : les réponses du modèle (plusieurs
   lignes) étaient rendues dans des `<input>` mono-ligne ou des
   `<textarea>` à hauteur fixe (3 lignes, ~90 px) : le texte était tronqué
   à l'affichage, non lisible en entier.
2. **Pas de navigation évidence → texte** : les citations de preuve sont
   listées dans le formulaire (colonne droite) et surlignées dans le
   document (colonne gauche) ; cliquer sur une citation ne menait pas à son
   endroit dans le texte.

## Comportement attendu

### 1. Cellules de résultats lisibles en entier

- Tout champ de type `text` du schéma de la question (ex. « Décrire le
  protocole d'injection ») est rendu dans un `<textarea>` **auto-ajusté** à
  sa hauteur de contenu (min 2 lignes, max 320 px, puis scroll interne),
  au lieu d'un `<input>` qui tronquait.
- Le champ « Reasoning summary » du formulaire est rendu avec le même
  composant auto-ajusté (au lieu d'un `<textarea rows=3>` fixe).
- Le texte est intégralement visible sans interaction (vérifié : hauteur du
  textarea >= scrollHeight).

### 2. Clic sur une évidence → scroll du document

- Chaque citation de la liste « Evidence quotes » est cliquable (bouton,
  texte intégral, pas de `truncate`).
- Au clic :
  - le document (colonne gauche) scrolle **au centre** de son surlignage
    correspondant ;
  - le surlignage clignote ~2 s (animation `evidence-flash`, 3 pulsations de
    box-shadow) pour repérer l'endroit ;
  - si le même extrait est cliqué de nouveau, le scroll est relancé (nonce).
- Mobile (onglets Document / Review) : le clic bascule automatiquement sur
  l'onglet Document, puis le panneau scrolle sur le surlignage.
- Le scroll est calculé explicitement sur le conteneur du panneau
  (`el.scrollTo`) : `scrollIntoView({block:"center"})` se calculait par
  rapport à la fenêtre en pleine réorganisation du layout (bascule
  d'onglet) et laissait le surlignage bas du panneau au lieu de le centrer.
  On retente au frame suivant tant que le `<mark>` n'a pas de boîte
  (height = 0, panneau encore masqué).
- Si le panneau lui-même est hors du viewport fenêtre (mobile), la fenêtre
  est aussi ajustée (`window.scrollBy`).

## Fichiers

- `frontend/src/components/DecisionFields.tsx` : `AnswerTextarea` (exporté,
  forwardRef + auto-resize), utilisé pour les champs `text`.
- `frontend/src/components/AnnotationForm.tsx` : `Reasoning summary` →
  `AnswerTextarea` ; citations cliquables (`onQuoteClick`), sans `truncate`.
- `frontend/src/components/EvidenceHighlighter.tsx` : prop `focus`
  (`{quote, n}`), effet de scroll centré + flash.
- `frontend/src/app/projects/[id]/annotate/page.tsx` : état `focus`,
  `onQuoteClick` (nonce + bascule d'onglet), reset au changement d'article.
- `frontend/src/app/globals.css` : keyframe `evidence-flash`.
- `frontend/src/messages/{fr,en,de,es}.json` : clé `jumpToQuote`.

## Vérifications

- `npx tsc -p tsconfig.json` → exit 0.
- Desktop 1440 px : clic citation → le mark est centré dans le panneau,
  classe `evidence-flash` présente pendant l'animation, retirée ensuite.
- Mobile 390 px : onglet Review → clic citation → bascule onglet Document +
  mark centré dans le panneau.
- Cellules : hauteur du textarea >= scrollHeight (texte entier visible).
