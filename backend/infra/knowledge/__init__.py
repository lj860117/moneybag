"""
Knowledge infrastructure -- RAG retrieval, embedding store, document indexing.

Design doc: docs/design/08-knowledge-rag.md

Exports:
  - KnowledgeRetriever: in-memory implementation (TF-IDF / sentence-transformers)
  - ChromaKnowledgeStore: persistent chromadb implementation (SQLite)
  - get_retriever: factory — auto-selects best backend
  - load_and_index_articles: startup helper to build index from content/ dir
  - JsonQuestionBank: question template bank for Socratic questions
  - get_question_bank: factory for question bank singleton
  - MultiViewAdvisor: multi-perspective review generator (M6 W1)
  - get_multi_view_advisor: factory for multi-view advisor singleton
"""
from infra.knowledge.retriever import KnowledgeRetriever
from infra.knowledge.indexer import load_and_index_articles
from infra.knowledge.chromadb_store import ChromaKnowledgeStore
from infra.knowledge.factory import get_retriever
from infra.knowledge.question_bank import JsonQuestionBank, get_question_bank
from infra.knowledge.multi_view_advisor import MultiViewAdvisor, get_multi_view_advisor

__all__ = [
    "KnowledgeRetriever",
    "ChromaKnowledgeStore",
    "get_retriever",
    "load_and_index_articles",
    "JsonQuestionBank",
    "get_question_bank",
    "MultiViewAdvisor",
    "get_multi_view_advisor",
]
