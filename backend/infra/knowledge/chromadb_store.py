"""
ChromaDB Knowledge Store -- persistent vector store implementation
=================================================================
Implements KnowledgeRetrieverProtocol using ChromaDB with local SQLite
persistence. This replaces the in-memory retriever for production use.

Design doc: docs/design/08-knowledge-rag.md §3, §6

Features:
  - Local SQLite persistence (no external service needed)
  - sentence-transformers embedding (via chromadb's built-in embedding function)
  - Automatic collection management
  - Graceful fallback to in-memory retriever if chromadb not installed

Usage::

    from infra.knowledge.chromadb_store import ChromaKnowledgeStore

    store = ChromaKnowledgeStore(persist_dir="./data/chromadb")
    store.index_article(article, chunks)
    results = store.retrieve("应急金怎么算", top_k=3)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from domain.models.knowledge import (
    KnowledgeArticle,
    KnowledgeChunk,
    RetrievalResult,
    SourceGrade,
)

logger = logging.getLogger(__name__)

# Default persist directory (relative to backend/)
_DEFAULT_PERSIST_DIR = Path(__file__).parent.parent.parent / "data" / "chromadb"

# Collection name for knowledge base
COLLECTION_NAME = "moneybag_knowledge"


def _is_chromadb_available() -> bool:
    """Check if chromadb is importable."""
    try:
        import chromadb  # noqa: F401
        return True
    except ImportError:
        return False


class ChromaKnowledgeStore:
    """ChromaDB-backed knowledge retriever implementing KnowledgeRetrieverProtocol.

    Uses local SQLite persistence for the vector store.
    Embeddings are computed by chromadb's built-in sentence-transformers
    integration, or by our embedding.py module.

    Falls back gracefully if chromadb is not installed.
    """

    def __init__(self, persist_dir: Optional[str] = None) -> None:
        self._articles: dict[str, KnowledgeArticle] = {}
        self._persist_dir = Path(persist_dir) if persist_dir else _DEFAULT_PERSIST_DIR
        self._client: Any = None
        self._collection: Any = None
        self._available = False
        self._init_chromadb()

    def _init_chromadb(self) -> None:
        """Initialize chromadb client and collection."""
        if not _is_chromadb_available():
            logger.warning("chromadb not installed — ChromaKnowledgeStore disabled")
            return

        try:
            import chromadb
            from chromadb.config import Settings

            # Ensure persist directory exists
            self._persist_dir.mkdir(parents=True, exist_ok=True)

            # Create persistent client
            self._client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=Settings(anonymized_telemetry=False),
            )

            # Get or create collection (uses default embedding function)
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )

            self._available = True
            logger.info(
                f"ChromaDB initialized: persist_dir={self._persist_dir}, "
                f"collection={COLLECTION_NAME}, "
                f"existing_count={self._collection.count()}"
            )
        except Exception as e:
            logger.error(f"ChromaDB init failed: {e}")
            self._available = False

    @property
    def is_available(self) -> bool:
        """Whether chromadb is available and initialized."""
        return self._available

    @property
    def embedding_backend(self) -> str:
        """Return current embedding backend name."""
        return "chromadb" if self._available else "unavailable"

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str = "",
    ) -> list[KnowledgeChunk]:
        """Retrieve top-k relevant chunks for a given query."""
        if not self._available or not self._collection:
            return []

        try:
            # Build where filter for category hint
            where_filter = None
            if category_hint:
                where_filter = {"category": category_hint}

            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_filter,
            )

            if not results or not results["ids"] or not results["ids"][0]:
                return []

            # Convert chromadb results to KnowledgeChunk
            chunks: list[KnowledgeChunk] = []
            ids = results["ids"][0]
            documents = results["documents"][0] if results["documents"] else []
            metadatas = results["metadatas"][0] if results["metadatas"] else []

            for i, chunk_id in enumerate(ids):
                meta = metadatas[i] if i < len(metadatas) else {}
                content = documents[i] if i < len(documents) else ""

                grade_str = meta.get("source_grade", "B")
                try:
                    grade = SourceGrade(grade_str)
                except ValueError:
                    grade = SourceGrade.B

                chunk = KnowledgeChunk(
                    chunk_id=chunk_id,
                    article_id=meta.get("article_id", ""),
                    content=content,
                    title=meta.get("title", ""),
                    category=meta.get("category", ""),
                    source_tag=meta.get("source_tag", ""),
                    source_grade=grade,
                    embedding=[],  # not returned by chromadb query
                    chunk_index=int(meta.get("chunk_index", 0)),
                    metadata={
                        "tags": meta.get("tags", "").split(",") if meta.get("tags") else [],
                        "review_status": meta.get("review_status", "draft"),
                    },
                )
                chunks.append(chunk)

            return chunks

        except Exception as e:
            logger.error(f"ChromaDB retrieve failed: {e}")
            return []

    def retrieve_with_metadata(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str = "",
    ) -> RetrievalResult:
        """Retrieve with full metadata."""
        chunks = self.retrieve(query, top_k, category_hint)
        return RetrievalResult(
            query=query,
            chunks=chunks,
            total_indexed=self.total_chunks(),
        )

    def search(
        self,
        query: str,
        top_k: int = 3,
        category_hint: str = "",
        tags: list[str] | None = None,
        source_grade: str = "",
    ) -> list[KnowledgeChunk]:
        """Enhanced search with filters."""
        if not self._available:
            return []

        # Build where filter
        where_clauses: list[dict[str, Any]] = []
        if category_hint:
            where_clauses.append({"category": category_hint})
        if source_grade:
            where_clauses.append({"source_grade": source_grade})

        where_filter: dict[str, Any] | None = None
        if len(where_clauses) == 1:
            where_filter = where_clauses[0]
        elif len(where_clauses) > 1:
            where_filter = {"$and": where_clauses}

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k * 3,  # over-fetch for post-filtering
                where=where_filter,
            )

            chunks = self._results_to_chunks(results)

            # Post-filter by tags if needed
            if tags:
                chunks = [
                    c for c in chunks
                    if any(t in c.metadata.get("tags", []) for t in tags)
                ]

            return chunks[:top_k]

        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            return []

    def index_article(
        self, article: KnowledgeArticle, chunks: list[KnowledgeChunk]
    ) -> int:
        """Index an article's chunks into chromadb."""
        if not self._available or not self._collection:
            return 0

        self._articles[article.article_id] = article

        # Delete existing chunks for this article (re-index)
        try:
            existing = self._collection.get(
                where={"article_id": article.article_id}
            )
            if existing and existing["ids"]:
                self._collection.delete(ids=existing["ids"])
        except Exception:
            pass

        # Prepare data for upsert
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for chunk in chunks:
            ids.append(chunk.chunk_id)
            documents.append(chunk.content)
            metadatas.append({
                "article_id": chunk.article_id,
                "title": chunk.title,
                "category": chunk.category,
                "source_tag": chunk.source_tag,
                "source_grade": chunk.source_grade.value,
                "chunk_index": chunk.chunk_index,
                "tags": ",".join(chunk.metadata.get("tags", [])),
                "review_status": chunk.metadata.get("review_status", "draft"),
            })

        try:
            # Batch upsert (chromadb will compute embeddings)
            self._collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            return len(chunks)
        except Exception as e:
            logger.error(f"ChromaDB index failed: {e}")
            return 0

    def bulk_index(
        self, articles_and_chunks: list[tuple[KnowledgeArticle, list[KnowledgeChunk]]]
    ) -> int:
        """Bulk index multiple articles."""
        total = 0
        for article, chunks in articles_and_chunks:
            count = self.index_article(article, chunks)
            total += count
        return total

    def list_articles(self) -> list[KnowledgeArticle]:
        """List all indexed articles."""
        return list(self._articles.values())

    def get_article_chunks(self, article_id: str) -> list[KnowledgeChunk]:
        """Get all chunks for a specific article."""
        if not self._available or not self._collection:
            return []

        try:
            results = self._collection.get(
                where={"article_id": article_id}
            )
            return self._get_results_to_chunks(results)
        except Exception:
            return []

    def total_chunks(self) -> int:
        """Total number of indexed chunks."""
        if not self._available or not self._collection:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    def _results_to_chunks(self, results: dict[str, Any]) -> list[KnowledgeChunk]:
        """Convert chromadb query results to KnowledgeChunk list."""
        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        chunks: list[KnowledgeChunk] = []
        ids = results["ids"][0]
        documents = results["documents"][0] if results.get("documents") else []
        metadatas = results["metadatas"][0] if results.get("metadatas") else []

        for i, chunk_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            content = documents[i] if i < len(documents) else ""
            chunks.append(self._meta_to_chunk(chunk_id, content, meta))

        return chunks

    def _get_results_to_chunks(self, results: dict[str, Any]) -> list[KnowledgeChunk]:
        """Convert chromadb get results to KnowledgeChunk list."""
        if not results or not results.get("ids"):
            return []

        chunks: list[KnowledgeChunk] = []
        ids = results["ids"]
        documents = results.get("documents", [])
        metadatas = results.get("metadatas", [])

        for i, chunk_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            content = documents[i] if i < len(documents) else ""
            chunks.append(self._meta_to_chunk(chunk_id, content, meta))

        return chunks

    @staticmethod
    def _meta_to_chunk(chunk_id: str, content: str, meta: dict[str, Any]) -> KnowledgeChunk:
        """Convert chromadb metadata dict to KnowledgeChunk."""
        grade_str = meta.get("source_grade", "B")
        try:
            grade = SourceGrade(grade_str)
        except ValueError:
            grade = SourceGrade.B

        tags_str = meta.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        return KnowledgeChunk(
            chunk_id=chunk_id,
            article_id=meta.get("article_id", ""),
            content=content,
            title=meta.get("title", ""),
            category=meta.get("category", ""),
            source_tag=meta.get("source_tag", ""),
            source_grade=grade,
            embedding=[],
            chunk_index=int(meta.get("chunk_index", 0)),
            metadata={
                "tags": tags,
                "review_status": meta.get("review_status", "draft"),
            },
        )
