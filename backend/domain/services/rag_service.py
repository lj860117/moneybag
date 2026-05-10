"""
RAG Service -- knowledge retrieval and chunk management
=======================================================
Pure domain logic for:
  - Parsing knowledge articles from Markdown
  - Chunking text into retrievable segments
  - Computing simple embeddings (TF-IDF based, no external deps)
  - Similarity search (cosine similarity)

Design doc: docs/design/08-knowledge-rag.md

NOTE: This service has NO I/O. It operates on in-memory data structures.
The infra/knowledge/ layer handles file loading and persistence.

Invariant #9: domain/services/ modules do not import each other.
Invariant #10: domain/ does not import infra/.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from domain.models.knowledge import (
    ContentCategory,
    KnowledgeArticle,
    KnowledgeChunk,
    RetrievalResult,
    SourceGrade,
)


# ---- Constants ----

# Chunk splitting parameters
CHUNK_MAX_CHARS: int = 500
CHUNK_OVERLAP_CHARS: int = 50
MIN_CHUNK_CHARS: int = 80

# Retrieval threshold: if cosine similarity < this, chunk is irrelevant
RELEVANCE_THRESHOLD: float = 0.1

# Stopwords for Chinese + English (minimal set for TF-IDF)
_STOPWORDS: set[str] = {
    "的", "了", "是", "在", "和", "有", "不", "就", "也", "都",
    "而", "及", "与", "为", "被", "从", "到", "以", "但", "这",
    "那", "对", "等", "可以", "可能", "比如", "如果", "因为", "所以",
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "of", "in", "on", "at",
    "to", "for", "with", "by", "from", "as", "or", "and", "not",
}


# ---- Article Parsing ----


def parse_article_frontmatter(content: str) -> dict[str, str]:
    """Parse YAML-like frontmatter from a Markdown article.

    Expects format:
        ---
        key: value
        ---

    Returns dict of key-value pairs. Returns empty dict if no frontmatter.
    """
    content = content.strip()
    if not content.startswith("---"):
        return {}

    # Find closing ---
    end_idx = content.find("---", 3)
    if end_idx == -1:
        return {}

    frontmatter_text = content[3:end_idx].strip()
    result: dict[str, str] = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()

    return result


def parse_article_body(content: str) -> str:
    """Extract article body (everything after frontmatter).

    Returns the body text without the YAML frontmatter block.
    """
    content = content.strip()
    if not content.startswith("---"):
        return content

    end_idx = content.find("---", 3)
    if end_idx == -1:
        return content

    # Skip past the closing --- and any immediate newlines
    body_start = end_idx + 3
    return content[body_start:].strip()


def build_article_from_frontmatter(
    article_id: str, frontmatter: dict[str, str]
) -> KnowledgeArticle:
    """Construct KnowledgeArticle from parsed frontmatter."""
    grade_str = frontmatter.get("source_grade", "B")
    try:
        grade = SourceGrade(grade_str)
    except ValueError:
        grade = SourceGrade.B

    return KnowledgeArticle(
        article_id=article_id,
        title=frontmatter.get("title", article_id),
        category=frontmatter.get("category", ""),
        source=frontmatter.get("source", ""),
        source_url=frontmatter.get("source_url", ""),
        source_grade=grade,
        reviewer=frontmatter.get("reviewer", ""),
        reviewed_at=frontmatter.get("reviewed_at", ""),
        version=frontmatter.get("version", "v1"),
    )


# ---- Text Chunking ----


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split text into overlapping chunks, respecting paragraph boundaries.

    Strategy:
    1. Split by paragraphs (double newline)
    2. Merge short paragraphs until max_chars reached
    3. Add overlap from previous chunk to next chunk

    Returns list of chunk strings (each <= max_chars + overlap).
    """
    # Split by paragraphs
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return []

    chunks: list[str] = []
    current_chunk: str = ""

    for para in paragraphs:
        # If adding this paragraph exceeds max_chars, flush current chunk
        if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
            chunks.append(current_chunk.strip())
            # Start new chunk with overlap from end of previous
            if overlap > 0 and len(current_chunk) > overlap:
                current_chunk = current_chunk[-overlap:] + "\n\n" + para
            else:
                current_chunk = para
        else:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para

    # Don't forget the last chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # Filter out chunks that are too short to be useful
    chunks = [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]

    return chunks


def build_chunks_for_article(
    article: KnowledgeArticle, body: str
) -> list[KnowledgeChunk]:
    """Build KnowledgeChunk objects from article body text.

    Chunks the body text and creates KnowledgeChunk for each segment.
    Embeddings are NOT computed here (done separately by compute_embedding).
    """
    text_chunks = chunk_text(body)
    result: list[KnowledgeChunk] = []

    for i, chunk_text_content in enumerate(text_chunks):
        chunk = KnowledgeChunk(
            chunk_id=f"{article.article_id}_chunk_{i}",
            article_id=article.article_id,
            content=chunk_text_content,
            title=article.title,
            category=article.category,
            source_tag=article.source,
            source_grade=article.source_grade,
            embedding=[],  # computed later
            chunk_index=i,
            metadata={
                "reviewer": article.reviewer,
                "reviewed_at": article.reviewed_at,
                "version": article.version,
            },
        )
        result.append(chunk)

    return result


# ---- Simple TF-IDF Embedding ----


def tokenize(text: str) -> list[str]:
    """Simple tokenizer for Chinese + English mixed text.

    Splits on whitespace and punctuation, removes stopwords.
    For Chinese, splits into individual characters (unigrams) plus
    bigrams for common phrases.
    """
    # Remove markdown formatting
    text = re.sub(r"[#*_`\[\](){}|>~]", " ", text)
    # Split by whitespace and punctuation
    tokens = re.split(r"[\s\-/\\.,;:!?，。！？、；：""''（）【】]+", text)
    tokens = [t.lower().strip() for t in tokens if t.strip()]

    # For Chinese text, also add character-level tokens
    expanded: list[str] = []
    for token in tokens:
        if re.search(r"[一-鿿]", token):
            # Chinese: add individual chars and bigrams
            chars = [c for c in token if c.strip()]
            expanded.extend(chars)
            for j in range(len(chars) - 1):
                expanded.append(chars[j] + chars[j + 1])
        else:
            if len(token) > 1:  # skip single-char English tokens
                expanded.append(token)

    # Remove stopwords
    return [t for t in expanded if t not in _STOPWORDS and len(t) > 0]


def compute_tf(tokens: list[str]) -> dict[str, float]:
    """Compute term frequency (normalized by document length)."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {term: count / total for term, count in counts.items()}


def compute_embedding(text: str, vocabulary: list[str]) -> list[float]:
    """Compute a simple TF-IDF-like embedding vector.

    Uses the provided vocabulary as the feature space.
    Returns a normalized vector of len(vocabulary) dimensions.

    This is a lightweight approach that requires no external ML libs.
    For production, replace with sentence-transformers or OpenAI embeddings.
    """
    tokens = tokenize(text)
    tf = compute_tf(tokens)

    # Build vector
    vector = [tf.get(term, 0.0) for term in vocabulary]

    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]

    return vector


def build_vocabulary(all_chunks: list[KnowledgeChunk], max_terms: int = 2000) -> list[str]:
    """Build vocabulary from all chunk contents.

    Selects the most frequent terms across all chunks (up to max_terms).
    """
    term_counts: Counter[str] = Counter()
    for chunk in all_chunks:
        tokens = tokenize(chunk.content)
        term_counts.update(set(tokens))  # document frequency (count once per chunk)

    # Take top N by frequency (but skip terms that appear in >80% of docs = too common)
    num_docs = len(all_chunks)
    threshold = int(num_docs * 0.8)
    filtered = {
        term: count
        for term, count in term_counts.items()
        if count <= threshold and count >= 2  # appear in at least 2 docs
    }

    # Sort by frequency descending, take top max_terms
    sorted_terms = sorted(filtered.keys(), key=lambda t: filtered[t], reverse=True)
    return sorted_terms[:max_terms]


# ---- Similarity Search ----


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Both vectors must have the same length.
    Returns value in [-1, 1], where 1 = identical direction.
    """
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def search_chunks(
    query_embedding: list[float],
    chunks: list[KnowledgeChunk],
    top_k: int = 3,
    category_hint: str = "",
    threshold: float = RELEVANCE_THRESHOLD,
) -> list[KnowledgeChunk]:
    """Search for most relevant chunks given a query embedding.

    Args:
        query_embedding: embedding vector for the query
        chunks: all indexed chunks to search
        top_k: max results to return
        category_hint: if set, boost chunks from this category
        threshold: minimum similarity to include

    Returns:
        List of top-k chunks sorted by relevance (descending).
    """
    if not chunks or not query_embedding:
        return []

    scored: list[tuple[float, KnowledgeChunk]] = []

    for chunk in chunks:
        if not chunk.embedding:
            continue

        sim = cosine_similarity(query_embedding, chunk.embedding)

        # Category boost: +20% if matches hint
        if category_hint and chunk.category == category_hint:
            sim *= 1.2

        if sim >= threshold:
            scored.append((sim, chunk))

    # Sort by similarity descending
    scored.sort(key=lambda x: x[0], reverse=True)

    return [chunk for _, chunk in scored[:top_k]]


def retrieve_knowledge(
    query: str,
    chunks: list[KnowledgeChunk],
    vocabulary: list[str],
    top_k: int = 3,
    category_hint: str = "",
) -> RetrievalResult:
    """High-level retrieval function: query -> RetrievalResult.

    Computes query embedding and searches indexed chunks.
    """
    query_embedding = compute_embedding(query, vocabulary)
    matched = search_chunks(query_embedding, chunks, top_k, category_hint)

    return RetrievalResult(
        query=query,
        chunks=matched,
        total_indexed=len(chunks),
    )
