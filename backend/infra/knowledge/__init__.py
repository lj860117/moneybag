"""
Knowledge infrastructure -- RAG retrieval, embedding store, document indexing.

Design doc: docs/design/08-knowledge-rag.md

Exports:
  - KnowledgeRetriever: implements KnowledgeRetrieverProtocol
  - load_and_index_articles: startup helper to build index from content/ dir
"""
from infra.knowledge.retriever import KnowledgeRetriever
from infra.knowledge.indexer import load_and_index_articles

__all__ = [
    "KnowledgeRetriever",
    "load_and_index_articles",
]
