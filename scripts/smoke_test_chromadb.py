#!/usr/bin/env python3
"""
Smoke Test: ChromaDB Knowledge Store
=====================================
Verifies chromadb_store.py works end-to-end:
  1. Initialize ChromaKnowledgeStore with test persist_dir
  2. Load one article from content/ using indexer
  3. Index the article
  4. Query "应急金怎么算" and verify non-empty results
  5. Print statistics
  6. Cleanup test data

Run: cd backend && python ../scripts/smoke_test_chromadb.py
"""
import os
import sys
import shutil
from pathlib import Path

# Ensure backend/ is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TEST_PERSIST_DIR = Path(__file__).resolve().parent.parent / "data" / "chromadb_test"


def main() -> int:
    print("=" * 60)
    print("ChromaDB Knowledge Store — Smoke Test")
    print("=" * 60)

    # Step 1: Check chromadb availability
    print("\n[1/5] Checking chromadb availability...")
    try:
        from infra.knowledge.chromadb_store import ChromaKnowledgeStore, _is_chromadb_available
        available = _is_chromadb_available()
        print(f"  chromadb installed: {available}")
        if not available:
            print("  ⚠️  chromadb not installed — testing graceful fallback only")
    except ImportError as e:
        print(f"  ❌ Import error: {e}")
        return 1

    # Step 2: Initialize store
    print(f"\n[2/5] Initializing ChromaKnowledgeStore(persist_dir={TEST_PERSIST_DIR})...")
    store = ChromaKnowledgeStore(persist_dir=str(TEST_PERSIST_DIR))
    print(f"  is_available: {store.is_available}")
    print(f"  embedding_backend: {store.embedding_backend}")

    if not store.is_available:
        print("\n  ℹ️  chromadb not available — testing fallback behavior:")
        assert store.retrieve("test") == [], "Expected empty list"
        assert store.total_chunks() == 0, "Expected 0 chunks"
        assert store.list_articles() == [], "Expected empty articles"
        print("  ✅ Graceful fallback verified (all methods return empty)")
        _cleanup()
        print("\n" + "=" * 60)
        print("SMOKE TEST PASSED (fallback mode)")
        print("=" * 60)
        return 0

    # Step 3: Load and index one article
    print("\n[3/5] Loading article: emergency-fund-6-months.md...")
    from infra.knowledge.indexer import load_article_from_file
    content_dir = BACKEND_DIR / "infra" / "knowledge" / "content"
    article_path = content_dir / "emergency-fund-6-months.md"

    result = load_article_from_file(article_path)
    if result is None:
        print("  ❌ Failed to load article")
        _cleanup()
        return 1

    article, chunks = result
    print(f"  Article: {article.title}")
    print(f"  Category: {article.category}")
    print(f"  Source grade: {article.source_grade.value}")
    print(f"  Tags: {article.tags}")
    print(f"  Chunks: {len(chunks)}")

    print("\n  Indexing into ChromaDB...")
    count = store.index_article(article, chunks)
    print(f"  Indexed: {count} chunks")
    assert count > 0, f"Expected >0 chunks indexed, got {count}"

    # Step 4: Query
    print('\n[4/5] Querying: "应急金怎么算"...')
    results = store.retrieve("应急金怎么算", top_k=3)
    print(f"  Results: {len(results)} chunks")
    assert len(results) > 0, "Expected non-empty results"

    for i, chunk in enumerate(results, 1):
        print(f"\n  [{i}] {chunk.title} (chunk {chunk.chunk_index})")
        print(f"      Source: {chunk.source_tag}")
        print(f"      Content: {chunk.content[:80]}...")

    # Step 5: Statistics
    print("\n[5/5] Statistics:")
    print(f"  Total articles: {len(store.list_articles())}")
    print(f"  Total chunks: {store.total_chunks()}")
    print(f"  Backend: {store.embedding_backend}")

    # Test search with grade filter
    print("\n  Testing search with source_grade filter...")
    b_results = store.search("应急金", top_k=3, source_grade="B")
    print(f"  B-grade results: {len(b_results)}")

    # Cleanup
    _cleanup()

    print("\n" + "=" * 60)
    print("✅ SMOKE TEST PASSED")
    print("=" * 60)
    return 0


def _cleanup():
    """Remove test persist directory."""
    if TEST_PERSIST_DIR.exists():
        shutil.rmtree(TEST_PERSIST_DIR)
        print(f"\n  🧹 Cleaned up: {TEST_PERSIST_DIR}")


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as e:
        print(f"\n❌ SMOKE TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        _cleanup()
        exit_code = 1

    sys.exit(exit_code)
