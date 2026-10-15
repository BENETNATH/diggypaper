"""Exemples du gold dans le prompt (few-shot) : « éduquer » le modèle avec des cas vérifiés.

Les articles servant d'exemples sont exclus de la validation (on ne note pas un modèle sur ce qu'on
lui a montré).
"""

from __future__ import annotations

import json
import random

from app.article_text import compose_text
from app.metrics import relevant

MAX_EXAMPLE_CHARS = 1200  # texte de l'article, par exemple
HEADER = (
    "Here are examples of articles with the verified answers. Answer for the new article in the "
    "same way, with the same JSON format.\n"
)


def pick_examples(gold: dict, k: int, seed: int = 0) -> list:
    """k ids d'articles du gold {article_id: vérité}, équilibrés entre pertinents et non pertinents
    quand c'est possible, choix reproductible."""
    if k <= 0 or not gold:
        return []
    rng = random.Random(seed)
    pos = sorted(i for i in gold if relevant(gold[i]))
    neg = sorted(i for i in gold if not relevant(gold[i]))
    rng.shuffle(pos)
    rng.shuffle(neg)
    out: list = []
    while len(out) < min(k, len(gold)) and (pos or neg):
        for pool in (pos, neg):
            if pool and len(out) < k:
                out.append(pool.pop())
    return out


def examples_block(articles: dict, gold: dict, ids: list, fields: list[dict], kinds=None) -> str:
    """Bloc de texte à ajouter au prompt système : un exemple par article (texte + réponse)."""
    parts = []
    for i in ids:
        a, g = articles.get(i), gold.get(i)
        if a is None or g is None:
            continue
        answer = {f["key"]: g.get(f["key"]) for f in fields}
        answer["confidence_score"] = g.get("confidence_score", 1.0)
        answer["evidence_quotes"] = g.get("evidence_quotes") or []
        text = compose_text(a, kinds)[:MAX_EXAMPLE_CHARS]
        parts.append(f"Article text:\n{text}\nAnswer:\n{json.dumps(answer, ensure_ascii=False)}")
    return "" if not parts else HEADER + "\n\n".join(parts)
