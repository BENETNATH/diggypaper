"""Moteur B : encodeur ONNX in-process (Laya-400M) — score de pertinence par article.

Convention de fichiers : ``model_identifier`` pointe le ``.onnx`` (relatif à
``MODELS_DIR``) ; le tokenizer est ``<même nom>.tokenizer.json`` ou, à défaut,
``tokenizer.json`` dans le même dossier. Sortie du modèle : 1 logit (sigmoïde) ou
logits de classes (softmax, classe 1 = pertinent). Threads CPU : ``OMP_NUM_THREADS``.
"""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path

from app.core.config import get_settings
from app.inference import InferenceError

_MAX_TOKENS = 512
# identifier -> (session, tokenizer, noms des entrées). Un chargement par process worker.
_SESSIONS: dict[str, tuple] = {}


def _resolve(identifier: str) -> Path:
    p = Path(identifier)
    if p.is_absolute():
        return p
    base = Path(get_settings().models_dir)
    # Le seed utilise « models/<fichier> » : ne pas doubler le dossier.
    return base / (p.relative_to("models") if p.parts[:1] == ("models",) else p)


def _load(identifier: str) -> tuple:
    if identifier in _SESSIONS:
        return _SESSIONS[identifier]
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise InferenceError("onnx_unavailable") from exc
    path = _resolve(identifier)
    tok_path = path.with_suffix(".tokenizer.json")
    if not tok_path.exists():
        tok_path = path.parent / "tokenizer.json"
    if not path.exists() or not tok_path.exists():
        raise InferenceError("model_file_missing")
    opts = ort.SessionOptions()
    threads = int(os.environ.get("OMP_NUM_THREADS") or 0)
    if threads:
        opts.intra_op_num_threads = threads
    try:
        session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
    except Exception as exc:  # fichier incomplet (poids externes manquants), format invalide…
        raise InferenceError(f"onnx_load_failed: {str(exc)[:160]}") from exc
    tokenizer = Tokenizer.from_file(str(tok_path))
    tokenizer.enable_truncation(max_length=_MAX_TOKENS)
    _SESSIONS[identifier] = (session, tokenizer, [i.name for i in session.get_inputs()])
    return _SESSIONS[identifier]


def _score(identifier: str, text: str) -> float:
    import numpy as np

    session, tokenizer, input_names = _load(identifier)
    enc = tokenizer.encode(text)
    feeds = {
        "input_ids": np.array([enc.ids], dtype=np.int64),
        "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
        "token_type_ids": np.zeros((1, len(enc.ids)), dtype=np.int64),
    }
    out = np.asarray(session.run(None, {k: v for k, v in feeds.items() if k in input_names})[0])
    logits = out.reshape(-1)
    if logits.size == 1:
        return 1 / (1 + math.exp(-float(logits[0])))
    e = np.exp(logits - logits.max())
    return float((e / e.sum())[-1])


async def score_relevance(identifier: str, text: str) -> float:
    """Probabilité de pertinence [0, 1] (calcul CPU hors boucle asyncio)."""
    return await asyncio.to_thread(_score, identifier, text)


def unload(identifier: str | None = None) -> None:
    """Libère la session ONNX (RAM) — utilisé entre deux modèles du benchmark."""
    if identifier is None:
        _SESSIONS.clear()
    else:
        _SESSIONS.pop(identifier, None)
