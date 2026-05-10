"""
Knowledge Retriever -- in-memory vector store implementation
============================================================
Implements KnowledgeRetrieverProtocol using:
  - In-memory chunk storage
  - TF-IDF embeddings (from domain/services/rag_service.py)
  - Cosine similarity search

Design doc: docs/design/08-knowledge-rag.md §6

For production, this could be swapped to ChromaDB or FAISS.
The Protocol interface stays the same.
"""
from __future__ import annotations

from domain.models.knowledge import KnowledgeArticle, KnowledgeChunk
from domain.services.rag_service import (
    build_vocabulary,
    compute_embedding,
    retrieve_knowledge,
    search_chunks,
    RetrievalResult,
)


class KnowledgeRetriever:
    """In-memory knowledge retriever implementing KnowledgeRetrieverProtocol.

    Stores all chunks in memory with their embeddings.
    Rebuilds vocabulary and embeddings when articles are indexed.

    Usage::

        retriever = KnowledgeRetriever()
        retriever.index_article(article, chunks)
        results = retriever.retrieve("应急金怎么算", top_k=3)
    """

    def __init__(self) -> None:
        self._articles: dict[str, KnowledgeArticle] = {}
        self._chunks: list[KnowledgeChunk] = []
        self._vocabulary: list[str] = []
        self._indexed: bool = False

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str = "",
    ) -> list[KnowledgeChunk]:
        """Retrieve top-k relevant chunks for a given query.

        If index is not built yet, returns empty list.
        """
        if not self._indexed or not self._chunks:
            return []

        result = retrieve_knowledge(
            query=query,
            chunks=self._chunks,
            vocabulary=self._vocabulary,
            top_k=top_k,
            category_hint=category_hint,
        )
        return result.chunks

    def retrieve_with_metadata(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str = "",
    ) -> RetrievalResult:
        """Retrieve with full metadata (total_indexed, etc)."""
        if not self._indexed or not self._chunks:
            return RetrievalResult(query=query, chunks=[], total_indexed=0)

        return retrieve_knowledge(
            query=query,
            chunks=self._chunks,
            vocabulary=self._vocabulary,
            top_k=top_k,
            category_hint=category_hint,
        )

    def index_article(
        self, article: KnowledgeArticle, chunks: list[KnowledgeChunk]
    ) -> int:
        """Index an article's chunks into the knowledge base.

        After indexing, rebuilds vocabulary and recomputes all embeddings.
        Returns number of chunks indexed.
        """
        self._articles[article.article_id] = article

        # Remove old chunks for this article (re-index scenario)
        self._chunks = [
            c for c in self._chunks if c.article_id != article.article_id
        ]

        # Add new chunks (without embeddings for now)
        self._chunks.extend(chunks)

        # Rebuild vocabulary and embeddings
        self._rebuild_index()

        return len(chunks)

    def bulk_index(
        self, articles_and_chunks: list[tuple[KnowledgeArticle, list[KnowledgeChunk]]]
    ) -> int:
        """Bulk index multiple articles at once (more efficient than one-by-one).

        Rebuilds vocabulary only once at the end.
        """
        total = 0
        for article, chunks in articles_and_chunks:
            self._articles[article.article_id] = article
            # Remove old chunks for this article
            self._chunks = [
                c for c in self._chunks if c.article_id != article.article_id
            ]
            self._chunks.extend(chunks)
            total += len(chunks)

        # Rebuild once
        self._rebuild_index()
        return total

    def list_articles(self) -> list[KnowledgeArticle]:
        """List all indexed articles."""
        return list(self._articles.values())

    def get_article_chunks(self, article_id: str) -> list[KnowledgeChunk]:
        """Get all chunks for a specific article."""
        return [c for c in self._chunks if c.article_id == article_id]

    def total_chunks(self) -> int:
        """Total number of indexed chunks."""
        return len(self._chunks)

    def _rebuild_index(self) -> None:
        """Rebuild vocabulary and recompute all embeddings."""
        if not self._chunks:
            self._vocabulary = []
            self._indexed = False
            return

        # Build vocabulary from all chunks
        self._vocabulary = build_vocabulary(self._chunks)

        # Recompute embeddings for all chunks
        updated_chunks: list[KnowledgeChunk] = []
        for chunk in self._chunks:
            embedding = compute_embedding(chunk.content, self._vocabulary)
            # Create new frozen dataclass with embedding
            updated = KnowledgeChunk(
                chunk_id=chunk.chunk_id,
                article_id=chunk.article_id,
                content=chunk.content,
                title=chunk.title,
                category=chunk.category,
                source_tag=chunk.source_tag,
                source_grade=chunk.source_grade,
                embedding=embedding,
                chunk_index=chunk.chunk_index,
                metadata=chunk.metadata,
            )
            updated_chunks.append(updated)

        self._chunks = updated_chunks
        self._indexed = True
