"""Validation metrics: model answers against references (gold), agreement between
variants. Pure functions (tested out of the database).

A "decision" = dict ``{item: value, confidence_score: relevance, …}`` (format of
``ArticlePrediction.decision_json`` and ``GoldArticle.ground_truth_json``).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter

RELEVANT_AT = 0.5  # relevance >= 0.5: article "to include"
METRICS = ("accuracy", "recall", "precision", "f1")


def confidence(value) -> float | None:
    """Confidence score of a model output, coerced to a float in [0, 1]; else ``None``.

    Small models sometimes return ``"0.9"``, ``"high"``, ``85`` (0-100 scale) or a
    list: ``bool``, non-numeric types, non-finite or outside [0, 1] → ``None``;
    valid numeric ``int`` / ``float`` / ``str`` → ``float``.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        score = float(value.strip() if isinstance(value, str) else value)
    except (ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0.0 <= score <= 1.0 else None


def _norm(v):
    return re.sub(r"\s+", " ", v.strip().lower()) if isinstance(v, str) else v


def _prf(tp: int, fp: int, fn: int, tn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    n = tp + fp + fn + tn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": p,
        "recall": r,
        "f1": 2 * p * r / (p + r) if p + r else 0.0,
        "accuracy": (tp + tn) / n if n else 0.0,
        "specificity": tn / (tn + fp) if tn + fp else 0.0,
    }


def as_bool(v) -> bool:
    """Boolean of a model output: "true"/"false"/"oui"/"non"/0/1 (bool("false") is True)."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "oui", "1", "vrai")
    return bool(v)


def as_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def relevant(decision: dict | None) -> bool:
    return as_float((decision or {}).get("confidence_score")) >= RELEVANT_AT


def _tokens(text) -> set[str]:
    return set(re.findall(r"\w+", str(text or "").lower()))


def _token_f1(a, b) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    inter = len(ta & tb)
    if not inter:
        return 0.0
    p, r = inter / len(ta), inter / len(tb)
    return 2 * p * r / (p + r)


def score(fields: list[dict], preds: dict, gold: dict, exclude: set | None = None) -> dict:
    """Scores of predictions {article_id: decision} against the gold {article_id: truth}.

    Only articles present on both sides and outside ``exclude`` (prompt examples) count.
    → {n, relevance: {…prf}, items: {key: {…}}, accuracy (average of boolean/choice items)}.
    """
    ids = [i for i in gold if i in preds and i not in (exclude or set())]
    rel = [0, 0, 0, 0]
    invalid = 0
    for i in ids:
        p, g = relevant(preds[i]), relevant(gold[i])
        if not preds[i]:  # invalid output: an error, never an advantageous "not relevant"
            p, invalid = not g, invalid + 1
        rel[0] += p and g
        rel[1] += p and not g
        rel[2] += g and not p
        rel[3] += (not p) and (not g)
    items: dict[str, dict] = {}
    for f in fields:
        k, t = f["key"], f["type"]
        known = [i for i in ids if k in preds[i] and gold[i].get(k) is not None]
        if t == "boolean":
            c = [0, 0, 0, 0]
            for i in known:
                p, g = as_bool(preds[i][k]), as_bool(gold[i][k])
                c[0] += p and g
                c[1] += p and not g
                c[2] += g and not p
                c[3] += (not p) and (not g)
            items[k] = {"type": t, "n": len(known), **_prf(*c)}
        elif t == "choice":
            ok = sum(_norm(preds[i][k]) == _norm(gold[i][k]) for i in known)
            items[k] = {"type": t, "n": len(known), "accuracy": ok / len(known) if known else 0.0}
        else:  # free text: word overlap (F1), not strict equality
            f1 = [_token_f1(preds[i][k], gold[i][k]) for i in known]
            items[k] = {"type": t, "n": len(known), "f1": sum(f1) / len(f1) if f1 else 0.0}
    accs = [v["accuracy"] for v in items.values() if "accuracy" in v and v["n"]]
    return {
        "n": len(ids),
        "invalid": invalid,
        "relevance": _prf(*rel),
        "items": items,
        "accuracy": sum(accs) / len(accs) if accs else None,
    }


def primary_value(scored: dict, fields: list[dict]) -> tuple[str, str, float | None]:
    """(target, metric, value) of the ranking criterion: the item marked ``primary`` with its
    ``metric`` (default recall), else the relevance recall (a triage must miss nothing)."""
    for f in fields:
        if f.get("primary"):
            metric = f.get("metric") if f.get("metric") in METRICS else "recall"
            data = scored["items"].get(f["key"], {})
            return f["key"], metric, data.get(metric, data.get("f1"))
    return "relevance", "recall", scored["relevance"]["recall"]


def _effective_keys(fields: list[dict], decisions: list[dict]) -> list[str]:
    """Non-text schema keys to compare — except when none of the decisions
    answers any of them (free-form answers): then nothing is comparable."""
    keys = [f["key"] for f in fields if f["type"] != "text"]
    if keys and not any(k in d for d in decisions for k in keys):
        return []
    return keys


def agreement(by_variant: dict[str, dict], fields: list[dict]) -> dict:
    """Agreement between variants without reference: for each variant, the fraction of
    (article, item) where it gives the majority answer of the variants.
    by_variant = {name: {article_id: decision}}."""
    names = list(by_variant)
    if len(names) < 2:
        return {}
    articles = set.intersection(*(set(v) for v in by_variant.values()))
    # Relevance is only comparable when at least one decision carries a
    # confidence signal; otherwise an all-empty comparison gives a fake 100%.
    decisions = [d for v in by_variant.values() for d in v.values() if d]
    has_conf = any((d or {}).get("confidence_score") is not None for d in decisions)
    keys = (["relevance"] if has_conf else []) + _effective_keys(fields, decisions)
    if not keys:
        return {}
    hits = Counter()
    total = 0
    for a in articles:
        for k in keys:
            vals = {
                n: (
                    relevant(by_variant[n][a])
                    if k == "relevance"
                    else _norm(by_variant[n][a].get(k))
                )
                for n in names
            }
            top, _ = Counter(vals.values()).most_common(1)[0]
            total += 1
            for n, v in vals.items():
                hits[n] += v == top
    return {n: (hits[n] / total if total else 0.0) for n in names}


def disagrees(
    outputs: list[dict | None],
    fields: list[dict],
    errors: list[str | None] | None = None,
) -> bool:
    """Do the variants differ on this article (relevance or a non-text item)?

    A variant that errored (no decision) while another answered counts as a
    divergence. Free-form answers (no non-text field to compare): the variants
    diverge when the full answer — text under any key, or the structured JSON
    shape — differs."""
    errs = errors or [None] * len(outputs)
    seen = [o for o in outputs if o]
    if len(seen) < 2:
        return len(seen) == 1 and any(e for e in errs)
    first = seen[0]
    keys = _effective_keys(fields, seen)
    if not keys:
        # Free-text answers: no objective agreement to validate by default.
        # Structured fields are required for a meaningful disagreement signal.
        return False
    return any(
        relevant(o) != relevant(first) or any(_norm(o.get(k)) != _norm(first.get(k)) for k in keys)
        for o in seen[1:]
    )


def _free_text(decision: dict) -> str:
    """Comparable text of a free-form decision (no schema field to compare):
    its canonical JSON — any difference in the answer (text or shape) counts."""
    return json.dumps(decision, sort_keys=True, ensure_ascii=False, default=str)
