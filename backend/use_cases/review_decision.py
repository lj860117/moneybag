"""
Review Decision Use Case — Mode B post-trade review orchestration
==================================================================
Orchestrates domain services (decision_guard_service) and persistence
(decision_archive) to submit and retrieve decision reviews.

Dependency rule: use_cases/ -> domain/ -> infra/ (never backward to api/).

Design doc: docs/design/07-decision-guard.md §4
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from domain.models.decision import (
    BuyReason,
    DecisionQualityScore,
    DecisionReview,
    REASON_BY_ID,
)
from domain.services.decision_guard_service import (
    compute_quality_score,
    evaluate_reasons,
    get_predefined_reasons,
)


def submit_decision_review(
    user_id: str,
    asset_code: str,
    asset_name: str,
    action: str,
    amount: float,
    reason_ids: List[str],
    custom_reason_text: str = "",
    trade_time: str = "",
    notes: str = "",
    has_emergency_fund: bool = True,
    concentration_ok: bool = True,
    money_not_needed_3y: bool = True,
    days_since_last_trade: int = 60,
) -> Tuple[DecisionReview, List[str]]:
    """Submit a post-trade decision review.

    Steps:
      1. Parse and validate buy reasons (from predefined IDs + custom text)
      2. Compute quality score from reasons + context
      3. Build DecisionReview record
      4. Return (review, warnings) — caller is responsible for persisting

    Args:
        user_id: user submitting the review
        asset_code: stock/fund code (e.g., "600519")
        asset_name: display name (e.g., "贵州茅台")
        action: buy/sell/add/reduce
        amount: trade amount in CNY
        reason_ids: list of predefined reason IDs selected
        custom_reason_text: optional free-text supplement
        trade_time: when the trade was executed (ISO 8601), defaults to now
        notes: optional notes
        has_emergency_fund: checklist context (from balance sheet)
        concentration_ok: checklist context (from holdings analysis)
        money_not_needed_3y: user self-reported
        days_since_last_trade: computed from decision log

    Returns:
        Tuple of (DecisionReview, warning_messages).
        Warning messages include red/yellow flag alerts.
    """
    warnings: List[str] = []

    # 1. Parse reasons
    reasons: List[BuyReason] = []
    for rid in reason_ids:
        reasons.append(BuyReason.from_predefined(rid))
    if custom_reason_text.strip():
        reasons.append(BuyReason.from_custom(custom_reason_text.strip()))

    # Validate: at least 1 reason required
    if not reasons:
        warnings.append("未选择任何买入理由，默认评分较低")

    # 2. Compute quality score
    quality_score = compute_quality_score(
        reasons=reasons,
        has_emergency_fund=has_emergency_fund,
        concentration_ok=concentration_ok,
        money_not_needed_3y=money_not_needed_3y,
        days_since_last_trade=days_since_last_trade,
    )

    # 3. Evaluate reason signals → generate warnings
    red_count, yellow_count, signal_messages = evaluate_reasons(reasons)
    warnings.extend(signal_messages)

    if quality_score.grade == "poor":
        warnings.append("⚠️ 决策质量评分较低，建议回顾你的投资计划")

    # 4. Build review record
    review_id = f"rev_{int(time.time())}_{user_id[:8]}"
    now = datetime.now().isoformat()

    review = DecisionReview(
        id=review_id,
        user_id=user_id,
        trade_time=trade_time or now,
        review_time=now,
        asset_code=asset_code,
        asset_name=asset_name,
        action=action,
        amount=amount,
        reasons=reasons,
        quality_score=quality_score,
        notes=notes,
    )

    return review, warnings


def save_review_to_archive(
    user_id: str,
    review: DecisionReview,
) -> Dict[str, object]:
    """Persist review to decision archive (hot zone).

    Delegates to domain.rule_engine.decision_archive.add_decision().
    This bridges the new review system into the existing archive system.
    """
    from domain.rule_engine.decision_archive import add_decision

    # Transform review into the decision archive format
    record = {
        "id": review.id,
        "type": "review",
        "action": review.action,
        "summary": f"{review.action} {review.asset_name}({review.asset_code}) ¥{review.amount:.0f}",
        "asset_code": review.asset_code,
        "asset_name": review.asset_name,
        "amount": review.amount,
        "reasons": [r.to_dict() for r in review.reasons],
        "quality_score": review.quality_score.to_dict(),
        "trade_time": review.trade_time,
        "notes": review.notes,
    }

    saved = add_decision(user_id, record)
    return saved


def get_user_reviews(
    user_id: str,
    limit: int = 20,
) -> List[Dict[str, object]]:
    """Get recent reviews for a user from the archive.

    Filters decision archive for type="review" entries.
    """
    from domain.rule_engine.decision_archive import get_decisions

    all_decisions = get_decisions(user_id, limit=limit * 2)
    reviews = [d for d in all_decisions if d.get("type") == "review"]
    return reviews[:limit]


def get_review_statistics(user_id: str) -> Dict[str, object]:
    """Compute aggregate review statistics for a user.

    Returns:
        avg_score, total_reviews, red_flag_total, grade_distribution, etc.
    """
    from domain.rule_engine.decision_archive import get_decisions

    all_decisions = get_decisions(user_id, limit=200)
    reviews = [d for d in all_decisions if d.get("type") == "review"]

    if not reviews:
        return {
            "total_reviews": 0,
            "avg_score": 0,
            "avg_grade": "N/A",
            "red_flag_total": 0,
            "yellow_flag_total": 0,
            "grade_distribution": {"excellent": 0, "good": 0, "mediocre": 0, "poor": 0},
        }

    scores = []
    red_total = 0
    yellow_total = 0
    grades: Dict[str, int] = {"excellent": 0, "good": 0, "mediocre": 0, "poor": 0}

    for rev in reviews:
        qs = rev.get("quality_score", {})
        total = qs.get("total", 0)
        scores.append(total)
        red_total += qs.get("red_flags", 0)
        yellow_total += qs.get("yellow_flags", 0)
        grade = qs.get("grade", "poor")
        if grade in grades:
            grades[grade] += 1

    avg_score = sum(scores) / len(scores) if scores else 0
    avg_grade = _score_to_grade(int(avg_score))

    return {
        "total_reviews": len(reviews),
        "avg_score": round(avg_score, 1),
        "avg_grade": avg_grade,
        "red_flag_total": red_total,
        "yellow_flag_total": yellow_total,
        "grade_distribution": grades,
    }


def _score_to_grade(score: int) -> str:
    """Map score to grade string."""
    if score >= 80:
        return "excellent"
    elif score >= 60:
        return "good"
    elif score >= 40:
        return "mediocre"
    else:
        return "poor"


__all__ = [
    "submit_decision_review",
    "save_review_to_archive",
    "get_user_reviews",
    "get_review_statistics",
]
