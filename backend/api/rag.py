"""
RAG Knowledge API routes
=========================
Endpoints for knowledge retrieval, article listing, and statistics.

Design doc: docs/design/08-knowledge-rag.md

Routes:
  POST /api/rag/retrieve     - Retrieve relevant knowledge chunks
  GET  /api/rag/articles     - List all indexed articles
  GET  /api/rag/stats        - Knowledge base statistics
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Optional

from infra.knowledge import KnowledgeRetriever, load_and_index_articles
from use_cases.retrieve_knowledge import (
    retrieve_knowledge_chunks,
    list_knowledge_articles,
    get_knowledge_stats,
)


# ---- Module-level singleton retriever ----
# Initialized on first request (lazy loading)
_retriever: Optional[KnowledgeRetriever] = None


def _get_retriever() -> KnowledgeRetriever:
    """Get or initialize the knowledge retriever singleton."""
    global _retriever
    if _retriever is None:
        _retriever = KnowledgeRetriever()
        load_and_index_articles(_retriever)
    return _retriever


# ---- Router ----

rag_router = APIRouter(prefix="/api/rag", tags=["rag"])


# ---- Request/Response Models ----

class RetrieveRequest(BaseModel):
    """Request body for knowledge retrieval."""
    query: str = Field(..., min_length=1, max_length=500, description="Natural language query")
    top_k: int = Field(default=3, ge=1, le=10, description="Max chunks to return")
    category_hint: str = Field(default="", description="Optional category filter")


class ChunkResponse(BaseModel):
    """A single knowledge chunk in the response."""
    chunk_id: str
    article_id: str
    title: str
    content: str
    category: str
    source_tag: str
    source_grade: str


class RetrieveResponse(BaseModel):
    """Response for knowledge retrieval."""
    query: str
    chunks: list[ChunkResponse]
    total_indexed: int
    further_reading: list[str]
    has_results: bool


class ArticleResponse(BaseModel):
    """Article metadata."""
    article_id: str
    title: str
    category: str
    source: str
    source_url: str
    source_grade: str
    reviewer: str
    reviewed_at: str
    version: str


class StatsResponse(BaseModel):
    """Knowledge base statistics."""
    article_count: int
    chunk_count: int
    categories: list[str]
    source_grades: dict[str, int]


# ---- Endpoints ----

@rag_router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve_knowledge(request: RetrieveRequest) -> dict[str, Any]:
    """Retrieve relevant knowledge chunks for a query.

    Uses TF-IDF based similarity search over indexed articles.
    Returns top-k most relevant chunks with source attribution.
    """
    retriever = _get_retriever()
    result = retrieve_knowledge_chunks(
        retriever=retriever,
        query=request.query,
        top_k=request.top_k,
        category_hint=request.category_hint,
    )
    return result


@rag_router.get("/articles", response_model=list[ArticleResponse])
async def list_articles() -> list[dict[str, Any]]:
    """List all indexed knowledge articles.

    Returns article metadata (no chunk content).
    """
    retriever = _get_retriever()
    return list_knowledge_articles(retriever)


@rag_router.get("/stats", response_model=StatsResponse)
async def knowledge_stats() -> dict[str, Any]:
    """Get knowledge base statistics.

    Returns article count, chunk count, categories, source grades.
    """
    retriever = _get_retriever()
    return get_knowledge_stats(retriever)
