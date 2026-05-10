"""
Report Domain Models — Monthly Decision Quality Report
========================================================
Business value objects for the M5 W1-2 monthly decision quality report.

Contents:
  - MotivationDistribution: breakdown of buy reason categories across reviews
  - DecisionPattern: detected recurring pattern (e.g., "涨得好" → loss >10%)
  - QualityTrend: quality score statistics over time
  - MonthlyReport: top-level report aggregating all metrics

All models are frozen dataclasses (immutable facts).
Downstream consumers: use_cases/generate_monthly_report, api/decisions (report endpoint).

Design doc: docs/design/07-decision-guard.md §四
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


# ============================================================
# MotivationDistribution — buy reason category breakdown
# ============================================================

@dataclass(frozen=True)
class MotivationDistribution:
    """Distribution of buy reasons across reviews for a given period.

    Each field is a count of how many reviews included that category.
    A single review can include multiple categories (multi-select).

    Fields:
        fundamental: 基本面 (valuation, sector logic, earnings)
        technical: 技术面 (chart signals, allocation gap)
        emotional: 情绪面 (momentum chase, averaging down)
        follow: 跟风 (friend recommend, FOMO, hot news)
        other: 其他 (DCA plan, custom text)
        total_reasons: total number of individual reasons across all reviews
    """
    fundamental: int = 0
    technical: int = 0
    emotional: int = 0
    follow: int = 0
    other: int = 0
    total_reasons: int = 0

    @property
    def emotional_plus_follow(self) -> int:
        """Combined emotional + follow count (情绪面 + 跟风)."""
        return self.emotional + self.follow

    @property
    def emotional_ratio(self) -> float:
        """Ratio of emotional+follow reasons to total reasons."""
        if self.total_reasons == 0:
            return 0.0
        return self.emotional_plus_follow / self.total_reasons

    def to_dict(self) -> Dict[str, object]:
        return {
            "fundamental": self.fundamental,
            "technical": self.technical,
            "emotional": self.emotional,
            "follow": self.follow,
            "other": self.other,
            "total_reasons": self.total_reasons,
            "emotional_plus_follow": self.emotional_plus_follow,
            "emotional_ratio": round(self.emotional_ratio, 3),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MotivationDistribution":
        return cls(
            fundamental=int(d.get("fundamental", 0)),
            technical=int(d.get("technical", 0)),
            emotional=int(d.get("emotional", 0)),
            follow=int(d.get("follow", 0)),
            other=int(d.get("other", 0)),
            total_reasons=int(d.get("total_reasons", 0)),
        )


# ============================================================
# DecisionPattern — detected recurring pattern
# ============================================================

@dataclass(frozen=True)
class DecisionPattern:
    """A detected recurring decision pattern, potentially harmful.

    Example:
      pattern_id="momentum_chase_loss"
      description="勾选'涨得好'的记录中，60% 后续亏损 >10%"
      trigger_reason_id="momentum_chase"
      occurrence_count=12
      loss_count=7
      loss_ratio=0.583
      avg_loss_pct=-15.2
      severity="high"

    Severity levels:
      high: loss_ratio >= 0.5 and occurrence_count >= 5
      medium: loss_ratio >= 0.3 or occurrence_count >= 3
      low: detected but not statistically significant
    """
    pattern_id: str = ""
    description: str = ""
    trigger_reason_id: str = ""
    occurrence_count: int = 0
    loss_count: int = 0
    loss_ratio: float = 0.0
    avg_loss_pct: float = 0.0
    severity: str = "low"  # high / medium / low

    def to_dict(self) -> Dict[str, object]:
        return {
            "pattern_id": self.pattern_id,
            "description": self.description,
            "trigger_reason_id": self.trigger_reason_id,
            "occurrence_count": self.occurrence_count,
            "loss_count": self.loss_count,
            "loss_ratio": round(self.loss_ratio, 3),
            "avg_loss_pct": round(self.avg_loss_pct, 2),
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionPattern":
        return cls(
            pattern_id=str(d.get("pattern_id", "")),
            description=str(d.get("description", "")),
            trigger_reason_id=str(d.get("trigger_reason_id", "")),
            occurrence_count=int(d.get("occurrence_count", 0)),
            loss_count=int(d.get("loss_count", 0)),
            loss_ratio=float(d.get("loss_ratio", 0.0)),
            avg_loss_pct=float(d.get("avg_loss_pct", 0.0)),
            severity=str(d.get("severity", "low")),
        )


# ============================================================
# QualityTrend — quality score statistics
# ============================================================

@dataclass(frozen=True)
class QualityTrend:
    """Quality score statistics for a reporting period.

    Fields:
        avg_score: average quality score (0-100)
        max_score: highest score in period
        min_score: lowest score in period
        median_score: median score
        score_std: standard deviation (spread)
        above_healthy_line: count of reviews with score >= 60
        below_healthy_line: count with score < 60
        healthy_ratio: ratio above healthy line
        trend_direction: "improving" | "stable" | "declining" (vs previous period)
    """
    avg_score: float = 0.0
    max_score: int = 0
    min_score: int = 0
    median_score: int = 0
    score_std: float = 0.0
    above_healthy_line: int = 0
    below_healthy_line: int = 0
    healthy_ratio: float = 0.0
    trend_direction: str = "stable"  # improving / stable / declining

    def to_dict(self) -> Dict[str, object]:
        return {
            "avg_score": round(self.avg_score, 1),
            "max_score": self.max_score,
            "min_score": self.min_score,
            "median_score": self.median_score,
            "score_std": round(self.score_std, 1),
            "above_healthy_line": self.above_healthy_line,
            "below_healthy_line": self.below_healthy_line,
            "healthy_ratio": round(self.healthy_ratio, 3),
            "trend_direction": self.trend_direction,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QualityTrend":
        return cls(
            avg_score=float(d.get("avg_score", 0.0)),
            max_score=int(d.get("max_score", 0)),
            min_score=int(d.get("min_score", 0)),
            median_score=int(d.get("median_score", 0)),
            score_std=float(d.get("score_std", 0.0)),
            above_healthy_line=int(d.get("above_healthy_line", 0)),
            below_healthy_line=int(d.get("below_healthy_line", 0)),
            healthy_ratio=float(d.get("healthy_ratio", 0.0)),
            trend_direction=str(d.get("trend_direction", "stable")),
        )


# ============================================================
# MonthlyReport — top-level monthly decision quality report
# ============================================================

@dataclass(frozen=True)
class MonthlyReport:
    """Monthly decision quality report — the core M5 deliverable.

    This is the "mirror" that shows the user their decision patterns over time.
    Design doc: 07-decision-guard.md §四 — "让你半年后看清自己的模式"

    Fields:
        report_id: unique identifier
        user_id: report owner
        year_month: e.g., "2026-04"
        generated_at: ISO 8601 timestamp
        total_operations: total number of trades reviewed this month
        checklist_passed: reviews that passed the 7-point checklist (score >= 60)
        checklist_failed: reviews that failed (score < 60)
        motivation_distribution: breakdown of buy reason categories
        quality_trend: score statistics and trend
        patterns: detected harmful patterns
        summary_text: LLM-generated or template summary text
        recommendations: actionable recommendations
    """
    report_id: str = ""
    user_id: str = ""
    year_month: str = ""
    generated_at: str = ""
    total_operations: int = 0
    checklist_passed: int = 0
    checklist_failed: int = 0
    motivation_distribution: MotivationDistribution = field(
        default_factory=MotivationDistribution
    )
    quality_trend: QualityTrend = field(default_factory=QualityTrend)
    patterns: List[DecisionPattern] = field(default_factory=list)
    summary_text: str = ""
    recommendations: List[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        """Ratio of checklist-passed reviews to total."""
        if self.total_operations == 0:
            return 0.0
        return self.checklist_passed / self.total_operations

    def to_dict(self) -> Dict[str, object]:
        return {
            "report_id": self.report_id,
            "user_id": self.user_id,
            "year_month": self.year_month,
            "generated_at": self.generated_at,
            "total_operations": self.total_operations,
            "checklist_passed": self.checklist_passed,
            "checklist_failed": self.checklist_failed,
            "pass_rate": round(self.pass_rate, 3),
            "motivation_distribution": self.motivation_distribution.to_dict(),
            "quality_trend": self.quality_trend.to_dict(),
            "patterns": [p.to_dict() for p in self.patterns],
            "summary_text": self.summary_text,
            "recommendations": self.recommendations,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MonthlyReport":
        motivation_raw = d.get("motivation_distribution", {})
        motivation = (
            MotivationDistribution.from_dict(motivation_raw)
            if motivation_raw
            else MotivationDistribution()
        )
        trend_raw = d.get("quality_trend", {})
        quality_trend = (
            QualityTrend.from_dict(trend_raw) if trend_raw else QualityTrend()
        )
        patterns_raw = d.get("patterns", [])
        patterns = [DecisionPattern.from_dict(p) for p in patterns_raw]

        return cls(
            report_id=str(d.get("report_id", "")),
            user_id=str(d.get("user_id", "")),
            year_month=str(d.get("year_month", "")),
            generated_at=str(d.get("generated_at", "")),
            total_operations=int(d.get("total_operations", 0)),
            checklist_passed=int(d.get("checklist_passed", 0)),
            checklist_failed=int(d.get("checklist_failed", 0)),
            motivation_distribution=motivation,
            quality_trend=quality_trend,
            patterns=patterns,
            summary_text=str(d.get("summary_text", "")),
            recommendations=list(d.get("recommendations", [])),
        )


__all__ = [
    "MotivationDistribution",
    "DecisionPattern",
    "QualityTrend",
    "MonthlyReport",
]
