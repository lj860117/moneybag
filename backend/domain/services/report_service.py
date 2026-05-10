"""
Report Service — Monthly Decision Quality Report computation (pure functions)
==============================================================================
Computes monthly report statistics from decision review records.

Functions:
  - compute_motivation_distribution() — categorize reasons across reviews
  - compute_quality_trend() — score statistics (avg/max/min/median/std)
  - detect_loss_patterns() — identify recurring harmful patterns
  - generate_recommendations() — produce actionable advice
  - build_monthly_report() — orchestrate all computations into MonthlyReport

All functions are pure (no side effects, no I/O). Data fetching via caller.

Design doc: docs/design/07-decision-guard.md §四
Invariant #10: domain/ layer has zero imports from infra/.
Invariant #9: domain/services do not cross-import (only domain/models).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from domain.models.report import (
    DecisionPattern,
    MonthlyReport,
    MotivationDistribution,
    QualityTrend,
)


# ============================================================
# Constants
# ============================================================

HEALTHY_SCORE_LINE = 60  # Reviews with score >= 60 are considered "passed"
PATTERN_MIN_OCCURRENCES = 3  # Minimum occurrences to flag a pattern
LOSS_THRESHOLD_PCT = 10.0  # Loss > 10% considered significant


# ============================================================
# Public API
# ============================================================

def compute_motivation_distribution(
    reviews: List[Dict[str, Any]],
) -> MotivationDistribution:
    """Compute motivation (buy reason) distribution across reviews.

    Reads 'reasons' array from each review record. Each reason has a 'category'.
    A single review may contribute to multiple categories.

    Args:
        reviews: list of review dicts from decision_archive (type="review")

    Returns:
        MotivationDistribution with counts per category.
    """
    fundamental = 0
    technical = 0
    emotional = 0
    follow = 0
    other = 0
    total_reasons = 0

    for review in reviews:
        reasons = review.get("reasons", [])
        for reason in reasons:
            total_reasons += 1
            cat = reason.get("category", "other")
            if cat == "fundamental":
                fundamental += 1
            elif cat == "technical":
                technical += 1
            elif cat == "emotional":
                emotional += 1
            elif cat == "follow":
                follow += 1
            else:
                other += 1

    return MotivationDistribution(
        fundamental=fundamental,
        technical=technical,
        emotional=emotional,
        follow=follow,
        other=other,
        total_reasons=total_reasons,
    )


def compute_quality_trend(
    reviews: List[Dict[str, Any]],
    previous_avg: Optional[float] = None,
) -> QualityTrend:
    """Compute quality score statistics for a set of reviews.

    Args:
        reviews: list of review dicts with 'quality_score.total' field
        previous_avg: previous period's average score (for trend detection)

    Returns:
        QualityTrend with statistics and trend direction.
    """
    if not reviews:
        return QualityTrend()

    scores: List[int] = []
    for review in reviews:
        qs = review.get("quality_score", {})
        total = int(qs.get("total", 0))
        scores.append(total)

    if not scores:
        return QualityTrend()

    scores_sorted = sorted(scores)
    n = len(scores_sorted)
    avg_score = sum(scores) / n
    max_score = scores_sorted[-1]
    min_score = scores_sorted[0]

    # Median
    if n % 2 == 0:
        median_score = (scores_sorted[n // 2 - 1] + scores_sorted[n // 2]) // 2
    else:
        median_score = scores_sorted[n // 2]

    # Standard deviation
    variance = sum((s - avg_score) ** 2 for s in scores) / n
    score_std = math.sqrt(variance)

    # Healthy line classification
    above = sum(1 for s in scores if s >= HEALTHY_SCORE_LINE)
    below = n - above
    healthy_ratio = above / n if n > 0 else 0.0

    # Trend direction
    trend_direction = "stable"
    if previous_avg is not None:
        diff = avg_score - previous_avg
        if diff >= 5.0:
            trend_direction = "improving"
        elif diff <= -5.0:
            trend_direction = "declining"

    return QualityTrend(
        avg_score=avg_score,
        max_score=max_score,
        min_score=min_score,
        median_score=median_score,
        score_std=score_std,
        above_healthy_line=above,
        below_healthy_line=below,
        healthy_ratio=healthy_ratio,
        trend_direction=trend_direction,
    )


def detect_loss_patterns(
    reviews: List[Dict[str, Any]],
) -> List[DecisionPattern]:
    """Detect recurring harmful decision patterns.

    Primary pattern: reviews with specific reason_ids that correlate with
    subsequent losses > 10%.

    Logic:
      For each predefined reason_id that has a red signal:
        1. Count how many reviews selected that reason
        2. Of those, count how many have result_tracked=True and loss > 10%
        3. If occurrence >= PATTERN_MIN_OCCURRENCES and loss_ratio >= 0.3, flag it

    Args:
        reviews: list of review dicts (may include 'result' field with post-trade outcome)

    Returns:
        List of DecisionPattern sorted by severity (high first).
    """
    # Red-signal reason_ids to monitor
    monitored_reasons = [
        ("momentum_chase", "涨得好/势头猛"),
        ("hot_news", "热搜/新闻/朋友推荐"),
        ("fomo", "其他人都在买，怕错过"),
        ("averaging_down", "前期亏损想摊平成本"),
    ]

    patterns: List[DecisionPattern] = []

    for reason_id, label in monitored_reasons:
        # Find reviews that include this reason
        matching_reviews: List[Dict[str, Any]] = []
        for review in reviews:
            reasons = review.get("reasons", [])
            for reason in reasons:
                if reason.get("reason_id") == reason_id:
                    matching_reviews.append(review)
                    break

        occurrence_count = len(matching_reviews)
        if occurrence_count < PATTERN_MIN_OCCURRENCES:
            continue

        # Check loss correlation
        loss_count = 0
        loss_pcts: List[float] = []
        for review in matching_reviews:
            result = review.get("result", {})
            if not result:
                continue
            # result may have: {"tracked": True, "return_pct": -15.0, "win": False}
            if result.get("tracked") or review.get("result_tracked"):
                return_pct = float(result.get("return_pct", 0.0))
                if return_pct < -LOSS_THRESHOLD_PCT:
                    loss_count += 1
                    loss_pcts.append(return_pct)

        # Compute metrics
        tracked_count = sum(
            1 for r in matching_reviews
            if r.get("result_tracked") or (r.get("result") and r["result"].get("tracked"))
        )
        loss_ratio = loss_count / tracked_count if tracked_count > 0 else 0.0
        avg_loss_pct = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0.0

        # Determine severity
        if loss_ratio >= 0.5 and occurrence_count >= 5:
            severity = "high"
        elif loss_ratio >= 0.3 or occurrence_count >= 5:
            severity = "medium"
        else:
            severity = "low"

        # Only report patterns with meaningful loss correlation
        if loss_ratio >= 0.3 or (occurrence_count >= 5 and loss_count > 0):
            pattern_desc = (
                f"勾选'{label}'的 {occurrence_count} 次操作中，"
                f"{loss_count} 次后续亏损 >{LOSS_THRESHOLD_PCT:.0f}%"
                f"（占比 {loss_ratio * 100:.0f}%）"
            )
            patterns.append(
                DecisionPattern(
                    pattern_id=f"{reason_id}_loss",
                    description=pattern_desc,
                    trigger_reason_id=reason_id,
                    occurrence_count=occurrence_count,
                    loss_count=loss_count,
                    loss_ratio=loss_ratio,
                    avg_loss_pct=avg_loss_pct,
                    severity=severity,
                )
            )

    # Sort by severity (high > medium > low)
    severity_order = {"high": 0, "medium": 1, "low": 2}
    patterns.sort(key=lambda p: severity_order.get(p.severity, 3))

    return patterns


def generate_recommendations(
    distribution: MotivationDistribution,
    trend: QualityTrend,
    patterns: List[DecisionPattern],
) -> List[str]:
    """Generate actionable recommendations based on report data.

    Rules:
      1. If emotional_ratio > 40%, suggest reducing emotional decisions
      2. If avg_score < 50, suggest reviewing investment plan
      3. If high-severity pattern exists, highlight specific pattern
      4. If trend is declining, suggest pause and reflection
      5. If healthy_ratio > 0.7, positive reinforcement

    Returns:
        List of recommendation strings (Chinese).
    """
    recommendations: List[str] = []

    # 1. High emotional ratio
    if distribution.emotional_ratio > 0.4:
        ratio_pct = distribution.emotional_ratio * 100
        recommendations.append(
            f"情绪面+跟风理由占比 {ratio_pct:.0f}%，建议操作前先等待 24 小时冷静期"
        )

    # 2. Low average score
    if trend.avg_score > 0 and trend.avg_score < 50:
        recommendations.append(
            f"本月平均决策质量分 {trend.avg_score:.0f}/100（低于健康线 60），"
            f"建议回顾你的投资计划和目标配比"
        )

    # 3. High-severity patterns
    for pattern in patterns:
        if pattern.severity == "high":
            recommendations.append(
                f"高风险模式：{pattern.description}，"
                f"建议将'{pattern.trigger_reason_id}'标记为个人禁区"
            )

    # 4. Declining trend
    if trend.trend_direction == "declining":
        recommendations.append(
            "决策质量较上月下滑，建议暂停操作 1-2 周，重新审视投资逻辑"
        )

    # 5. Positive reinforcement
    if trend.healthy_ratio > 0.7 and trend.avg_score >= 60:
        recommendations.append(
            f"本月 {trend.healthy_ratio * 100:.0f}% 的决策在健康线以上，保持！"
        )

    # Default: if no specific recommendations
    if not recommendations:
        recommendations.append(
            "继续记录每笔操作理由，数据积累越多，行为模式分析越准确"
        )

    return recommendations


def build_monthly_report(
    user_id: str,
    year_month: str,
    reviews: List[Dict[str, Any]],
    report_id: str = "",
    generated_at: str = "",
    previous_avg: Optional[float] = None,
) -> MonthlyReport:
    """Orchestrate all computations to build a MonthlyReport.

    This is the main entry point for report generation.
    Pure function — all data must be passed in.

    Args:
        user_id: report owner
        year_month: e.g., "2026-04"
        reviews: list of review dicts from decision_archive for this month
        report_id: unique report ID (caller generates)
        generated_at: ISO 8601 timestamp (caller provides)
        previous_avg: previous month's avg score for trend detection

    Returns:
        MonthlyReport with all computed metrics.
    """
    total_operations = len(reviews)

    # Classify passed/failed
    checklist_passed = 0
    checklist_failed = 0
    for review in reviews:
        qs = review.get("quality_score", {})
        total = int(qs.get("total", 0))
        if total >= HEALTHY_SCORE_LINE:
            checklist_passed += 1
        else:
            checklist_failed += 1

    # Compute sub-metrics
    distribution = compute_motivation_distribution(reviews)
    trend = compute_quality_trend(reviews, previous_avg=previous_avg)
    patterns = detect_loss_patterns(reviews)
    recommendations = generate_recommendations(distribution, trend, patterns)

    # Generate summary text
    summary_text = _build_summary_text(
        total_operations=total_operations,
        checklist_passed=checklist_passed,
        distribution=distribution,
        trend=trend,
        patterns=patterns,
    )

    return MonthlyReport(
        report_id=report_id,
        user_id=user_id,
        year_month=year_month,
        generated_at=generated_at,
        total_operations=total_operations,
        checklist_passed=checklist_passed,
        checklist_failed=checklist_failed,
        motivation_distribution=distribution,
        quality_trend=trend,
        patterns=patterns,
        summary_text=summary_text,
        recommendations=recommendations,
    )


# ============================================================
# Private helpers
# ============================================================

def _build_summary_text(
    total_operations: int,
    checklist_passed: int,
    distribution: MotivationDistribution,
    trend: QualityTrend,
    patterns: List[DecisionPattern],
) -> str:
    """Build a human-readable summary text for the report."""
    parts: List[str] = []

    # Basic stats
    parts.append(
        f"本月共 {total_operations} 次操作，"
        f"其中 {checklist_passed} 次通过质量检查"
        f"（通过率 {checklist_passed / total_operations * 100:.0f}%）"
        if total_operations > 0
        else "本月无操作记录"
    )

    # Score summary
    if trend.avg_score > 0:
        parts.append(
            f"平均决策质量分 {trend.avg_score:.0f}/100"
            f"（{_score_to_grade_zh(trend.avg_score)}）"
        )

    # Emotional ratio alert
    if distribution.emotional_ratio > 0.4:
        parts.append(
            f"⚠️ 情绪面理由占比 {distribution.emotional_ratio * 100:.0f}%，"
            f"超过 40% 警戒线"
        )

    # High-severity patterns
    high_patterns = [p for p in patterns if p.severity == "high"]
    if high_patterns:
        for p in high_patterns[:2]:  # max 2 highlights
            parts.append(f"🚨 {p.description}")

    # Trend
    if trend.trend_direction == "improving":
        parts.append("📈 决策质量较上月提升")
    elif trend.trend_direction == "declining":
        parts.append("📉 决策质量较上月下滑")

    return "；".join(parts) + "。"


def _score_to_grade_zh(score: float) -> str:
    """Map score to Chinese grade label."""
    if score >= 80:
        return "优秀"
    elif score >= 60:
        return "良好"
    elif score >= 40:
        return "一般"
    else:
        return "较差"


__all__ = [
    "compute_motivation_distribution",
    "compute_quality_trend",
    "detect_loss_patterns",
    "generate_recommendations",
    "build_monthly_report",
    "HEALTHY_SCORE_LINE",
    "PATTERN_MIN_OCCURRENCES",
    "LOSS_THRESHOLD_PCT",
]
