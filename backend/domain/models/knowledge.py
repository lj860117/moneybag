"""
Knowledge domain models for RAG knowledge base.
================================================

Design doc: docs/design/08-knowledge-rag.md

Contains:
  - KnowledgeChunk: a single retrievable text chunk with metadata
  - KnowledgeArticle: full article metadata (for indexing/management)
  - SourceGrade: content source quality grade (A/B/C/D)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SourceGrade(str, Enum):
    """Content source quality grade (08-knowledge-rag.md §4.1).

    A = authoritative institutions (e.g. CMB private bank, Bogleheads)
    B = classic investment books
    C = reputable KOL columns (non-stock-picking)
    D = BANNED (self-media, AI free-creation)
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class ContentCategory(str, Enum):
    """Knowledge article topic categories (08-knowledge-rag.md §2)."""

    FAMILY_FINANCE = "家庭财务基础"
    ASSET_ALLOCATION = "资产配置"
    SINGLE_ASSET = "单类资产"
    BEHAVIORAL_FINANCE = "行为金融"
    COMMON_TRAPS = "常见陷阱"
    MATH_BASICS = "数学常识"


@dataclass(frozen=True)
class KnowledgeChunk:
    """A single retrievable text chunk with embedding and metadata.

    Each article is split into 1+ chunks for vector retrieval.
    The chunk is the atomic unit returned by RAG search.

    Fields::

        chunk_id        -- unique identifier (article_slug + chunk_index)
        article_id      -- parent article slug (e.g. "emergency-fund-6-months")
        content         -- chunk text (200-500 chars typical)
        title           -- parent article title
        category        -- content category (ContentCategory enum)
        source_tag      -- source attribution string
        source_grade    -- A/B/C quality grade
        embedding       -- vector embedding (list of floats)
        chunk_index     -- position within parent article (0-based)
        metadata        -- extra fields (reviewer, reviewed_at, version, etc.)
    """

    chunk_id: str
    article_id: str
    content: str
    title: str
    category: str
    source_tag: str
    source_grade: SourceGrade
    embedding: list[float] = field(default_factory=list)
    chunk_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Serialize to dict for storage/API response."""
        return {
            "chunk_id": self.chunk_id,
            "article_id": self.article_id,
            "content": self.content,
            "title": self.title,
            "category": self.category,
            "source_tag": self.source_tag,
            "source_grade": self.source_grade.value,
            "embedding": self.embedding,
            "chunk_index": self.chunk_index,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgeChunk":
        """Construct from stored dict."""
        return cls(
            chunk_id=d.get("chunk_id", ""),
            article_id=d.get("article_id", ""),
            content=d.get("content", ""),
            title=d.get("title", ""),
            category=d.get("category", ""),
            source_tag=d.get("source_tag", ""),
            source_grade=SourceGrade(d.get("source_grade", "B")),
            embedding=d.get("embedding", []),
            chunk_index=d.get("chunk_index", 0),
            metadata=d.get("metadata", {}),
        )


@dataclass(frozen=True)
class KnowledgeArticle:
    """Full article metadata for indexing and management.

    Represents the front-matter of a knowledge Markdown file.
    """

    article_id: str  # slug, e.g. "emergency-fund-6-months"
    title: str
    category: str
    source: str
    source_url: str = ""
    source_grade: SourceGrade = SourceGrade.B
    reviewer: str = ""
    reviewed_at: str = ""
    version: str = "v1"

    def to_dict(self) -> dict[str, object]:
        """Serialize to dict."""
        return {
            "article_id": self.article_id,
            "title": self.title,
            "category": self.category,
            "source": self.source,
            "source_url": self.source_url,
            "source_grade": self.source_grade.value,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KnowledgeArticle":
        """Construct from stored dict."""
        return cls(
            article_id=d.get("article_id", ""),
            title=d.get("title", ""),
            category=d.get("category", ""),
            source=d.get("source", ""),
            source_url=d.get("source_url", ""),
            source_grade=SourceGrade(d.get("source_grade", "B")),
            reviewer=d.get("reviewer", ""),
            reviewed_at=d.get("reviewed_at", ""),
            version=d.get("version", "v1"),
        )


@dataclass(frozen=True)
class RetrievalResult:
    """Result of a RAG retrieval query.

    Contains the matched chunks plus query metadata.
    """

    query: str
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    total_indexed: int = 0

    @property
    def has_results(self) -> bool:
        """Whether any relevant chunks were found."""
        return len(self.chunks) > 0

    @property
    def titles(self) -> list[str]:
        """Unique article titles from results (for 'further reading')."""
        seen: set[str] = set()
        result: list[str] = []
        for chunk in self.chunks:
            if chunk.title not in seen:
                seen.add(chunk.title)
                result.append(chunk.title)
        return result
