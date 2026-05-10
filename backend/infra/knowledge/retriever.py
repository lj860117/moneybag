"""
Knowledge Retriever -- in-memory vector store implementation
============================================================
Implements KnowledgeRetrieverProtocol using:
  - In-memory chunk storage
  - sentence-transformers dense embeddings (preferred, 384-dim)
  - TF-IDF sparse embeddings (fallback if sentence-transformers not installed)
  - Cosine similarity search

Design doc: docs/design/08-knowledge-rag.md §6

Embedding strategy:
  1. Try sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)
  2. Fall back to TF-IDF if not available

For production, this could be swapped to ChromaDB or FAISS.
The Protocol interface stays the same.
"""
from __future__ import annotations

import logging

from domain.models.knowledge import KnowledgeArticle, KnowledgeChunk
from domain.services.rag_service import (
    build_vocabulary,
    compute_embedding,
    retrieve_knowledge,
    search_chunks,
    cosine_similarity,
    RetrievalResult,
)

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """In-memory knowledge retriever implementing KnowledgeRetrieverProtocol.

    Stores all chunks in memory with their embeddings.
    Uses sentence-transformers if available, else TF-IDF fallback.

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
        self._use_st: bool = False
        self._st_embed_fn: object = None
        self._st_batch_fn: object = None
        self._detect_embedding_backend()

    def _detect_embedding_backend(self) -> None:
        """Detect if sentence-transformers is available."""
        try:
            from infra.knowledge.embedding import (
                is_sentence_transformers_available,
                embed_text_st,
                embed_batch_st,
            )
            if is_sentence_transformers_available():
                self._use_st = True
                self._st_embed_fn = embed_text_st
                self._st_batch_fn = embed_batch_st
                logger.info("KnowledgeRetriever: using sentence-transformers embeddings")
            else:
                logger.info("KnowledgeRetriever: using TF-IDF fallback")
        except Exception:
            logger.info("KnowledgeRetriever: using TF-IDF fallback")

    @property
    def embedding_backend(self) -> str:
        """Return current embedding backend name."""
        return "sentence-transformers" if self._use_st else "tfidf"

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

        if self._use_st:
            return self._retrieve_st(query, top_k, category_hint)

        result = retrieve_knowledge(
            query=query,
            chunks=self._chunks,
            vocabulary=self._vocabulary,
            top_k=top_k,
            category_hint=category_hint,
        )
        return result.chunks

    def _retrieve_st(
        self, query: str, top_k: int, category_hint: str
    ) -> list[KnowledgeChunk]:
        """Retrieve using sentence-transformers embeddings."""
        query_embedding = self._st_embed_fn(query)  # type: ignore[misc]
        return search_chunks(
            query_embedding=query_embedding,
            chunks=self._chunks,
            top_k=top_k,
            category_hint=category_hint,
            threshold=0.2,  # higher threshold for dense vectors
        )

    def retrieve_with_metadata(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str = "",
    ) -> RetrievalResult:
        """Retrieve with full metadata (total_indexed, etc)."""
        if not self._indexed or not self._chunks:
            return RetrievalResult(query=query, chunks=[], total_indexed=0)

        chunks = self.retrieve(query, top_k, category_hint)
        return RetrievalResult(
            query=query,
            chunks=chunks,
            total_indexed=len(self._chunks),
        )

    def search(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str = "",
        tags: list[str] | None = None,
        source_grade: str = "",
    ) -> list[KnowledgeChunk]:
        """Enhanced search with tag and grade filters.

        This is the method backing /api/rag/search endpoint.
        """
        # Get base results (more than needed for filtering)
        candidates = self.retrieve(query, top_k=top_k * 3, category_hint=category_hint)

        # Apply filters
        if tags:
            candidates = [
                c for c in candidates
                if any(t in c.metadata.get("tags", []) for t in tags)
            ]

        if source_grade:
            candidates = [
                c for c in candidates
                if c.source_grade.value == source_grade
            ]

        return candidates[:top_k]

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

        if self._use_st:
            self._rebuild_index_st()
        else:
            self._rebuild_index_tfidf()

    def _rebuild_index_st(self) -> None:
        """Rebuild index using sentence-transformers batch encoding."""
        texts = [chunk.content for chunk in self._chunks]
        embeddings = self._st_batch_fn(texts)  # type: ignore[misc]

        updated_chunks: list[KnowledgeChunk] = []
        for chunk, embedding in zip(self._chunks, embeddings):
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
        logger.info(f"Index rebuilt (sentence-transformers): {len(self._chunks)} chunks")

    def _rebuild_index_tfidf(self) -> None:
        """Rebuild index using TF-IDF (fallback)."""
        # Build vocabulary from all chunks
        self._vocabulary = build_vocabulary(self._chunks)

        # Recompute embeddings for all chunks
        updated_chunks: list[KnowledgeChunk] = []
        for chunk in self._chunks:
            embedding = compute_embedding(chunk.content, self._vocabulary)
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
        logger.info(f"Index rebuilt (TF-IDF): {len(self._chunks)} chunks, vocab={len(self._vocabulary)}")
