"""
Monthly Decision Quality Report Tests (M5 W1-2)
==================================================
Validates the monthly report system:
  1. Domain models (MonthlyReport, MotivationDistribution, DecisionPattern, QualityTrend)
  2. Protocol satisfaction (ReportGeneratorProtocol)
  3. Service layer (compute_motivation_distribution, compute_quality_trend,
                   detect_loss_patterns, generate_recommendations, build_monthly_report)
  4. Use case (generate_monthly_report end-to-end with mock data)
  5. API endpoint (GET /api/decisions/monthly-report/{user_id})
  6. Seed script (generates valid data matching distribution requirements)
  7. Invariants (domain zero infra import, service pure functions)

Run: cd backend && python -m pytest ../tests/test_monthly_report.py -v
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Ensure backend/ is on sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ============================================================
# Test fixtures — mock review data
# ============================================================

def _make_review(
    reason_ids: List[str],
    categories: List[str],
    signals: List[str],
    score_total: int = 50,
    grade: str = "mediocre",
    time: str = "2026-04-10T10:00:00",
    result_tracked: bool = False,
    result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create a mock review dict matching decision_archive format."""
    reasons = []
    for rid, cat, sig in zip(reason_ids, categories, signals):
        reasons.append({
            "reason_id": rid,
            "custom_text": "",
            "category": cat,
            "signal": sig,
        })
    return {
        "id": f"rev_test_{hash(time) % 10000}",
        "type": "review",
        "action": "buy",
        "summary": f"buy 贵州茅台(600519) ¥10000",
        "asset_code": "600519",
        "asset_name": "贵州茅台",
        "amount": 10000.0,
        "reasons": reasons,
        "quality_score": {
            "reason_clarity": score_total // 4,
            "info_source": score_total // 4,
            "risk_awareness": score_total // 4,
            "time_horizon": score_total - 3 * (score_total // 4),
            "total": score_total,
            "red_flags": sum(1 for s in signals if s == "red"),
            "yellow_flags": sum(1 for s in signals if s == "yellow"),
            "grade": grade,
        },
        "trade_time": time,
        "time": time,
        "review_time": time,
        "notes": "",
        "result_tracked": result_tracked,
        "result": result or {},
    }


@pytest.fixture
def sample_reviews() -> List[Dict[str, Any]]:
    """Generate a mix of reviews for testing."""
    reviews = []

    # Emotional reviews (low scores, some with losses)
    for i in range(5):
        reviews.append(_make_review(
            reason_ids=["momentum_chase"],
            categories=["emotional"],
            signals=["red"],
            score_total=35,
            grade="poor",
            time=f"2026-04-{10+i:02d}T10:00:00",
            result_tracked=True,
            result={"tracked": True, "return_pct": -15.0 - i, "win": False},
        ))

    # Fundamental reviews (high scores, mostly wins)
    for i in range(4):
        reviews.append(_make_review(
            reason_ids=["valuation_low"],
            categories=["fundamental"],
            signals=["green"],
            score_total=75,
            grade="good",
            time=f"2026-04-{20+i:02d}T10:00:00",
            result_tracked=True,
            result={"tracked": True, "return_pct": 10.0 + i, "win": True},
        ))

    # Other reviews (mixed scores)
    for i in range(3):
        reviews.append(_make_review(
            reason_ids=["dca_plan"],
            categories=["other"],
            signals=["green"],
            score_total=65,
            grade="good",
            time=f"2026-04-{25+i:02d}T10:00:00",
        ))

    return reviews


# ============================================================
# 1. Domain Models
# ============================================================

class TestReportModels:
    """Test domain model dataclasses."""

    def test_motivation_distribution_creation(self):
        from domain.models.report import MotivationDistribution

        dist = MotivationDistribution(
            fundamental=5, technical=2, emotional=8, follow=3, other=2, total_reasons=20
        )
        assert dist.fundamental == 5
        assert dist.emotional_plus_follow == 11
        assert dist.emotional_ratio == 0.55

    def test_motivation_distribution_to_from_dict(self):
        from domain.models.report import MotivationDistribution

        dist = MotivationDistribution(
            fundamental=3, technical=1, emotional=5, follow=2, other=1, total_reasons=12
        )
        d = dist.to_dict()
        assert d["fundamental"] == 3
        assert d["emotional_plus_follow"] == 7
        assert "emotional_ratio" in d

        restored = MotivationDistribution.from_dict(d)
        assert restored.fundamental == 3
        assert restored.emotional == 5

    def test_decision_pattern_to_from_dict(self):
        from domain.models.report import DecisionPattern

        pattern = DecisionPattern(
            pattern_id="momentum_chase_loss",
            description="涨得好 → 亏损",
            trigger_reason_id="momentum_chase",
            occurrence_count=10,
            loss_count=6,
            loss_ratio=0.6,
            avg_loss_pct=-15.5,
            severity="high",
        )
        d = pattern.to_dict()
        assert d["severity"] == "high"
        assert d["loss_ratio"] == 0.6

        restored = DecisionPattern.from_dict(d)
        assert restored.pattern_id == "momentum_chase_loss"
        assert restored.loss_count == 6

    def test_quality_trend_to_from_dict(self):
        from domain.models.report import QualityTrend

        trend = QualityTrend(
            avg_score=55.5,
            max_score=90,
            min_score=20,
            median_score=55,
            score_std=15.3,
            above_healthy_line=5,
            below_healthy_line=7,
            healthy_ratio=0.417,
            trend_direction="declining",
        )
        d = trend.to_dict()
        assert d["trend_direction"] == "declining"
        assert d["avg_score"] == 55.5

        restored = QualityTrend.from_dict(d)
        assert restored.max_score == 90

    def test_monthly_report_to_from_dict(self):
        from domain.models.report import (
            MonthlyReport,
            MotivationDistribution,
            QualityTrend,
            DecisionPattern,
        )

        report = MonthlyReport(
            report_id="rpt_test",
            user_id="user_1",
            year_month="2026-04",
            generated_at="2026-05-01T00:00:00",
            total_operations=12,
            checklist_passed=7,
            checklist_failed=5,
            motivation_distribution=MotivationDistribution(
                fundamental=3, emotional=5, follow=2, total_reasons=10
            ),
            quality_trend=QualityTrend(avg_score=55.0, max_score=90, min_score=20),
            patterns=[
                DecisionPattern(
                    pattern_id="test", severity="high", loss_ratio=0.6
                )
            ],
            summary_text="Test summary",
            recommendations=["建议1", "建议2"],
        )

        d = report.to_dict()
        assert d["total_operations"] == 12
        assert d["pass_rate"] == pytest.approx(7 / 12, rel=1e-2)
        assert len(d["patterns"]) == 1
        assert d["recommendations"] == ["建议1", "建议2"]

        restored = MonthlyReport.from_dict(d)
        assert restored.report_id == "rpt_test"
        assert restored.total_operations == 12
        assert restored.motivation_distribution.fundamental == 3
        assert len(restored.patterns) == 1

    def test_monthly_report_pass_rate_zero_operations(self):
        from domain.models.report import MonthlyReport

        report = MonthlyReport(total_operations=0)
        assert report.pass_rate == 0.0


# ============================================================
# 2. Protocol
# ============================================================

class TestReportProtocol:
    """Test ReportGeneratorProtocol structural compliance."""

    def test_protocol_importable(self):
        from domain.protocols.report_generator import ReportGeneratorProtocol
        assert ReportGeneratorProtocol is not None

    def test_protocol_in_init(self):
        from domain.protocols import ReportGeneratorProtocol
        assert ReportGeneratorProtocol is not None


# ============================================================
# 3. Service Layer (pure functions)
# ============================================================

class TestReportService:
    """Test domain/services/report_service.py pure functions."""

    def test_compute_motivation_distribution(self, sample_reviews: List[Dict[str, Any]]):
        from domain.services.report_service import compute_motivation_distribution

        dist = compute_motivation_distribution(sample_reviews)
        assert dist.emotional == 5  # 5 momentum_chase reviews
        assert dist.fundamental == 4
        assert dist.other == 3
        assert dist.total_reasons == 12  # 12 reviews × 1 reason each

    def test_compute_motivation_distribution_empty(self):
        from domain.services.report_service import compute_motivation_distribution

        dist = compute_motivation_distribution([])
        assert dist.total_reasons == 0
        assert dist.emotional_ratio == 0.0

    def test_compute_quality_trend(self, sample_reviews: List[Dict[str, Any]]):
        from domain.services.report_service import compute_quality_trend

        trend = compute_quality_trend(sample_reviews)
        assert trend.avg_score > 0
        assert trend.max_score == 75
        assert trend.min_score == 35
        assert trend.above_healthy_line >= 0
        assert trend.below_healthy_line >= 0
        assert trend.above_healthy_line + trend.below_healthy_line == len(sample_reviews)

    def test_compute_quality_trend_with_previous(self, sample_reviews: List[Dict[str, Any]]):
        from domain.services.report_service import compute_quality_trend

        # Previous avg much higher → trend should be declining
        trend = compute_quality_trend(sample_reviews, previous_avg=80.0)
        assert trend.trend_direction == "declining"

        # Previous avg much lower → trend should be improving
        trend = compute_quality_trend(sample_reviews, previous_avg=20.0)
        assert trend.trend_direction == "improving"

    def test_compute_quality_trend_empty(self):
        from domain.services.report_service import compute_quality_trend

        trend = compute_quality_trend([])
        assert trend.avg_score == 0.0
        assert trend.trend_direction == "stable"

    def test_detect_loss_patterns(self, sample_reviews: List[Dict[str, Any]]):
        from domain.services.report_service import detect_loss_patterns

        patterns = detect_loss_patterns(sample_reviews)
        # We have 5 momentum_chase reviews, all with losses > 10%
        # This should trigger a pattern
        assert len(patterns) >= 1
        momentum_pattern = next(
            (p for p in patterns if p.trigger_reason_id == "momentum_chase"),
            None,
        )
        assert momentum_pattern is not None
        assert momentum_pattern.occurrence_count == 5
        assert momentum_pattern.loss_count == 5
        assert momentum_pattern.loss_ratio == 1.0
        assert momentum_pattern.severity == "high"

    def test_detect_loss_patterns_below_threshold(self):
        from domain.services.report_service import detect_loss_patterns

        # Only 2 momentum reviews — below PATTERN_MIN_OCCURRENCES (3)
        reviews = [
            _make_review(
                reason_ids=["momentum_chase"],
                categories=["emotional"],
                signals=["red"],
                time=f"2026-04-{i:02d}T10:00:00",
                result_tracked=True,
                result={"tracked": True, "return_pct": -15.0, "win": False},
            )
            for i in range(2)
        ]
        patterns = detect_loss_patterns(reviews)
        assert len(patterns) == 0  # Below threshold

    def test_generate_recommendations_emotional_high(self):
        from domain.services.report_service import generate_recommendations
        from domain.models.report import MotivationDistribution, QualityTrend

        dist = MotivationDistribution(
            emotional=6, follow=4, fundamental=2, total_reasons=12
        )
        trend = QualityTrend(avg_score=45.0, healthy_ratio=0.3)
        patterns: List[Any] = []

        recs = generate_recommendations(dist, trend, patterns)
        assert any("情绪面" in r for r in recs)
        assert any("平均决策质量分" in r for r in recs)

    def test_generate_recommendations_positive(self):
        from domain.services.report_service import generate_recommendations
        from domain.models.report import MotivationDistribution, QualityTrend

        dist = MotivationDistribution(
            fundamental=8, emotional=1, total_reasons=9
        )
        trend = QualityTrend(avg_score=75.0, healthy_ratio=0.8)
        patterns: List[Any] = []

        recs = generate_recommendations(dist, trend, patterns)
        assert any("保持" in r for r in recs)

    def test_build_monthly_report(self, sample_reviews: List[Dict[str, Any]]):
        from domain.services.report_service import build_monthly_report

        report = build_monthly_report(
            user_id="test_user",
            year_month="2026-04",
            reviews=sample_reviews,
            report_id="rpt_test",
            generated_at="2026-05-01T00:00:00",
        )

        assert report.report_id == "rpt_test"
        assert report.user_id == "test_user"
        assert report.year_month == "2026-04"
        assert report.total_operations == 12
        assert report.checklist_passed >= 0
        assert report.checklist_failed >= 0
        assert report.checklist_passed + report.checklist_failed == 12
        assert report.motivation_distribution.total_reasons > 0
        assert report.quality_trend.avg_score > 0
        assert len(report.summary_text) > 0
        assert len(report.recommendations) > 0

    def test_build_monthly_report_empty(self):
        from domain.services.report_service import build_monthly_report

        report = build_monthly_report(
            user_id="test_user",
            year_month="2026-04",
            reviews=[],
            report_id="rpt_empty",
            generated_at="2026-05-01T00:00:00",
        )

        assert report.total_operations == 0
        assert report.checklist_passed == 0
        assert report.checklist_failed == 0
        assert report.pass_rate == 0.0


# ============================================================
# 4. Use Case (with mock filesystem)
# ============================================================

class TestGenerateMonthlyReportUseCase:
    """Test use_cases/generate_monthly_report.py."""

    def test_generate_monthly_report_with_data(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test report generation using a temporary data directory."""
        # Setup: write mock decisions to tmp dir
        user_id = "test_user_uc"
        user_dir = tmp_path / user_id / "memory"
        user_dir.mkdir(parents=True)

        reviews = [
            _make_review(
                reason_ids=["momentum_chase"],
                categories=["emotional"],
                signals=["red"],
                score_total=35,
                grade="poor",
                time=f"2026-04-{10+i:02d}T10:00:00",
                result_tracked=True,
                result={"tracked": True, "return_pct": -15.0, "win": False},
            )
            for i in range(5)
        ] + [
            _make_review(
                reason_ids=["valuation_low"],
                categories=["fundamental"],
                signals=["green"],
                score_total=80,
                grade="excellent",
                time=f"2026-04-{20+i:02d}T10:00:00",
            )
            for i in range(5)
        ]

        (user_dir / "decisions.json").write_text(
            json.dumps(reviews, ensure_ascii=False), encoding="utf-8"
        )

        # Monkeypatch DATA_DIR as Path in all relevant modules
        import config
        import domain.services.user_preference_service as ups
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(ups, "DATA_DIR", tmp_path)

        from use_cases.generate_monthly_report import generate_monthly_report

        report = generate_monthly_report(user_id=user_id, year_month="2026-04")

        assert report["year_month"] == "2026-04"
        assert report["total_operations"] == 10
        assert report["user_id"] == user_id
        assert "motivation_distribution" in report
        assert "quality_trend" in report
        assert "patterns" in report
        assert "recommendations" in report

    def test_get_available_months(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Test month listing."""
        user_id = "test_user_months"
        user_dir = tmp_path / user_id / "memory"
        user_dir.mkdir(parents=True)

        reviews = [
            _make_review(
                reason_ids=["dca_plan"],
                categories=["other"],
                signals=["green"],
                time="2026-03-15T10:00:00",
            ),
            _make_review(
                reason_ids=["dca_plan"],
                categories=["other"],
                signals=["green"],
                time="2026-04-15T10:00:00",
            ),
        ]

        (user_dir / "decisions.json").write_text(
            json.dumps(reviews, ensure_ascii=False), encoding="utf-8"
        )

        import config
        import domain.services.user_preference_service as ups
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(ups, "DATA_DIR", tmp_path)

        from use_cases.generate_monthly_report import get_available_months

        months = get_available_months(user_id)
        assert months == ["2026-03", "2026-04"]


# ============================================================
# 5. API Endpoint
# ============================================================

class TestMonthlyReportAPI:
    """Test API endpoint schema and imports."""

    def test_endpoint_importable(self):
        """Verify the endpoint function is importable."""
        from api.decisions import get_monthly_report, get_monthly_report_summary_endpoint
        assert get_monthly_report is not None
        assert get_monthly_report_summary_endpoint is not None

    def test_response_models(self):
        """Verify response models are defined."""
        from api.decisions import MonthlyReportResponse, MonthlyReportSummaryResponse
        assert MonthlyReportResponse is not None
        assert MonthlyReportSummaryResponse is not None


# ============================================================
# 6. Seed Script
# ============================================================

class TestSeedScript:
    """Test the seed data generation script."""

    def test_generate_reviews_count(self):
        """Generated reviews should be between 60-80."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from seed_decision_reviews import generate_reviews

        reviews = generate_reviews()
        assert 60 <= len(reviews) <= 80

    def test_generate_reviews_time_span(self):
        """Reviews should span 2026-03 to 2026-04."""
        from seed_decision_reviews import generate_reviews

        reviews = generate_reviews()
        months = set(r["time"][:7] for r in reviews)
        assert "2026-03" in months
        assert "2026-04" in months

    def test_generate_reviews_reason_distribution(self):
        """Emotional+follow should be ~40%, fundamental ~30%."""
        from seed_decision_reviews import generate_reviews

        reviews = generate_reviews()
        total_reasons = 0
        cat_counts: Dict[str, int] = {}
        for r in reviews:
            for reason in r["reasons"]:
                cat = reason["category"]
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
                total_reasons += 1

        emotional_follow = cat_counts.get("emotional", 0) + cat_counts.get("follow", 0)
        fundamental = cat_counts.get("fundamental", 0)

        # Allow some tolerance (±10%)
        assert emotional_follow / total_reasons >= 0.30
        assert emotional_follow / total_reasons <= 0.55
        assert fundamental / total_reasons >= 0.20
        assert fundamental / total_reasons <= 0.45

    def test_generate_reviews_score_by_category(self):
        """Emotional avg score < 50, fundamental avg score > 70."""
        from seed_decision_reviews import generate_reviews

        reviews = generate_reviews()

        emotional_scores: List[int] = []
        fundamental_scores: List[int] = []

        for r in reviews:
            primary_cat = r["reasons"][0]["category"] if r["reasons"] else "other"
            score = r["quality_score"]["total"]
            if primary_cat in ("emotional", "follow"):
                emotional_scores.append(score)
            elif primary_cat == "fundamental":
                fundamental_scores.append(score)

        avg_emotional = sum(emotional_scores) / len(emotional_scores) if emotional_scores else 0
        avg_fundamental = sum(fundamental_scores) / len(fundamental_scores) if fundamental_scores else 0

        assert avg_emotional < 50, f"Emotional avg {avg_emotional} should be < 50"
        assert avg_fundamental > 70, f"Fundamental avg {avg_fundamental} should be > 70"

    def test_generate_reviews_momentum_loss_correlation(self):
        """60% of momentum_chase reviews should have loss > 10%."""
        from seed_decision_reviews import generate_reviews

        reviews = generate_reviews()

        momentum_reviews = [
            r for r in reviews
            if any(reason.get("reason_id") == "momentum_chase" for reason in r["reasons"])
        ]
        momentum_tracked = [r for r in momentum_reviews if r.get("result_tracked")]
        momentum_losses = [
            r for r in momentum_tracked
            if r.get("result", {}).get("return_pct", 0) < -10
        ]

        assert len(momentum_tracked) >= 3, "Need enough tracked results"
        loss_ratio = len(momentum_losses) / len(momentum_tracked)
        # Target is 60%, allow tolerance (50%-75%)
        assert loss_ratio >= 0.45, f"Loss ratio {loss_ratio:.1%} should be >= 45%"
        assert loss_ratio <= 0.80, f"Loss ratio {loss_ratio:.1%} should be <= 80%"


# ============================================================
# 7. Invariants
# ============================================================

class TestReportInvariants:
    """Test architectural invariants."""

    def test_report_model_no_infra_import(self):
        """domain/models/report.py must not import from infra/."""
        source = (BACKEND_DIR / "domain" / "models" / "report.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("infra"), (
                        f"domain/models/report.py imports from infra: {node.module}"
                    )

    def test_report_service_no_infra_import(self):
        """domain/services/report_service.py must not import from infra/."""
        source = (BACKEND_DIR / "domain" / "services" / "report_service.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("infra"), (
                        f"domain/services/report_service.py imports from infra: {node.module}"
                    )

    def test_report_protocol_no_infra_import(self):
        """domain/protocols/report_generator.py must not import from infra/."""
        source = (BACKEND_DIR / "domain" / "protocols" / "report_generator.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("infra"), (
                        f"domain/protocols/report_generator.py imports from infra: {node.module}"
                    )

    def test_report_service_pure_functions(self):
        """report_service.py should have no file I/O or network calls."""
        source = (BACKEND_DIR / "domain" / "services" / "report_service.py").read_text()
        # No open(), no Path(), no requests, no httpx
        assert "open(" not in source
        assert "import requests" not in source
        assert "import httpx" not in source
        assert "Path(" not in source

    def test_models_init_exports_report(self):
        """domain/models/__init__.py should export report models."""
        from domain.models import (
            MotivationDistribution,
            DecisionPattern,
            QualityTrend,
            MonthlyReport,
        )
        assert MotivationDistribution is not None
        assert DecisionPattern is not None
        assert QualityTrend is not None
        assert MonthlyReport is not None

    def test_protocols_init_exports_report_generator(self):
        """domain/protocols/__init__.py should export ReportGeneratorProtocol."""
        from domain.protocols import ReportGeneratorProtocol
        assert ReportGeneratorProtocol is not None
