"""
Monthly RAG Update
==================
Skeleton for the monthly knowledge-base refresh mechanism.
Runs on the 1st of each month — discovers, validates, and indexes
new articles added to the knowledge base since the last run.

Usage:
    python scripts/monthly_rag_update.py [--dry-run] [--force-reindex]

Design doc: docs/design/08-knowledge-rag.md §四
Scheduling: docs/design/05-scheduling.md (monthly cron)

Key invariants:
  - New articles must pass frontmatter validation before indexing
  - Duplicate detection: same article_id → skip (log warning)
  - No AI free-creation: this script only indexes human-authored content
  - All state (last-run timestamp, indexed article list) via infra/store
  - Failure: logs error per article, does NOT abort entire run
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure backend/ is on path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from infra.knowledge import get_retriever, load_and_index_articles
from infra.store.file_store import FileStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("monthly_rag_update")

# ============================================================================
# Constants
# ============================================================================

CONTENT_DIR = BACKEND_DIR / "infra" / "knowledge" / "content"
STATE_COLLECTION = "rag_update_state"

# Required frontmatter fields — articles missing any are rejected
REQUIRED_FRONTMATTER_FIELDS = [
    "title",
    "category",
    "source",
    "source_url",
    "source_grade",
    "reviewer",
    "reviewed_at",
    "version",
    "tags",
    "review_status",
]

VALID_CATEGORIES = {
    "家庭财务基础",
    "单类资产",
    "投资策略",
    "行为金融",
    "税务与债务",
}

VALID_REVIEW_STATUSES = {"published", "draft", "archived"}


# ============================================================================
# State Management (via infra/store)
# ============================================================================

_state_store = FileStore(collection=STATE_COLLECTION)
_STATE_KEY = "global"


def load_state() -> Dict[str, Any]:
    """Load persistent update state from store."""
    data = _state_store.load(_STATE_KEY)
    if not data:
        return {
            "last_run_at": None,
            "indexed_article_ids": [],
            "total_runs": 0,
        }
    return data


def save_state(state: Dict[str, Any]) -> None:
    """Persist update state to store."""
    state["updated_at"] = time.time()
    _state_store.save(_STATE_KEY, state)


# ============================================================================
# Frontmatter Validation
# ============================================================================

def parse_frontmatter(md_content: str) -> Optional[Dict[str, str]]:
    """Parse YAML frontmatter from markdown content.

    Returns dict of fields, or None if frontmatter is missing/malformed.
    Handles only simple key: value pairs (no nested YAML).
    """
    lines = md_content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    fm_lines = []
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
        fm_lines.append(line)

    if end_idx is None:
        return None  # No closing ---

    fields: Dict[str, str] = {}
    for line in fm_lines:
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()

    return fields


def validate_article(article_path: Path) -> List[str]:
    """Validate a candidate article file.

    Returns list of error strings. Empty list = valid.
    """
    errors: List[str] = []

    try:
        content = article_path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"Cannot read file: {e}"]

    fm = parse_frontmatter(content)
    if fm is None:
        return ["Missing or malformed frontmatter (no --- delimiters)"]

    # Check required fields
    for field in REQUIRED_FRONTMATTER_FIELDS:
        if field not in fm or not fm[field]:
            errors.append(f"Missing required frontmatter field: {field}")

    # Validate category
    category = fm.get("category", "")
    if category and category not in VALID_CATEGORIES:
        errors.append(f"Invalid category '{category}'. Must be one of: {VALID_CATEGORIES}")

    # Validate review_status
    status = fm.get("review_status", "")
    if status and status not in VALID_REVIEW_STATUSES:
        errors.append(f"Invalid review_status '{status}'. Must be one of: {VALID_REVIEW_STATUSES}")

    # Ensure article has body content (after frontmatter)
    body = content.split("---", 2)[-1].strip() if content.count("---") >= 2 else ""
    if len(body) < 100:
        errors.append(f"Article body too short ({len(body)} chars, minimum 100)")

    return errors


# ============================================================================
# Discovery
# ============================================================================

def discover_new_articles(already_indexed: List[str]) -> List[Path]:
    """Find .md article files not yet in the indexed set.

    Only returns articles with review_status: published.
    Skips subdirectories (multiviews/, questions/).
    """
    if not CONTENT_DIR.exists():
        logger.error(f"Content directory not found: {CONTENT_DIR}")
        return []

    new_articles: List[Path] = []
    for md_file in sorted(CONTENT_DIR.glob("*.md")):
        article_id = md_file.stem
        if article_id in already_indexed:
            continue

        # Quick check: only published articles
        try:
            content = md_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            if fm and fm.get("review_status") == "published":
                new_articles.append(md_file)
        except Exception as e:
            logger.warning(f"Cannot read {md_file.name}: {e}")

    return new_articles


# ============================================================================
# Main Update Logic
# ============================================================================

def run_monthly_update(dry_run: bool = False, force_reindex: bool = False) -> Dict[str, Any]:
    """Run the monthly RAG knowledge base update.

    Args:
        dry_run: If True, validate only — do not index or persist state.
        force_reindex: If True, re-index all articles (clears indexed set).

    Returns:
        Summary dict with counts and any errors.
    """
    logger.info(f"=== Monthly RAG Update === dry_run={dry_run}, force_reindex={force_reindex}")

    state = load_state()
    already_indexed: List[str] = state.get("indexed_article_ids", [])

    if force_reindex:
        logger.info("Force reindex: clearing existing indexed set")
        already_indexed = []

    # 1. Discover new articles
    new_articles = discover_new_articles(already_indexed)
    logger.info(f"Found {len(new_articles)} new article(s) to process")

    results: Dict[str, Any] = {
        "new_found": len(new_articles),
        "validated_ok": 0,
        "validation_errors": 0,
        "indexed": 0,
        "skipped_errors": [],
        "dry_run": dry_run,
    }

    if not new_articles:
        logger.info("No new articles to index. Done.")
        if not dry_run:
            state["last_run_at"] = time.time()
            state["total_runs"] = state.get("total_runs", 0) + 1
            save_state(state)
        return results

    # 2. Validate each article
    valid_articles: List[Path] = []
    for article_path in new_articles:
        errors = validate_article(article_path)
        if errors:
            logger.warning(f"Validation FAILED for {article_path.name}: {errors}")
            results["validation_errors"] += 1
            results["skipped_errors"].append({
                "article_id": article_path.stem,
                "errors": errors,
            })
        else:
            logger.info(f"Validation OK: {article_path.name}")
            results["validated_ok"] += 1
            valid_articles.append(article_path)

    # 3. Index valid articles
    if not valid_articles:
        logger.info("No valid new articles to index.")
    elif dry_run:
        logger.info(f"[DRY-RUN] Would index {len(valid_articles)} article(s): "
                    f"{[p.stem for p in valid_articles]}")
        results["indexed"] = len(valid_articles)
    else:
        retriever = get_retriever()
        # Re-load all articles to rebuild index (simple approach for correctness)
        # TODO: optimize to incremental indexing when article count exceeds ~200
        try:
            load_and_index_articles(retriever)
            results["indexed"] = len(valid_articles)
            logger.info(f"Indexed {len(valid_articles)} new article(s)")
        except Exception as e:
            logger.error(f"Indexing failed: {e}")
            results["indexing_error"] = str(e)

    # 4. Persist state
    if not dry_run and results.get("indexed", 0) > 0:
        new_ids = [p.stem for p in valid_articles]
        already_indexed = list(set(already_indexed + new_ids))
        state["indexed_article_ids"] = already_indexed
        state["last_run_at"] = time.time()
        state["total_runs"] = state.get("total_runs", 0) + 1
        save_state(state)
        logger.info(f"State persisted. Total indexed: {len(already_indexed)}")
    elif not dry_run:
        state["last_run_at"] = time.time()
        state["total_runs"] = state.get("total_runs", 0) + 1
        save_state(state)

    return results


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    """CLI entry point for monthly RAG update."""
    parser = argparse.ArgumentParser(description="Monthly RAG Knowledge Base Update")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only — do not index or persist state",
    )
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="Re-index all articles from scratch (ignores cached indexed set)",
    )
    args = parser.parse_args()

    results = run_monthly_update(dry_run=args.dry_run, force_reindex=args.force_reindex)

    logger.info(
        f"=== Done === Found: {results['new_found']}, "
        f"Valid: {results['validated_ok']}, "
        f"Indexed: {results.get('indexed', 0)}, "
        f"Errors: {results['validation_errors']}"
    )

    if results["skipped_errors"]:
        logger.warning("Articles with validation errors (not indexed):")
        for item in results["skipped_errors"]:
            logger.warning(f"  - {item['article_id']}: {item['errors']}")

    # Exit with error code if any articles failed validation
    if results["validation_errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
