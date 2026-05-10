"""
Weekly Education Cron
======================
Runs every Sunday 20:00 — selects and delivers weekly financial lessons
personalized to each user's holdings.

Usage:
    python scripts/weekly_education_cron.py [--user_id USER] [--dry-run]

Design doc: docs/design/09-advisor-features.md §三
Scheduling: 05-scheduling.md (weekly cron, not part of nightly pipeline)

Key invariants:
  - AI only selects + fills blanks, never free-creates content
  - Content 100% from RAG knowledge base (08-knowledge-rag.md)
  - Fatigue control: max 2/week, same article not repeated within 90 days
  - Holdings read from infra/store (not services/ direct-call)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure backend/ is on path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from domain.models.education import (
    HoldingContext,
    LessonPushRecord,
    LessonTrigger,
    PushStatus,
    WeeklyLesson,
)
from domain.services.education_service import get_current_week_iso
from use_cases.generate_weekly_lesson import (
    generate_weekly_lesson,
    record_lesson_push,
)
from infra.knowledge import get_retriever, load_and_index_articles
from infra.store.file_store import FileStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("weekly_education_cron")


# ============================================================================
# Push History Store (uses infra/store)
# ============================================================================

_push_store = FileStore(collection="education_push_history")


def load_push_history(user_id: str) -> List[LessonPushRecord]:
    """Load user's push history from store."""
    data = _push_store.load(user_id)
    if not data or "records" not in data:
        return []
    return [LessonPushRecord.from_dict(r) for r in data["records"]]


def save_push_record(record: LessonPushRecord) -> None:
    """Append a push record to the user's history."""
    history = load_push_history(record.user_id)
    history.append(record)
    _push_store.save(record.user_id, {
        "records": [r.to_dict() for r in history],
        "updated_at": time.time(),
    })


# ============================================================================
# Holdings Context Builder
# ============================================================================

def build_holding_context(user_id: str) -> HoldingContext:
    """Build HoldingContext from user's stored portfolio data.

    Reads from infra/store (not services/ direct-call).
    This is a simplified version — in production would read from
    balance_sheet + holdings stores.
    """
    # Try to read from balance sheet store
    bs_store = FileStore(collection="balance_sheets")
    bs_data = bs_store.load(user_id)

    has_fund = False
    has_stock = False
    has_gold = False
    has_bond = False
    has_real_estate = False
    has_insurance = False
    asset_classes: List[str] = []
    total_positions = 0

    if bs_data:
        # Parse balance sheet items
        investments = bs_data.get("investments", [])
        if isinstance(investments, list):
            for item in investments:
                item_name = str(item.get("name", "")).lower()
                item_type = str(item.get("asset_type", "")).lower()

                if "基金" in item_name or "fund" in item_type or "etf" in item_name:
                    has_fund = True
                    asset_classes.append("fund")
                elif "股" in item_name or "stock" in item_type:
                    has_stock = True
                    asset_classes.append("stock")
                elif "黄金" in item_name or "gold" in item_type:
                    has_gold = True
                    asset_classes.append("gold")
                elif "债" in item_name or "bond" in item_type:
                    has_bond = True
                    asset_classes.append("bond")
                total_positions += 1

        real_estate = bs_data.get("real_estate", [])
        if real_estate:
            has_real_estate = True
            asset_classes.append("real_estate")
            total_positions += len(real_estate) if isinstance(real_estate, list) else 1

    # If no balance sheet, check if user has any holdings data at all
    if total_positions == 0:
        # Default: assume user has at least fund holdings (most common case)
        has_fund = True
        asset_classes = ["fund"]
        total_positions = 1

    return HoldingContext(
        user_id=user_id,
        asset_classes=list(set(asset_classes)),
        has_fund=has_fund,
        has_stock=has_stock,
        has_gold=has_gold,
        has_bond=has_bond,
        has_real_estate=has_real_estate,
        has_insurance=has_insurance,
        total_positions=total_positions,
    )


# ============================================================================
# Main Cron Logic
# ============================================================================

def run_weekly_education(
    user_id: str,
    dry_run: bool = False,
    trigger: LessonTrigger = LessonTrigger.WEEKLY_REGULAR,
) -> Optional[Dict[str, Any]]:
    """Run weekly education for a single user.

    Args:
        user_id: Target user
        dry_run: If True, don't persist push record
        trigger: What triggered this run

    Returns:
        Lesson dict if delivered, None if skipped.
    """
    logger.info(f"[{user_id}] Starting weekly education (trigger={trigger.value}, dry_run={dry_run})")

    # 1. Load push history
    push_history = load_push_history(user_id)

    # 2. Build holding context
    context = build_holding_context(user_id)
    logger.info(f"[{user_id}] Holdings: {context.asset_classes}, positions={context.total_positions}")

    # 3. Load knowledge articles
    retriever = get_retriever()
    if retriever.total_chunks() == 0:
        load_and_index_articles(retriever)
    articles = retriever.list_articles()

    # 4. Generate lesson
    lesson = generate_weekly_lesson(
        context=context,
        push_history=push_history,
        available_articles=articles,
        trigger=trigger,
    )

    if lesson is None:
        logger.info(f"[{user_id}] No lesson generated (fatigue control or no match)")
        return None

    logger.info(f"[{user_id}] Selected: '{lesson.article_title}' (trigger={lesson.trigger.value})")

    # 5. Persist push record (unless dry run)
    if not dry_run:
        record = record_lesson_push(lesson)
        save_push_record(record)
        logger.info(f"[{user_id}] Push record saved")

    return lesson.to_dict()


def get_all_user_ids() -> List[str]:
    """Get list of all registered user IDs.

    Reads from family profiles store.
    """
    profile_store = FileStore(collection="profiles")
    # FileStore stores files as {user_id}.json
    data_dir = profile_store._data_dir  # type: ignore[attr-defined]
    if not data_dir.exists():
        return ["default_user"]  # Fallback

    user_ids = []
    for f in data_dir.glob("*.json"):
        user_ids.append(f.stem)

    return user_ids if user_ids else ["default_user"]


def main() -> None:
    """CLI entry point for weekly education cron."""
    parser = argparse.ArgumentParser(description="Weekly Financial Education Cron")
    parser.add_argument("--user-id", type=str, default="", help="Run for specific user (default: all users)")
    parser.add_argument("--dry-run", action="store_true", help="Don't persist push records")
    parser.add_argument("--trigger", type=str, default="weekly_regular",
                        choices=["weekly_regular", "holding_event", "new_article"],
                        help="Trigger type")
    args = parser.parse_args()

    trigger = LessonTrigger(args.trigger)
    week_iso = get_current_week_iso()
    logger.info(f"=== Weekly Education Cron === Week: {week_iso}, Trigger: {trigger.value}")

    if args.user_id:
        user_ids = [args.user_id]
    else:
        user_ids = get_all_user_ids()

    results = {"delivered": 0, "skipped": 0, "errors": 0}

    for uid in user_ids:
        try:
            result = run_weekly_education(uid, dry_run=args.dry_run, trigger=trigger)
            if result:
                results["delivered"] += 1
                logger.info(f"[{uid}] ✅ Delivered: {result['article_title']}")
            else:
                results["skipped"] += 1
        except Exception as e:
            results["errors"] += 1
            logger.error(f"[{uid}] ❌ Error: {e}")

    logger.info(f"=== Done === Delivered: {results['delivered']}, Skipped: {results['skipped']}, Errors: {results['errors']}")


if __name__ == "__main__":
    main()
