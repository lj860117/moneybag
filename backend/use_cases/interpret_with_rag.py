"""
Interpret with RAG use case
=============================
Orchestrates AI interpretation with automatic RAG injection:
  1. Takes structured facts from rule engine
  2. Retrieves top-k relevant knowledge chunks
  3. Formats "延伸阅读" (further reading) section
  4. Returns interpretation context ready for LLM prompt

Design doc: docs/design/04-ai-interface.md §4 + docs/design/08-knowledge-rag.md §5

Key constraint (from 04-ai-interface.md §4):
  "有观点"输出必须引用 RAG — AI 解读必须引用 RAG 检索结果。

Invariant #10: use_cases/ → domain/ → infra/ (one-way dependency).
"""
from __future__ import annotations

from typing import Any


def build_rag_context(
    retriever: object,
    facts_summary: str,
    category_hint: str = "",
    top_k: int = 3,
) -> dict[str, Any]:
    """Build RAG context for AI interpretation prompts.

    Given a summary of rule-engine facts, retrieves the most relevant
    knowledge chunks to inject into the LLM prompt.

    Args:
        retriever: object implementing KnowledgeRetrieverProtocol
        facts_summary: text summary of computed facts (e.g. "股票偏离 +12%，集中度 35%")
        category_hint: optional category to boost (e.g. "资产配置")
        top_k: number of chunks to retrieve

    Returns:
        Dict with:
          - rag_chunks: list of chunk dicts (content + source_tag)
          - further_reading: list of article titles
          - rag_prompt_injection: formatted text to inject into LLM prompt
          - has_rag: whether any relevant chunks were found
    """
    # Retrieve relevant chunks
    chunks = retriever.retrieve(  # type: ignore[attr-defined]
        query=facts_summary,
        top_k=top_k,
        category_hint=category_hint,
    )

    if not chunks:
        return {
            "rag_chunks": [],
            "further_reading": [],
            "rag_prompt_injection": "",
            "has_rag": False,
        }

    # Format chunks for prompt injection
    rag_sections: list[str] = []
    further_reading: list[str] = []
    seen_titles: set[str] = set()
    chunk_dicts: list[dict[str, str]] = []

    for i, chunk in enumerate(chunks, 1):
        rag_sections.append(
            f"[参考资料{i}] {chunk.title}\n"
            f"来源: {chunk.source_tag}\n"
            f"{chunk.content}"
        )
        chunk_dicts.append({
            "chunk_id": chunk.chunk_id,
            "title": chunk.title,
            "content": chunk.content,
            "source_tag": chunk.source_tag,
            "category": chunk.category,
        })
        if chunk.title not in seen_titles:
            seen_titles.add(chunk.title)
            further_reading.append(chunk.title)

    # Build the prompt injection text
    rag_prompt_injection = (
        "---\n"
        "以下是相关金融常识资料，请在解读中引用（不要编造资料外的内容）：\n\n"
        + "\n\n".join(rag_sections)
        + "\n---"
    )

    return {
        "rag_chunks": chunk_dicts,
        "further_reading": further_reading,
        "rag_prompt_injection": rag_prompt_injection,
        "has_rag": True,
    }


def format_further_reading(titles: list[str]) -> str:
    """Format the '延伸阅读' section for appending to AI output.

    Args:
        titles: list of article titles from RAG results

    Returns:
        Formatted string for display, e.g.:
        📚 延伸阅读：
        - 应急金的 6 个月法则
        - 股债再平衡原理
    """
    if not titles:
        return ""

    lines = ["📚 延伸阅读："]
    for title in titles:
        lines.append(f"  - {title}")

    return "\n".join(lines)


def enrich_interpretation(
    retriever: object,
    interpretation_text: str,
    facts_summary: str,
    category_hint: str = "",
    top_k: int = 3,
) -> dict[str, Any]:
    """Enrich an AI interpretation with RAG further reading.

    Takes an existing interpretation text and appends relevant
    further reading links. This is the primary entry point for
    post-LLM enrichment.

    Args:
        retriever: KnowledgeRetrieverProtocol implementation
        interpretation_text: the AI-generated interpretation text
        facts_summary: the original facts used for retrieval
        category_hint: optional category filter
        top_k: number of RAG results

    Returns:
        Dict with:
          - text: original text + further reading section
          - further_reading: list of titles
          - rag_sources: list of source dicts
    """
    rag_context = build_rag_context(
        retriever=retriever,
        facts_summary=facts_summary,
        category_hint=category_hint,
        top_k=top_k,
    )

    further_reading_text = format_further_reading(rag_context["further_reading"])

    # Compose enriched output
    enriched_text = interpretation_text
    if further_reading_text:
        enriched_text = interpretation_text.rstrip() + "\n\n" + further_reading_text

    return {
        "text": enriched_text,
        "further_reading": rag_context["further_reading"],
        "rag_sources": rag_context["rag_chunks"],
        "has_rag": rag_context["has_rag"],
    }
