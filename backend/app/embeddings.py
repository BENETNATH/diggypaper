"""Local embeddings (RAG, spec 2026-11-07-recherche-semantique-rag).

Souverain par construction : le modèle d'embedding est local (CPU) et chargé
**paresseusement** — à la première vectorisation, pas au démarrage de l'API ni
du worker (torch n'est payé qu'une fois, et seulement si on s'en sert).

Le nom de modèle est un paramètre système (``embedding_model``) ; le défaut est
``all-MiniLM-L6-v2`` (384-dim). Un changement de modèle invalide les vecteurs
existants (re-vectorisation, cf. ``app.tasks.vectorize``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Default local embedding model (384-dim, ~22 M params, CPU-friendly).
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

#: The live embedder, or ``None`` until first use. Tests replace this with a
#: fake (no model download in the suite).
_embedder: Embedder | None = None


class Embedder:
    """A named embedder with a fixed dimension (one model = one dimension)."""

    name: str
    dim: int

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def _pick_device() -> str:
    """Fastest available device: CUDA (GPU servers, TORCH_VARIANT=cuda), then
    Apple Silicon MPS (native macOS), then CPU. Inside Linux containers
    (Docker Desktop on Mac) neither GPU backend is visible → CPU."""
    import torch  # noqa: PLC0415 (lazy: torch only where embeddings run)

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available() and torch.backends.mps.is_built():
        return "mps"
    return "cpu"


class _SentenceTransformerEmbedder(Embedder):
    """sentence-transformers wrapper (lazy import: torch stays out of the API
    process until the first vectorization)."""

    def __init__(self, name: str) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415 (lazy)

        self.name = name
        device = _pick_device()
        logger.info("embeddings: device=%s", device)
        self._model = SentenceTransformer(name, device=device)
        # sentence-transformers >= 3.x renamed the method; fall back for older.
        dim_fn = getattr(self._model, "get_embedding_dimension", None)
        self.dim = int((dim_fn or self._model.get_sentence_embedding_dimension)())

    def embed(self, text: str) -> list[float]:
        vec = self._model.encode(text, normalize_embeddings=True)
        return [float(x) for x in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [[float(x) for x in vec] for vec in vecs]


def get_embedder(name: str | None = None) -> Embedder:
    """The active embedder. A requested ``name`` that matches the cached
    embedder (or no name at all) returns it; anything else loads (a model
    change = full re-vectorization upstream). The model loads on first use and
    is then cached."""
    global _embedder
    if _embedder is not None and (name is None or _embedder.name == name):
        return _embedder
    name = name or DEFAULT_EMBEDDING_MODEL
    if _embedder is None:
        logger.info("embeddings: loading %s (first use)", name)
    _embedder = _SentenceTransformerEmbedder(name)
    return _embedder
