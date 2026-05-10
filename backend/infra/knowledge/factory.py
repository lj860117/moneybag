"""
Knowledge Retriever Factory
=============================
Returns the best available knowledge retriever implementation:
  1. ChromaKnowledgeStore (if chromadb installed) — persistent SQLite
  2. KnowledgeRetriever (fallback) — in-memory TF-IDF/sentence-transformers

Callers use the Protocol interface — no awareness of which backend is active.

Design doc: docs/design/08-knowledge-rag.md §6

Usage::

    from infra.knowledge.factory import get_retriever

    retriever = get_retriever()  # auto-selects best backend
    results = retriever.retrieve("应急金怎么算", top_k=3)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from domain.models.knowledge import KnowledgeArticle, KnowledgeChunk

logger = logging.getLogger(__name__)

# Default persist directory for chromadb
_DEFAULT_CHROMADB_DIR = Path(__file__).parent.parent.parent / "data" / "chromadb"

# Module-level singleton
_retriever_instance: Optional[object] = None


def get_retriever(
    persist_dir: Optional[str] = None,
    force_memory: bool = False,
) -> object:
    """Get or create the knowledge retriever singleton.

    Auto-selects the best available backend:
      1. ChromaKnowledgeStore (chromadb installed + not force_memory)
      2. KnowledgeRetriever (in-memory fallback)

    Both implement KnowledgeRetrieverProtocol — callers are backend-agnostic.

    Args:
        persist_dir: override chromadb persist directory (default: data/chromadb)
        force_memory: if True, always use in-memory backend (useful for tests)

    Returns:
        Object implementing KnowledgeRetrieverProtocol (retrieve/index_article/etc.)
    """
    global _retriever_instance

    if _retriever_instance is not None:
        return _retriever_instance

    if not force_memory:
        # Try ChromaDB first
        try:
            from infra.knowledge.chromadb_store import ChromaKnowledgeStore

            dir_path = persist_dir or str(_DEFAULT_CHROMADB_DIR)
            store = ChromaKnowledgeStore(persist_dir=dir_path)

            if store.is_available:
                logger.info(f"Factory: using ChromaKnowledgeStore (persist={dir_path})")
                _retriever_instance = store
                return _retriever_instance
        except Exception as e:
            logger.warning(f"Factory: ChromaDB init failed ({e}), falling back to memory")

    # Fallback: in-memory retriever
    from infra.knowledge.retriever import KnowledgeRetriever

    logger.info("Factory: using KnowledgeRetriever (in-memory)")
    _retriever_instance = KnowledgeRetriever()
    return _retriever_instance


def reset_retriever() -> None:
    """Reset the singleton (for testing purposes only)."""
    global _retriever_instance
    _retriever_instance = None
