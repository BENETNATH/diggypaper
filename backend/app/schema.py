"""Schéma de réponse par projet : la question posée aux modèles + ses champs.

``Project.fields`` NULL = schéma historique (``DEFAULT_FIELDS`` + clés standard de
la spec §5). Sinon : liste de champs ``{key, label, type, options?}`` définie par
le chef de projet. Types : ``boolean``, ``choice`` (options), ``text``.
Les clés standard ``confidence_score``, ``evidence_quotes``, ``reasoning_summary``
sont toujours demandées au modèle (triage + surlignage).
"""

from __future__ import annotations

import re

TYPES = ("boolean", "choice", "text")
STANDARD_KEYS = ("confidence_score", "field_confidence", "evidence_quotes", "reasoning_summary")

DEFAULT_QUESTION = "Does the study use AI-based behavior analysis, and which animal sex is studied?"
DEFAULT_FIELDS = [
    {"key": "use_ai_behavior", "label": "Uses AI behavior analysis", "type": "boolean"},
    {
        "key": "animal_sex",
        "label": "Animal sex",
        "type": "choice",
        "options": ["MALE_ONLY", "FEMALE_ONLY", "BOTH_SEXES", "UNSPECIFIED"],
    },
]

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def project_fields(project) -> list[dict]:
    return project.fields or DEFAULT_FIELDS


def check_fields(fields: list[dict]) -> list[dict]:
    """Valide la définition des champs (PATCH projet) ; lève ValueError."""
    if not fields:
        raise ValueError("at least one field is required")
    seen: set[str] = set()
    out = []
    for f in fields:
        key = str(f.get("key", ""))
        if not _KEY_RE.match(key) or key in STANDARD_KEYS:
            raise ValueError(f"invalid field key: {key!r}")
        if key in seen:
            raise ValueError(f"duplicate field key: {key}")
        seen.add(key)
        ftype = f.get("type")
        if ftype not in TYPES:
            raise ValueError(f"field {key}: type must be one of {TYPES}")
        item = {"key": key, "label": str(f.get("label") or key)[:200], "type": ftype}
        if ftype == "choice":
            opts = [str(o).strip() for o in f.get("options") or [] if str(o).strip()]
            if len(opts) < 2:
                raise ValueError(f"field {key}: at least 2 options required")
            item["options"] = list(dict.fromkeys(opts))
        out.append(item)
    return out


def validate_decision(fields: list[dict], data: dict) -> list[str]:
    """Erreurs de validation d'une décision (gold / annotation) selon les champs."""
    errors = []
    for f in fields:
        v = data.get(f["key"])
        if v is None:
            errors.append(f"missing: {f['key']}")
        elif f["type"] == "boolean" and not isinstance(v, bool):
            errors.append(f"{f['key']}: boolean expected")
        elif f["type"] == "choice" and v not in f["options"]:
            errors.append(f"{f['key']}: one of {f['options']}")
        elif f["type"] == "text" and not isinstance(v, str):
            errors.append(f"{f['key']}: text expected")
    c = data.get("confidence_score")
    if c is not None and not (isinstance(c, (int, float)) and 0 <= c <= 1):
        errors.append("confidence_score: number in [0, 1]")
    fc = data.get("field_confidence")
    if fc is not None and not isinstance(fc, dict):
        errors.append("field_confidence: object")
    return errors


def build_system_prompt(question: str | None, fields: list[dict]) -> str:
    """System prompt généré : question + schéma JSON exact demandé au modèle."""
    lines = []
    for f in fields:
        if f["type"] == "boolean":
            t = "boolean"
        elif f["type"] == "choice":
            t = " | ".join(f'"{o}"' for o in f["options"])
        else:
            t = "string"
        lines.append(f'  "{f["key"]}": {t},  // {f["label"]}')
    schema = "\n".join(
        [
            "{",
            *lines,
            '  "confidence_score": float (0.0 to 1.0, probability that the article is relevant '
            "to the question),",
            '  "field_confidence": { one float (0.0 to 1.0) per answer above, keyed by the '
            "answer's field name: how certain you are of THAT answer },",
            '  "evidence_quotes": [ string (exact verbatim quotes from the text) ],',
            '  "reasoning_summary": string',
            "}",
        ]
    )
    return (
        "You are a scientific literature screening assistant. Read the article text "
        "(in EN, FR, DE or ES) and answer this question:\n"
        f"{(question or DEFAULT_QUESTION).strip()}\n\n"
        f"Return ONLY a valid JSON object matching this schema:\n{schema}\n"
        "No markdown wrappers, no text outside the JSON. If the text does not allow "
        "an answer, lower confidence_score."
    )


def compute_metrics(fields: list[dict], prediction: dict, truth: dict) -> dict:
    """Accuracy = fraction des champs exacts ; P/R/F1 sur evidence_quotes (comme avant)."""
    keys = [f["key"] for f in fields]
    exact = sum(1 for k in keys if _norm(prediction.get(k)) == _norm(truth.get(k)))
    accuracy = exact / len(keys)
    pred_q = set(prediction.get("evidence_quotes") or [])
    gold_q = set(truth.get("evidence_quotes") or [])
    if not gold_q:
        precision = recall = f1 = 1.0
    else:
        inter = pred_q & gold_q
        precision = len(inter) / len(pred_q) if pred_q else 0.0
        recall = len(inter) / len(gold_q)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "accuracy": accuracy,
        "precision_score": precision,
        "recall_score": recall,
        "f1_score": f1,
    }


def _norm(v):
    return v.strip().lower() if isinstance(v, str) else v
