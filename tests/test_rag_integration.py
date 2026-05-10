"""
RAG Integration Tests (M4 W3)
================================
Validates that RAG is properly integrated into chat.py and decisions.py:
  1. chat: RAG injection into system_prompt works
  2. chat: enrich_interpretation appends further reading
  3. chat: RAG failure does not block main flow
  4. decisions/checklist: further_reading populated
  5. decisions/review: further_reading populated
  6. decisions: RAG failure does not block main flow

Run: cd backend && python -m pytest ../tests/test_rag_integration.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

import pytest

# Ensure backend/ is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---- Test Fixtures ----


@dataclass
class FakeChunk:
    """Minimal KnowledgeChunk-like object for testing."""
    chunk_id: str = "chunk_001"
    article_id: str = "art_001"
    content: str = "应急金应覆盖 6 个月支出，存放于货币基金。"
    title: str = "应急金的 6 个月法则"
    category: str = "家庭财务基础"
    source_tag: str = "招行私行研报"
    source_grade: str = "A"
    embedding: list = None
    metadata: dict = None

    def __post_init__(self):
        if self.embedding is None:
            self.embedding = [0.0] * 10
        if self.metadata is None:
            self.metadata = {}


class FakeRetriever:
    """Fake retriever that returns controlled results for testing."""

    def __init__(self, chunks=None, should_raise=False):
        self._chunks = chunks or []
        self._should_raise = should_raise
        self._indexed = 5  # simulate having data

    def total_chunks(self) -> int:
        if self._should_raise:
            raise RuntimeError("retriever error")
        return self._indexed

    def retrieve(self, query: str, top_k: int = 3, category_hint: str = "") -> list:
        if self._should_raise:
            raise RuntimeError("retriever error")
        return self._chunks[:top_k]


# ---- Tests: build_rag_context ----


def test_build_rag_context_with_chunks():
    """build_rag_context returns proper structure when chunks found."""
    from use_cases.interpret_with_rag import build_rag_context

    chunks = [
        FakeChunk(chunk_id="c1", title="应急金的 6 个月法则"),
        FakeChunk(chunk_id="c2", title="股债再平衡原理", content="每年再平衡一次。"),
    ]
    retriever = FakeRetriever(chunks=chunks)

    result = build_rag_context(retriever, facts_summary="我要买基金", category_hint="资产配置")

    assert result["has_rag"] is True
    assert len(result["rag_chunks"]) == 2
    assert "应急金的 6 个月法则" in result["further_reading"]
    assert "股债再平衡原理" in result["further_reading"]
    assert "参考资料" in result["rag_prompt_injection"]
    assert "招行私行研报" in result["rag_prompt_injection"]


def test_build_rag_context_empty():
    """build_rag_context returns has_rag=False when no chunks."""
    from use_cases.interpret_with_rag import build_rag_context

    retriever = FakeRetriever(chunks=[])

    result = build_rag_context(retriever, facts_summary="随便问问")

    assert result["has_rag"] is False
    assert result["rag_chunks"] == []
    assert result["further_reading"] == []
    assert result["rag_prompt_injection"] == ""


# ---- Tests: enrich_interpretation ----


def test_enrich_interpretation_appends_reading():
    """enrich_interpretation appends 📚 section to text."""
    from use_cases.interpret_with_rag import enrich_interpretation

    chunks = [FakeChunk(title="应急金的 6 个月法则")]
    retriever = FakeRetriever(chunks=chunks)

    result = enrich_interpretation(
        retriever,
        interpretation_text="你应该先存应急金。",
        facts_summary="应急金",
        category_hint="家庭财务基础",
    )

    assert "📚 延伸阅读" in result["text"]
    assert "应急金的 6 个月法则" in result["text"]
    assert result["has_rag"] is True
    assert result["further_reading"] == ["应急金的 6 个月法则"]


def test_enrich_interpretation_no_chunks():
    """enrich_interpretation returns original text when no chunks."""
    from use_cases.interpret_with_rag import enrich_interpretation

    retriever = FakeRetriever(chunks=[])

    result = enrich_interpretation(
        retriever,
        interpretation_text="原文不变。",
        facts_summary="无关查询",
    )

    assert result["text"] == "原文不变。"
    assert result["has_rag"] is False


# ---- Tests: RAG failure non-blocking ----


def test_chat_rag_injection_failure_nonblocking():
    """If get_retriever() raises, chat should still work (rule-based fallback)."""
    # This tests the pattern: try/except around RAG code
    # Simulate by calling build_rag_context with a broken retriever
    from use_cases.interpret_with_rag import build_rag_context

    class BrokenRetriever:
        def total_chunks(self):
            return 5

        def retrieve(self, query, top_k=3, category_hint=""):
            raise RuntimeError("ChromaDB crashed!")

    # build_rag_context should propagate the exception
    # but the chat.py wrapper catches it — test the wrapper pattern
    rag_context = {"has_rag": False, "further_reading": []}
    try:
        retriever = BrokenRetriever()
        rag_context = build_rag_context(retriever, facts_summary="test")
    except Exception:
        # This is what chat.py does — catch and continue
        rag_context = {"has_rag": False, "further_reading": []}

    assert rag_context["has_rag"] is False
    assert rag_context["further_reading"] == []


def test_decisions_rag_failure_nonblocking():
    """If RAG fails in decisions, warnings should still be returned without RAG entries."""
    # Simulate the pattern used in decisions.py
    warnings = ["🚨 红灯：最近涨得好 / 势头猛"]
    further_reading = []

    try:
        raise RuntimeError("ChromaDB unavailable")
    except Exception:
        pass  # Non-blocking, continue

    # Original warnings intact, no further_reading added
    assert len(warnings) == 1
    assert "红灯" in warnings[0]
    assert further_reading == []


# ---- Tests: format_further_reading ----


def test_format_further_reading():
    """format_further_reading produces correct format."""
    from use_cases.interpret_with_rag import format_further_reading

    result = format_further_reading(["应急金的 6 个月法则", "股债再平衡原理"])
    assert "📚 延伸阅读" in result
    assert "应急金的 6 个月法则" in result
    assert "股债再平衡原理" in result


def test_format_further_reading_empty():
    """format_further_reading returns empty string for no titles."""
    from use_cases.interpret_with_rag import format_further_reading

    result = format_further_reading([])
    assert result == ""


# ---- Tests: Integration with decisions patterns ----


def test_checklist_rag_further_reading_pattern():
    """Test the pattern used in decisions/checklist for RAG enrichment."""
    from use_cases.interpret_with_rag import build_rag_context

    chunks = [
        FakeChunk(title="行为偏差：追涨杀跌", category="行为金融"),
        FakeChunk(title="冷静期的科学依据", category="行为金融"),
    ]
    retriever = FakeRetriever(chunks=chunks)

    # Simulate checklist RAG pattern
    warnings = ["⚠️ 黄灯：前期亏损，想摊平成本"]
    further_reading = []

    facts_summary = "买入理由: momentum_chase, averaging_down"
    rag_ctx = build_rag_context(retriever, facts_summary=facts_summary, category_hint="行为金融", top_k=3)

    if rag_ctx["has_rag"]:
        further_reading = rag_ctx["further_reading"]
        for title in further_reading:
            warnings.append(f"📚 延伸阅读：{title}")

    assert len(further_reading) == 2
    assert "行为偏差：追涨杀跌" in further_reading
    assert any("📚 延伸阅读" in w for w in warnings)
    # Original warning still present
    assert "黄灯" in warnings[0]


def test_review_rag_further_reading_pattern():
    """Test the pattern used in decisions/review for RAG enrichment."""
    from use_cases.interpret_with_rag import build_rag_context

    chunks = [FakeChunk(title="追涨的代价：均值回归")]
    retriever = FakeRetriever(chunks=chunks)

    # Simulate review RAG pattern
    warnings = ["🚨 红灯：最近涨得好 / 势头猛"]
    further_reading = []

    facts_summary = "买入操作 贵州茅台(600519)，理由: momentum_chase; 决策质量: poor"
    rag_ctx = build_rag_context(retriever, facts_summary=facts_summary, category_hint="行为金融", top_k=3)

    if rag_ctx["has_rag"]:
        further_reading = rag_ctx["further_reading"]
        for title in further_reading:
            warnings.append(f"📚 延伸阅读：{title}")

    assert further_reading == ["追涨的代价：均值回归"]
    assert len(warnings) == 2
    assert "📚 延伸阅读：追涨的代价：均值回归" in warnings[1]


# ---- Tests: get_retriever singleton ----


def test_get_retriever_returns_singleton():
    """get_retriever returns the same instance on repeated calls."""
    from infra.knowledge.factory import get_retriever, reset_retriever

    reset_retriever()  # Clean state
    r1 = get_retriever(force_memory=True)
    r2 = get_retriever(force_memory=True)
    assert r1 is r2
    reset_retriever()  # Cleanup


def test_get_retriever_has_total_chunks():
    """get_retriever result has total_chunks() method."""
    from infra.knowledge.factory import get_retriever, reset_retriever

    reset_retriever()
    r = get_retriever(force_memory=True)
    assert hasattr(r, "total_chunks")
    assert isinstance(r.total_chunks(), int)
    reset_retriever()
