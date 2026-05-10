"""
Knowledge Retriever Protocol
=============================
Interface for RAG knowledge retrieval (08-knowledge-rag.md §6).

Invariant #11: post-M1, any new cross-module interface needs a Protocol first.

Usage::

    from domain.protocols import KnowledgeRetrieverProtocol

    class MyRetriever:
        def retrieve(self, query: str, top_k: int = 3, category_hint: str = "") -> list[KnowledgeChunk]:
            ...
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.models.knowledge import KnowledgeChunk, KnowledgeArticle


@runtime_checkable
class KnowledgeRetrieverProtocol(Protocol):
    """Protocol for RAG knowledge retrieval.

    Implementations: infra/knowledge/retriever.py

    Design doc: docs/design/08-knowledge-rag.md §6
    """

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str = "",
    ) -> list[KnowledgeChunk]:
        """Retrieve top-k relevant chunks for a given query.

        Args:
            query: natural language query string
            top_k: maximum number of chunks to return (default 3)
            category_hint: optional category filter (ContentCategory value)

        Returns:
            List of KnowledgeChunk, ordered by relevance (most relevant first).
            Empty list if no relevant chunks found (similarity < threshold).
        """
        ...

    def index_article(self, article: KnowledgeArticle, chunks: list[KnowledgeChunk]) -> int:
        """Index an article's chunks into the knowledge base.

        Args:
            article: article metadata
            chunks: pre-chunked text segments with embeddings

        Returns:
            Number of chunks successfully indexed.
        """
        ...

    def list_articles(self) -> list[KnowledgeArticle]:
        """List all indexed articles."""
        ...

    def get_article_chunks(self, article_id: str) -> list[KnowledgeChunk]:
        """Get all chunks for a specific article."""
        ...

    def total_chunks(self) -> int:
        """Total number of indexed chunks."""
        ...
