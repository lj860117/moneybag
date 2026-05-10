"""
Sentence-Transformers Embedding Provider
=========================================
Provides dense vector embeddings using sentence-transformers models.
Falls back to TF-IDF if sentence-transformers is not installed.

Model: paraphrase-multilingual-MiniLM-L12-v2 (384 dimensions)
  - Multilingual (supports Chinese + English)
  - Lightweight (~120MB)
  - Good quality for semantic similarity

Design doc: docs/design/08-knowledge-rag.md §3

Usage::

    from infra.knowledge.embedding import get_embedding_fn, EMBEDDING_DIM

    embed = get_embedding_fn()
    vector = embed("应急金需要多少个月")
    assert len(vector) == EMBEDDING_DIM
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Model configuration
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM_ST = 384  # sentence-transformers model output dim
EMBEDDING_DIM_TFIDF = 2000  # TF-IDF fallback dimension (from rag_service.build_vocabulary max_terms)

# Module-level cached model
_model: Optional[object] = None
_use_sentence_transformers: Optional[bool] = None


def _check_sentence_transformers_available() -> bool:
    """Check if sentence-transformers is importable."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _load_model() -> object:
    """Load the sentence-transformers model (lazy, cached)."""
    global _model
    if _model is not None:
        return _model

    from sentence_transformers import SentenceTransformer
    logger.info(f"Loading sentence-transformers model: {MODEL_NAME}")
    _model = SentenceTransformer(MODEL_NAME)
    logger.info(f"Model loaded: {MODEL_NAME} (dim={EMBEDDING_DIM_ST})")
    return _model


def embed_text_st(text: str) -> list[float]:
    """Compute embedding using sentence-transformers.

    Returns a normalized 384-dim vector.
    """
    model = _load_model()
    # encode returns numpy array, convert to list
    vector = model.encode(text, normalize_embeddings=True)  # type: ignore[union-attr]
    return vector.tolist()


def embed_batch_st(texts: list[str]) -> list[list[float]]:
    """Batch embed multiple texts using sentence-transformers.

    More efficient than calling embed_text_st one by one.
    """
    if not texts:
        return []
    model = _load_model()
    vectors = model.encode(texts, normalize_embeddings=True, batch_size=32)  # type: ignore[union-attr]
    return [v.tolist() for v in vectors]


def is_sentence_transformers_available() -> bool:
    """Check if sentence-transformers is available (cached check)."""
    global _use_sentence_transformers
    if _use_sentence_transformers is None:
        _use_sentence_transformers = _check_sentence_transformers_available()
        if _use_sentence_transformers:
            logger.info("sentence-transformers available — using dense embeddings")
        else:
            logger.info("sentence-transformers NOT available — falling back to TF-IDF")
    return _use_sentence_transformers


def get_embedding_fn() -> Callable[[str], list[float]]:
    """Get the best available embedding function.

    Returns sentence-transformers embed if available, else None
    (caller should fall back to TF-IDF).
    """
    if is_sentence_transformers_available():
        return embed_text_st
    # Return None-like signal: caller uses TF-IDF
    raise ImportError("sentence-transformers not installed")


def get_embedding_dim() -> int:
    """Get the embedding dimension for the current backend."""
    if is_sentence_transformers_available():
        return EMBEDDING_DIM_ST
    return EMBEDDING_DIM_TFIDF
