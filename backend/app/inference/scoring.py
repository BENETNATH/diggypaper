"""Modèles « scoreurs » (non génératifs) : encodeur ONNX local ou serveur de décision."""

from __future__ import annotations

from app.article_text import compose_text, title_abstract
from app.db.models import BackendType
from app.inference.onnx_driver import score_relevance
from app.inference.systemone import decide
from app.schema import DEFAULT_QUESTION, project_fields

DEFAULT_WINDOWS = 4  # ≈ 2 000 jetons lus par article, ~10 s sur CPU avec Laya
SCORERS = (BackendType.ONNX_LOCAL, BackendType.SYSTEMONE)


def is_scorer(model) -> bool:
    return model is not None and model.backend_type in SCORERS


async def score_prediction(model, project, article, *, light: bool) -> dict:
    """Prédiction d'un scoreur. ``light`` = étage 1 : titre + abstract seulement (rapide)."""
    if model.backend_type == BackendType.ONNX_LOCAL:
        p = await score_relevance(model.model_identifier, title_abstract(article))
        return {
            "confidence_score": p,
            "decision": "INCLUDED" if p >= 0.5 else "EXCLUDED",
            "evidence_quotes": [],
            "reasoning_summary": "",
        }
    text = title_abstract(article) if light else compose_text(article, project.text_sections)
    return await decide(
        model.model_identifier,
        text,
        project.question or DEFAULT_QUESTION,
        [] if light else project_fields(project),  # étage 1 : pertinence seule (plus rapide)
        windows=1 if light else (project.read_windows or DEFAULT_WINDOWS),
    )
