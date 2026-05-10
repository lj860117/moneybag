"""
Weekly Education Service
=========================
Pure functions for weekly lesson selection and fatigue control.
- Match holdings to relevant articles
- Check fatigue limits (weekly cap, dedup, event cooldown)
- Select best lesson for a user this week
- Render personalized intro sentence

Invariant #12: service functions are pure (no I/O, no side effects).
Design doc: docs/design/09-advisor-features.md §三
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from domain.models.education import (
    HoldingArticleMapping,
    HoldingContext,
    LessonPushRecord,
    LessonTrigger,
    PushStatus,
    WeeklyLesson,
    HOLDING_ARTICLE_MAPPINGS,
    MAX_PUSHES_PER_WEEK,
    ARTICLE_REPEAT_COOLDOWN_DAYS,
    EVENT_TRIGGER_MAX_PER_MONTH,
    DRAWDOWN_THRESHOLD_PCT,
)
from domain.models.knowledge import KnowledgeArticle


# ============================================================================
# Fatigue Control
# ============================================================================

def get_current_week_iso() -> str:
    """Return current ISO week string (e.g., '2026-W19')."""
    now = datetime.now()
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def count_pushes_this_week(
    push_history: List[LessonPushRecord],
    week_iso: str,
) -> int:
    """Count how many pushes were delivered this week."""
    return sum(
        1 for r in push_history
        if r.week_iso == week_iso and r.status == PushStatus.DELIVERED
    )


def count_event_pushes_this_month(
    push_history: List[LessonPushRecord],
    current_time: float,
) -> int:
    """Count event-triggered pushes in the last 30 days."""
    cutoff = current_time - (30 * 24 * 3600)
    return sum(
        1 for r in push_history
        if r.trigger == LessonTrigger.HOLDING_EVENT
        and r.status == PushStatus.DELIVERED
        and r.pushed_at >= cutoff
    )


def get_recently_sent_article_ids(
    push_history: List[LessonPushRecord],
    current_time: float,
    cooldown_days: int = ARTICLE_REPEAT_COOLDOWN_DAYS,
) -> Set[str]:
    """Get article IDs sent within cooldown period (for dedup)."""
    cutoff = current_time - (cooldown_days * 24 * 3600)
    return {
        r.article_id for r in push_history
        if r.status == PushStatus.DELIVERED and r.pushed_at >= cutoff
    }


def check_fatigue(
    push_history: List[LessonPushRecord],
    week_iso: str,
    trigger: LessonTrigger,
    current_time: float,
) -> Tuple[bool, str]:
    """Check if a push is allowed under fatigue rules.

    Args:
        push_history: User's push history
        week_iso: Current ISO week
        trigger: What triggered this push
        current_time: Unix timestamp

    Returns:
        (allowed: bool, reason: str)
    """
    # Rule 1: Max 2 pushes per week
    weekly_count = count_pushes_this_week(push_history, week_iso)
    if weekly_count >= MAX_PUSHES_PER_WEEK:
        return False, f"周推送上限已达 {MAX_PUSHES_PER_WEEK} 条"

    # Rule 2: Event-triggered max 1/month
    if trigger == LessonTrigger.HOLDING_EVENT:
        monthly_events = count_event_pushes_this_month(push_history, current_time)
        if monthly_events >= EVENT_TRIGGER_MAX_PER_MONTH:
            return False, f"事件触发本月已达上限 {EVENT_TRIGGER_MAX_PER_MONTH} 条"

    return True, ""


# ============================================================================
# Holding → Article Matching
# ============================================================================

def evaluate_holding_conditions(context: HoldingContext) -> List[str]:
    """Evaluate which conditions are met for a user's holdings.

    Returns list of condition strings that match the user's portfolio state.
    """
    conditions: List[str] = []

    if context.has_fund:
        conditions.append("has_fund")
    if context.has_stock:
        conditions.append("has_stock")
    if context.has_gold:
        conditions.append("has_gold")
    if context.has_bond:
        conditions.append("has_bond")
    if context.has_real_estate:
        conditions.append("has_real_estate")
    if context.has_insurance:
        conditions.append("has_insurance")

    # Drawdown event
    if (context.max_drawdown_pct is not None
            and context.max_drawdown_pct >= DRAWDOWN_THRESHOLD_PCT):
        conditions.append("drawdown_gt_10")

    # General condition (always true if any holdings)
    if context.total_positions > 0:
        conditions.append("any_holding")

    return conditions


def get_matching_articles(
    context: HoldingContext,
    available_article_ids: Set[str],
    excluded_article_ids: Set[str],
) -> List[HoldingArticleMapping]:
    """Get all matching article mappings, sorted by priority (desc).

    Args:
        context: User's holding context
        available_article_ids: Articles actually in knowledge base
        excluded_article_ids: Articles to exclude (recently sent)

    Returns:
        Sorted list of matching mappings (highest priority first).
    """
    conditions = evaluate_holding_conditions(context)

    matches: List[HoldingArticleMapping] = []
    for mapping in HOLDING_ARTICLE_MAPPINGS:
        # Check condition matches
        if mapping.condition not in conditions:
            continue
        # Check article exists in knowledge base
        if mapping.article_id not in available_article_ids:
            continue
        # Check not recently sent
        if mapping.article_id in excluded_article_ids:
            continue
        matches.append(mapping)

    # Sort by priority (highest first)
    matches.sort(key=lambda m: m.priority, reverse=True)
    return matches


# ============================================================================
# Intro Sentence Rendering
# ============================================================================

def render_intro_sentence(
    template: str,
    context: HoldingContext,
) -> str:
    """Fill intro template with user context.

    Simple {field} substitution — no AI free-creation.
    """
    subs: Dict[str, str] = {
        "user_id": context.user_id,
        "asset_name": context.drawdown_asset_name or "",
        "drawdown_pct": str(round(context.max_drawdown_pct or 0, 1)),
        "total_positions": str(context.total_positions),
    }

    text = template
    for key, value in subs.items():
        text = text.replace(f"{{{key}}}", value)

    return text.strip()


# ============================================================================
# Lesson Selection (Orchestration)
# ============================================================================

def select_weekly_lesson(
    context: HoldingContext,
    push_history: List[LessonPushRecord],
    available_articles: List[KnowledgeArticle],
    trigger: LessonTrigger = LessonTrigger.WEEKLY_REGULAR,
    current_time: Optional[float] = None,
) -> Optional[WeeklyLesson]:
    """Select the best lesson for a user this week.

    Pure function: takes all inputs, returns lesson or None.
    None means fatigue control blocked or no suitable article found.

    Args:
        context: User's holding state
        push_history: User's push record history
        available_articles: All articles in knowledge base
        trigger: What triggered this selection
        current_time: Override for testing (default: now)

    Returns:
        WeeklyLesson if a suitable lesson found and fatigue allows,
        None otherwise.
    """
    if current_time is None:
        current_time = time.time()

    week_iso = get_current_week_iso()

    # 1. Fatigue check
    allowed, reason = check_fatigue(push_history, week_iso, trigger, current_time)
    if not allowed:
        return None

    # 2. Get available and excluded article IDs
    available_ids = {a.article_id for a in available_articles}
    excluded_ids = get_recently_sent_article_ids(push_history, current_time)

    # 3. Match holdings → articles
    matches = get_matching_articles(context, available_ids, excluded_ids)
    if not matches:
        return None

    # 4. Take the highest-priority match
    best = matches[0]

    # 5. Find article metadata
    article_meta: Optional[KnowledgeArticle] = None
    for a in available_articles:
        if a.article_id == best.article_id:
            article_meta = a
            break

    if article_meta is None:
        return None

    # 6. Render intro sentence
    intro = render_intro_sentence(best.intro_template, context)

    # 7. Build lesson
    lesson_id = f"{context.user_id}:{week_iso}:{best.article_id}"

    return WeeklyLesson(
        lesson_id=lesson_id,
        user_id=context.user_id,
        article_id=best.article_id,
        article_title=article_meta.title,
        article_category=article_meta.category,
        intro_sentence=intro,
        trigger=trigger,
        week_iso=week_iso,
        created_at=current_time,
    )


def build_push_record(
    lesson: WeeklyLesson,
    status: PushStatus = PushStatus.DELIVERED,
) -> LessonPushRecord:
    """Build a push record from a delivered lesson."""
    return LessonPushRecord(
        user_id=lesson.user_id,
        article_id=lesson.article_id,
        week_iso=lesson.week_iso,
        trigger=lesson.trigger,
        status=status,
        pushed_at=lesson.created_at,
    )
