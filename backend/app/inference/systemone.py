"""Modèles de décision (Laya, Kev…) via le protocole ``POST /v1/systemone``.

Un « modèle de décision » (encodeur type BERT, ou Kev) lit un texte et répond à des
questions typées avec des probabilités calibrées, en un seul passage (pas de génération).
``model_identifier`` = ``<url du serveur>#<checkpoint>`` ex. ``http://laya:8000#multilingual``.

Question du projet → question ``noul`` (oui/non) dont la probabilité de « oui » est le
``confidence_score`` (triage > 0,85 accepté, < 0,15 écarté) ; chaque champ du projet
devient une question supplémentaire (booléen → noul, choix → choice ; texte libre ignoré).
"""

from __future__ import annotations

import httpx

from app.inference import InferenceError

_MAX_STATE_CHARS = 48000  # le serveur refuse > 50 000 caractères
_MAX_QUESTIONS = 64
# Le coût d'un passage croît très vite avec la longueur (mesuré sur CPU : ~1 s pour 180 jetons,
# 6 s pour 1 024, 51 s pour 4 096, 150 s pour 8 192). On lit donc le texte par fenêtres courtes.
WINDOW_CHARS = 2000  # ≈ 500 jetons
WINDOW_OVERLAP = 200
_WINDOW_MAX_LEN = 768


def split_identifier(identifier: str) -> tuple[str, str | None]:
    base, _, model = identifier.partition("#")
    return base.rstrip("/"), (model or None)


def split_windows(text: str, max_windows: int) -> list[str]:
    """Fenêtres successives (avec recouvrement) sur le début du texte, au plus ``max_windows``."""
    text = text.strip()
    if len(text) <= WINDOW_CHARS:
        return [text]
    step = WINDOW_CHARS - WINDOW_OVERLAP
    out = [text[i : i + WINDOW_CHARS] for i in range(0, len(text), step)]
    return out[: max(1, max_windows)]


def build_questions(question: str, fields: list[dict]) -> dict:
    qs: dict[str, dict] = {"relevance": {"type": "noul", "instructions": question}}
    for f in fields[: _MAX_QUESTIONS - 1]:
        if f["type"] == "boolean":
            qs[f["key"]] = {"type": "noul", "instructions": f["label"]}
        elif f["type"] == "choice":
            qs[f["key"]] = {
                "type": "choice",
                "instructions": f["label"],
                "criteria": {o: o for o in f["options"]},
            }
    return qs


def parse_answers(result: dict, fields: list[dict]) -> dict:
    """Réponse du serveur → prédiction au format interne (decision_json)."""
    answers = result.get("answers") or {}
    rel = answers.get("relevance") or {}
    try:
        p = float(rel["noul"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InferenceError("invalid_response") from exc
    out: dict = {
        "confidence_score": p,
        "decision": "INCLUDED" if p >= 0.5 else "EXCLUDED",
        "evidence_quotes": [],  # un modèle de décision ne produit pas de citations
        "reasoning_summary": "",
        "model": result.get("model"),
    }
    certainty: dict[str, float] = {}
    for f in fields:
        a = answers.get(f["key"])
        if not isinstance(a, dict):
            continue
        if f["type"] == "boolean" and "noul" in a:
            prob = float(a["noul"])
            out[f["key"]] = prob >= 0.5
            certainty[f["key"]] = _certainty(a, max(prob, 1 - prob))
        elif f["type"] == "choice" and "choice" in a:
            out[f["key"]] = a["choice"]
            certainty[f["key"]] = _certainty(a, (a.get("probabilities") or {}).get(a["choice"]))
    out["field_confidence"] = certainty  # certitude propre à chaque réponse
    return out


def _certainty(answer: dict, fallback) -> float | None:
    """Certitude de la réponse : ``answer_confidence`` du serveur, sinon calculée."""
    for v in (answer.get("answer_confidence"), fallback):
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            continue
    return None


async def _call(client: httpx.AsyncClient, base: str, body: dict, fields: list[dict]) -> dict:
    try:
        resp = await client.post(f"{base}/v1/systemone", json=body)
    except httpx.TimeoutException as exc:
        raise InferenceError("timeout") from exc
    except httpx.HTTPError as exc:
        raise InferenceError("connection_error") from exc
    if resp.status_code >= 400:
        raise InferenceError(f"HTTP {resp.status_code}")
    return parse_answers(resp.json(), fields)


async def decide(
    identifier: str,
    text: str,
    question: str,
    fields: list[dict],
    http: httpx.AsyncClient | None = None,
    timeout: float = 600.0,
    windows: int = 1,
) -> dict:
    """Lit le texte par fenêtres (au plus ``windows``) : P(oui) = maximum sur les fenêtres."""
    base, model = split_identifier(identifier)
    questions = build_questions(question, fields)
    owned = http is None
    client = http or httpx.AsyncClient(timeout=timeout)

    def body(part: str, qs: dict, long: bool) -> dict:
        b: dict = {"state": part, "questions": qs}
        if model:
            b["model"] = model
        if long:
            b["max_len"] = _WINDOW_MAX_LEN
        return b

    try:
        parts = split_windows(text[:_MAX_STATE_CHARS], windows)
        if len(parts) == 1:
            out = await _call(client, base, body(parts[0], questions, False), fields)
            out["windows"] = 1
            return out
        # Le serveur recalcule le texte pour CHAQUE question : on cherche d'abord la meilleure
        # fenêtre avec la seule question de pertinence, puis on pose les questions de détail
        # (champs) sur cette fenêtre uniquement.
        rel_only = {"relevance": questions["relevance"]}
        best_p, best_part = -1.0, parts[0]
        for part in parts:
            out = await _call(client, base, body(part, rel_only, True), [])
            if out["confidence_score"] > best_p:
                best_p, best_part = out["confidence_score"], part
        best = {
            "confidence_score": best_p,
            "decision": "INCLUDED" if best_p >= 0.5 else "EXCLUDED",
            "evidence_quotes": [],
            "reasoning_summary": "",
        }
        if len(questions) > 1:
            detail = await _call(client, base, body(best_part, questions, True), fields)
            best.update(
                {k: v for k, v in detail.items() if k not in ("confidence_score", "decision")}
            )
        best["windows"] = len(parts)
        return best
    finally:
        if owned:
            await client.aclose()
