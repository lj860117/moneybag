"""
Generate Monthly Report Use Case — orchestrates service + store
=================================================================
Orchestrates domain/services/report_service (pure computation) with
domain/rule_engine/decision_archive (data retrieval) to produce and
optionally persist monthly decision quality reports.

Dependency rule: use_cases/ -> domain/ -> infra/ (never backward to api/).

Design doc: docs/design/07-decision-guard.md §四 (M5 复盘归因)
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from domain.services.report_service import (
    build_monthly_report,
    HEALTHY_SCORE_LINE,
)


def generate_monthly_report(
    user_id: str,
    year_month: str,
    previous_avg: Optional[float] = None,
) -> Dict[str, object]:
    """Generate a monthly decision quality report.

    Steps:
      1. Fetch all decision reviews for the user in the given month
      2. Optionally fetch previous month's avg for trend detection
      3. Call domain service to compute report
      4. Return report dict

    Args:
        user_id: user to generate report for
        year_month: target month (e.g., "2026-04")
        previous_avg: previous month's avg score (optional, for trend)

    Returns:
        MonthlyReport as dict.
    """
    from domain.rule_engine.decision_archive import get_decisions, get_archived_decisions

    # Fetch all reviews for the target month
    # Check both hot and cold zones
    hot_decisions = get_decisions(user_id, limit=200)
    cold_decisions = get_archived_decisions(user_id, limit=500)
    all_decisions = hot_decisions + cold_decisions

    # Filter to type="review" and matching year_month
    reviews = [
        d for d in all_decisions
        if d.get("type") == "review"
        and d.get("time", "").startswith(year_month)
    ]

    # If previous_avg not provided, try to compute from previous month
    if previous_avg is None:
        previous_avg = _compute_previous_avg(all_decisions, year_month)

    # Generate report
    report_id = f"rpt_{year_month}_{user_id[:8]}_{int(time.time())}"
    generated_at = datetime.now().isoformat()

    report = build_monthly_report(
        user_id=user_id,
        year_month=year_month,
        reviews=reviews,
        report_id=report_id,
        generated_at=generated_at,
        previous_avg=previous_avg,
    )

    return report.to_dict()


def get_available_months(user_id: str) -> List[str]:
    """Get list of months that have review data available.

    Returns sorted list of year_month strings (e.g., ["2026-03", "2026-04"]).
    """
    from domain.rule_engine.decision_archive import get_decisions, get_archived_decisions

    hot_decisions = get_decisions(user_id, limit=200)
    cold_decisions = get_archived_decisions(user_id, limit=500)
    all_decisions = hot_decisions + cold_decisions

    months: set[str] = set()
    for d in all_decisions:
        if d.get("type") == "review":
            t = d.get("time", "")
            if len(t) >= 7:
                months.add(t[:7])

    return sorted(months)


def get_monthly_report_summary(user_id: str) -> Dict[str, object]:
    """Get a quick summary of available reports and latest stats.

    Returns:
        Dict with available_months, latest_month, latest_avg_score.
    """
    months = get_available_months(user_id)
    if not months:
        return {
            "available_months": [],
            "latest_month": "",
            "latest_avg_score": 0,
            "total_reviews_all_time": 0,
        }

    # Quick stats from latest month
    from domain.rule_engine.decision_archive import get_decisions

    all_decisions = get_decisions(user_id, limit=200)
    reviews = [d for d in all_decisions if d.get("type") == "review"]

    latest_month = months[-1]
    latest_reviews = [
        d for d in reviews
        if d.get("time", "").startswith(latest_month)
    ]

    scores = [
        int(r.get("quality_score", {}).get("total", 0))
        for r in latest_reviews
    ]
    latest_avg = sum(scores) / len(scores) if scores else 0

    return {
        "available_months": months,
        "latest_month": latest_month,
        "latest_avg_score": round(latest_avg, 1),
        "total_reviews_all_time": len(reviews),
    }


# ============================================================
# Private helpers
# ============================================================

def _compute_previous_avg(
    all_decisions: List[Dict[str, Any]],
    current_month: str,
) -> Optional[float]:
    """Compute previous month's average quality score for trend detection."""
    # Parse current month to find previous
    try:
        year = int(current_month[:4])
        month = int(current_month[5:7])
    except (ValueError, IndexError):
        return None

    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    prev_month_str = f"{prev_year:04d}-{prev_month:02d}"

    # Filter previous month's reviews
    prev_reviews = [
        d for d in all_decisions
        if d.get("type") == "review"
        and d.get("time", "").startswith(prev_month_str)
    ]

    if not prev_reviews:
        return None

    scores = [
        int(r.get("quality_score", {}).get("total", 0))
        for r in prev_reviews
    ]
    return sum(scores) / len(scores) if scores else None


__all__ = [
    "generate_monthly_report",
    "get_available_months",
    "get_monthly_report_summary",
]
