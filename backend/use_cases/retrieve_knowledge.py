"""
Retrieve Knowledge use case
============================
Orchestrates RAG retrieval: query → retrieve chunks → format response.

Design doc: docs/design/08-knowledge-rag.md §5

Invariant #10: use_cases/ → domain/ → infra/ (one-way dependency).
"""
from __future__ import annotations

from typing import Any

from domain.models.knowledge import (
    KnowledgeChunk,
    KnowledgeArticle,
    RetrievalResult,
)


def retrieve_knowledge_chunks(
    retriever: object,
    query: str,
    top_k: int = 3,
    category_hint: str = "",
) -> dict[str, Any]:
    """Retrieve relevant knowledge chunks for a query.

    Args:
        retriever: object implementing KnowledgeRetrieverProtocol
        query: natural language query
        top_k: max results
        category_hint: optional category filter

    Returns:
        Dict with keys: query, chunks, total_indexed, further_reading
    """
    # Call retriever (Protocol method)
    chunks: list[KnowledgeChunk] = retriever.retrieve(  # type: ignore[union-attr]
        query=query, top_k=top_k, category_hint=category_hint
    )

    # Get total indexed count
    total = retriever.total_chunks()  # type: ignore[union-attr]

    # Format response
    chunk_dicts = [_format_chunk(c) for c in chunks]

    # Extract unique titles for "further reading"
    seen_titles: set[str] = set()
    further_reading: list[str] = []
    for c in chunks:
        if c.title not in seen_titles:
            seen_titles.add(c.title)
            further_reading.append(c.title)

    return {
        "query": query,
        "chunks": chunk_dicts,
        "total_indexed": total,
        "further_reading": further_reading,
        "has_results": len(chunks) > 0,
    }


def list_knowledge_articles(retriever: object) -> list[dict[str, Any]]:
    """List all indexed articles (for management/UI).

    Returns list of article metadata dicts.
    """
    articles: list[KnowledgeArticle] = retriever.list_articles()  # type: ignore[union-attr]
    return [a.to_dict() for a in articles]


def get_knowledge_stats(retriever: object) -> dict[str, Any]:
    """Get knowledge base statistics.

    Returns dict with article_count, chunk_count, categories.
    """
    articles: list[KnowledgeArticle] = retriever.list_articles()  # type: ignore[union-attr]
    total_chunks = retriever.total_chunks()  # type: ignore[union-attr]

    categories: set[str] = set()
    source_grades: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    for a in articles:
        if a.category:
            categories.add(a.category)
        grade_key = a.source_grade.value
        if grade_key in source_grades:
            source_grades[grade_key] += 1

    return {
        "article_count": len(articles),
        "chunk_count": total_chunks,
        "categories": sorted(categories),
        "source_grades": source_grades,
    }


def _format_chunk(chunk: KnowledgeChunk) -> dict[str, Any]:
    """Format a chunk for API response (without embedding vector)."""
    return {
        "chunk_id": chunk.chunk_id,
        "article_id": chunk.article_id,
        "title": chunk.title,
        "content": chunk.content,
        "category": chunk.category,
        "source_tag": chunk.source_tag,
        "source_grade": chunk.source_grade.value,
    }
