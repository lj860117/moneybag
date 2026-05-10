"""
Knowledge Indexer -- loads Markdown articles from disk and builds index
======================================================================
Startup helper that reads all .md files from infra/knowledge/content/,
parses frontmatter + body, chunks the text, and indexes into retriever.

Design doc: docs/design/08-knowledge-rag.md §6

Usage::

    from infra.knowledge import load_and_index_articles, KnowledgeRetriever

    retriever = KnowledgeRetriever()
    count = load_and_index_articles(retriever)
    print(f"Indexed {count} chunks")
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from domain.models.knowledge import KnowledgeArticle, KnowledgeChunk, SourceGrade
from domain.services.rag_service import (
    build_article_from_frontmatter,
    build_chunks_for_article,
    parse_article_body,
    parse_article_frontmatter,
)


# Default content directory (relative to this file)
_DEFAULT_CONTENT_DIR = Path(__file__).parent / "content"


def load_article_from_file(
    file_path: Path,
) -> tuple[KnowledgeArticle, list[KnowledgeChunk]] | None:
    """Load a single article from a Markdown file.

    Returns (article, chunks) tuple, or None if file is invalid/empty.
    Rejects D-grade sources (invariant: D级来源禁止).
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if not content.strip():
        return None

    # Parse frontmatter
    frontmatter = parse_article_frontmatter(content)
    if not frontmatter:
        return None

    # Reject D-grade sources
    grade_str = frontmatter.get("source_grade", "B")
    if grade_str == "D":
        return None

    # Build article metadata
    article_id = file_path.stem  # filename without extension
    article = build_article_from_frontmatter(article_id, frontmatter)

    # Parse body and chunk
    body = parse_article_body(content)
    if not body:
        return None

    chunks = build_chunks_for_article(article, body)
    if not chunks:
        return None

    return (article, chunks)


def load_and_index_articles(
    retriever: object,
    content_dir: Optional[Path] = None,
) -> int:
    """Load all .md articles from content_dir and index them into retriever.

    Args:
        retriever: object implementing KnowledgeRetrieverProtocol
            (must have bulk_index or index_article method)
        content_dir: directory containing .md files (defaults to infra/knowledge/content/)

    Returns:
        Total number of chunks indexed.
    """
    if content_dir is None:
        content_dir = _DEFAULT_CONTENT_DIR

    if not content_dir.exists() or not content_dir.is_dir():
        return 0

    # Collect all articles and their chunks
    articles_and_chunks: list[tuple[KnowledgeArticle, list[KnowledgeChunk]]] = []

    for md_file in sorted(content_dir.glob("*.md")):
        result = load_article_from_file(md_file)
        if result is not None:
            articles_and_chunks.append(result)

    if not articles_and_chunks:
        return 0

    # Use bulk_index if available (more efficient)
    if hasattr(retriever, "bulk_index"):
        return retriever.bulk_index(articles_and_chunks)  # type: ignore[union-attr]

    # Fallback: index one by one
    total = 0
    for article, chunks in articles_and_chunks:
        count = retriever.index_article(article, chunks)  # type: ignore[union-attr]
        total += count

    return total


def get_content_dir() -> Path:
    """Return the default content directory path."""
    return _DEFAULT_CONTENT_DIR
