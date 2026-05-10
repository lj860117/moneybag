"""
Generate Weekly Lesson Use Case
================================
Orchestrates infra (knowledge retriever + push store) + domain service.

Dependency rule: use_cases/ → domain/ → infra/ (never backward).
Design doc: docs/design/09-advisor-features.md §三
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from domain.models.education import (
    HoldingContext,
    LessonPushRecord,
    LessonTrigger,
    PushStatus,
    WeeklyLesson,
)
from domain.models.knowledge import KnowledgeArticle
from domain.services.education_service import (
    build_push_record,
    check_fatigue,
    get_current_week_iso,
    select_weekly_lesson,
)


def generate_weekly_lesson(
    context: HoldingContext,
    push_history: List[LessonPushRecord],
    available_articles: List[KnowledgeArticle],
    trigger: LessonTrigger = LessonTrigger.WEEKLY_REGULAR,
    current_time: Optional[float] = None,
) -> Optional[WeeklyLesson]:
    """Generate a personalized weekly lesson for a user.

    Args:
        context: User's holding context (derived from portfolio)
        push_history: User's historical push records
        available_articles: All articles from knowledge base
        trigger: What triggered this generation
        current_time: Override for testing

    Returns:
        WeeklyLesson if suitable lesson found and fatigue allows,
        None otherwise (fatigue blocked or no matching article).
    """
    return select_weekly_lesson(
        context=context,
        push_history=push_history,
        available_articles=available_articles,
        trigger=trigger,
        current_time=current_time,
    )


def record_lesson_push(lesson: WeeklyLesson) -> LessonPushRecord:
    """Create a push record for a delivered lesson.

    Caller is responsible for persisting this record.

    Args:
        lesson: The delivered lesson

    Returns:
        LessonPushRecord to be saved.
    """
    return build_push_record(lesson, PushStatus.DELIVERED)


def check_push_allowed(
    push_history: List[LessonPushRecord],
    trigger: LessonTrigger = LessonTrigger.WEEKLY_REGULAR,
    current_time: Optional[float] = None,
) -> Dict[str, Any]:
    """Check if a push is currently allowed (for UI/API status check).

    Args:
        push_history: User's push history
        trigger: What would trigger the push
        current_time: Override for testing

    Returns:
        {allowed: bool, reason: str, week_iso: str, pushes_this_week: int}
    """
    import time as _time
    now = current_time if current_time is not None else _time.time()
    week_iso = get_current_week_iso()
    allowed, reason = check_fatigue(push_history, week_iso, trigger, now)

    from domain.services.education_service import count_pushes_this_week
    count = count_pushes_this_week(push_history, week_iso)

    return {
        "allowed": allowed,
        "reason": reason,
        "week_iso": week_iso,
        "pushes_this_week": count,
        "max_per_week": 2,
    }


def get_lesson_history_summary(
    push_history: List[LessonPushRecord],
) -> Dict[str, Any]:
    """Summarize push history for UI display.

    Returns:
        {total_lessons, weeks_active, last_pushed_at, articles_covered}
    """
    delivered = [r for r in push_history if r.status == PushStatus.DELIVERED]
    articles_covered = list({r.article_id for r in delivered})
    weeks_active = list({r.week_iso for r in delivered})

    last_pushed_at: Optional[float] = None
    if delivered:
        last_pushed_at = max(r.pushed_at for r in delivered)

    return {
        "total_lessons": len(delivered),
        "weeks_active": len(weeks_active),
        "articles_covered": len(articles_covered),
        "article_ids_covered": articles_covered,
        "last_pushed_at": last_pushed_at,
    }
