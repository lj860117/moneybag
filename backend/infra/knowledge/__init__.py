"""
Knowledge infrastructure -- RAG retrieval, embedding store, document indexing.

Design doc: docs/design/08-knowledge-rag.md

Exports:
  - KnowledgeRetriever: in-memory implementation (TF-IDF / sentence-transformers)
  - ChromaKnowledgeStore: persistent chromadb implementation (SQLite)
  - load_and_index_articles: startup helper to build index from content/ dir
"""
from infra.knowledge.retriever import KnowledgeRetriever
from infra.knowledge.indexer import load_and_index_articles
from infra.knowledge.chromadb_store import ChromaKnowledgeStore

__all__ = [
    "KnowledgeRetriever",
    "ChromaKnowledgeStore",
    "load_and_index_articles",
]
