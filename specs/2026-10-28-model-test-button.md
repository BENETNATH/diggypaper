# Spec — Bouton « Test » sur les modèles installés (page Modèles)

Date : 2026-10-28
Demande : « ajoute des boutons Test devant chaque modèle installé pour confirmer qu'il est opérationnel »

## Objectif

Sur la page Modèles de diggypaper, chaque modèle de la liste « Modèles installés »
reçoit un bouton **Test** qui lance un appel d'inférence minimal (ping) et affiche
le résultat : opérationnel / erreur.

## Comportement

### Backend — `POST /api/v1/models/{model_id}/test`

- Auth : utilisateur connecté.
- Permissions : owner du modèle ou ADMIN (`_require_owner_or_admin`) — un tiers ne
  peut pas brûler la quota/clé d'autrui. Sinon 403.
- Modèle inconnu → 404.
- Rate limit : `model_test`, 10 appels / 15 min / utilisateur (même bucket que
  `/external/test`).
- Appelle le modèle via le service réel de l'instance :
  - `OPENAI_API` → URL + clé chiffrées du modèle (`infer_with` gère le cas) ;
  - autres backends (OLLAMA, …) → service par défaut `make_inference_service(db)`
    (endpoint natif `/api/chat` pour Ollama, timeout/réglages de l'instance).
- Prompt minimal : system `'Reply with the JSON {"ok": true}.'`, user `ping`.
- Réponse : `{"ok": true}` ou `{"ok": false, "error": "<InferenceError assaini>"}`
  (200 dans les deux cas — c'est un état, pas une faute HTTP).
- Pas de mutation, pas d'effets de bord (le modèle reste chargé en RAM : le ping
  le réchauffe, ce qui est un bonus).

### Frontend — page `/models`

- Bouton **Test** en tête du groupe de boutons de chaque ligne de modèle installé
  (avant Partager/Supprimer), pour tout modèle visible.
- Pendant l'appel : bouton désactivé, label « Test… ».
- Résultat affiché sous l'identifiant du modèle :
  - `ok` → « Opérationnel » (vert) ;
  - erreur → « Test échoué : <error> » (rouge).
- Le résultat est conservé à l'écran jusqu'au prochain test (pas de poll).

### i18n (fr/en/de/es)

`testModel`, `testRunning`, `testOk`, `testError`.

## Hors périmètre

- Pas de test automatique au chargement de la page (coût inférence).
- Pas de test sur les modèles détectés non enregistrés (ils ne sont pas encore
  installés côté registre).
- Pas de changement de la section « Modèles externes » (son bouton Test existe).

## Tests (TDD)

- OLLAMA ok : `{"ok": true}`, infer appelé avec l'identifiant du modèle.
- OPENAI_API : l'appel part bien vers l'URL + clé du modèle (mock httpx).
- Erreur : `InferenceError("timeout")` → `{"ok": false, "error": "timeout"}` (200).
- UUID inconnu → 404.
- Modèle d'autrui → 403.
