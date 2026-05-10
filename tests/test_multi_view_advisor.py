"""
Test suite for M6 W1: Three-perspective advisor system
======================================================
Tests for:
- Trigger condition checking (amount, asset class, concentration)
- Template selection and scoring
- Template rendering with context variables
- Confidence calculation
- Complete end-to-end review generation
- API endpoints
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Any, List

import pytest

# Ensure backend/ is on sys.path (same as main.py line 17)
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from domain.models.multi_perspective import (
    PerspectiveView,
    PerspectiveType,
    MultiViewReview,
    MultiViewRequest,
)
from domain.services.multi_view_advisor_service import (
    check_amount_major,
    check_asset_class_change,
    check_concentration_breach,
    derive_triggers,
    score_template_applicability,
    select_template_for_perspective,
    render_template,
    calculate_confidence,
    generate_multi_view_review,
    get_perspective_titles,
)
from infra.knowledge.multi_view_advisor import get_multi_view_advisor


class TestTriggerConditions:
    """Test trigger condition checking."""
    
    def test_amount_major_triggered(self) -> None:
        """Transaction >20% of portfolio triggers amount_major."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=30000.0,  # >20%
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
            recent_return_pct=5.0,
            historical_return_pct=10.0,
        )
        met, metadata = check_amount_major(request)
        assert met is True
        assert metadata["transaction_pct"] == 30.0
        assert metadata["threshold"] == 20.0
    
    def test_amount_major_not_triggered(self) -> None:
        """Transaction <=20% does not trigger amount_major."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=15000.0,  # <20%
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        met, metadata = check_amount_major(request)
        assert met is False
        assert metadata["transaction_pct"] == 15.0
    
    def test_amount_major_boundary(self) -> None:
        """Exactly 20% is not triggered (> not >=)."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=20000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        met, metadata = check_amount_major(request)
        assert met is False
    
    def test_asset_class_change_triggered(self) -> None:
        """New major asset class (first position) triggers asset_class_change."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="Au",
            asset_class="gold",
            transaction_amount=5000.0,
            current_position_value=0.0,  # First position
            total_portfolio_value=100000.0,
        )
        met, metadata = check_asset_class_change(request)
        assert met is True
        assert metadata["asset_class"] == "gold"
        assert metadata["is_new"] is True
    
    def test_asset_class_change_not_new(self) -> None:
        """Existing asset class does not trigger."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="Au",
            asset_class="gold",
            transaction_amount=5000.0,
            current_position_value=10000.0,  # Already has position
            total_portfolio_value=100000.0,
        )
        met, metadata = check_asset_class_change(request)
        assert met is False
    
    def test_asset_class_change_not_major(self) -> None:
        """New minor asset class (e.g., stocks) does not trigger."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",  # Not in major_classes
            transaction_amount=5000.0,
            current_position_value=0.0,
            total_portfolio_value=100000.0,
        )
        met, metadata = check_asset_class_change(request)
        assert met is False
    
    def test_concentration_breach_triggered(self) -> None:
        """Position >25% of portfolio triggers concentration_breach."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=20000.0,
            current_position_value=15000.0,
            total_portfolio_value=100000.0,
        )
        met, metadata = check_concentration_breach(request)
        assert met is True  # 35/100 = 35%
        assert metadata["new_concentration_pct"] == 35.0
    
    def test_concentration_breach_not_triggered(self) -> None:
        """Position <=25% does not trigger."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=15000.0,
            total_portfolio_value=100000.0,
        )
        met, metadata = check_concentration_breach(request)
        assert met is False  # 20/100 = 20%
    
    def test_concentration_breach_boundary(self) -> None:
        """Exactly 25% is not triggered (> not >=)."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=10000.0,
            current_position_value=15000.0,
            total_portfolio_value=100000.0,
        )
        met, metadata = check_concentration_breach(request)
        assert met is False  # 25/100 = 25%


class TestTriggerDerivation:
    """Test trigger derivation (orchestration of all conditions)."""
    
    def test_single_trigger(self) -> None:
        """Only amount_major is triggered (when concentration stays under 25%)."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=25000.0,  # 25% > 20% threshold - triggers amount_major
            current_position_value=0.0,  # New total: 25k/100k = 25% (not >25%, exactly at boundary)
            total_portfolio_value=100000.0,
        )
        triggered, metadata = derive_triggers(request)
        assert "amount_major" in triggered
        assert "asset_class_change" not in triggered
        assert "concentration_breach" not in triggered
    
    def test_multiple_triggers(self) -> None:
        """Multiple triggers can fire simultaneously."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="Au",
            asset_class="gold",
            transaction_amount=30000.0,
            current_position_value=0.0,
            total_portfolio_value=100000.0,
        )
        triggered, metadata = derive_triggers(request)
        assert "amount_major" in triggered
        assert "asset_class_change" in triggered
    
    def test_no_triggers(self) -> None:
        """Small purchase of existing asset has no triggers."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        triggered, metadata = derive_triggers(request)
        assert len(triggered) == 0
        assert len(metadata) == 0


class TestTemplateScoring:
    """Test template applicability scoring."""
    
    def test_score_with_trigger_match(self) -> None:
        """Template matching triggered conditions gets bonus."""
        template = {
            "id": "cv_safety_margin",
            "trigger": ["amount_major", "concentration_breach"],
            "template": "Consider safety margin..."
        }
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=30000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        triggered = ["amount_major"]
        
        score = score_template_applicability(template, request, triggered)
        assert score > 0.0  # Has match on amount_major
    
    def test_score_with_context_bonus(self) -> None:
        """More available context increases score."""
        template = {"id": "test", "trigger": [], "template": "Test"}
        
        request_no_context = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=0.0,
            total_portfolio_value=100000.0,
        )
        
        request_full_context = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=0.0,
            total_portfolio_value=100000.0,
            recent_return_pct=5.0,
            historical_return_pct=10.0,
            loss_pct=-2.0,
            days_since_rebalance=30,
        )
        
        score_no = score_template_applicability(template, request_no_context, [])
        score_full = score_template_applicability(template, request_full_context, [])
        assert score_full > score_no


class TestTemplateSelection:
    """Test template selection logic."""
    
    def test_select_best_template(self) -> None:
        """Highest-scoring template is selected."""
        templates = [
            {"id": "t1", "trigger": [], "template": "Template 1"},
            {"id": "t2", "trigger": ["amount_major"], "template": "Template 2"},
            {"id": "t3", "trigger": [], "template": "Template 3"},
        ]
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=30000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        triggered = ["amount_major"]
        
        selected = select_template_for_perspective(templates, request, triggered)
        assert selected is not None
        assert selected["id"] == "t2"  # Matches trigger
    
    def test_select_from_empty_list(self) -> None:
        """Empty template list returns None."""
        selected = select_template_for_perspective([], None, [])  # type: ignore
        assert selected is None


class TestTemplateRendering:
    """Test template variable substitution."""
    
    def test_render_basic_placeholders(self) -> None:
        """Placeholders are filled with request values."""
        template = {
            "id": "test",
            "template": "Buy {asset_name} at {transaction_pct}% of portfolio"
        }
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=20000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        
        text = render_template(template, request)
        assert "MSFT" in text
        assert "20.0" in text or "20%" in text  # Transaction is 20% of 100k
    
    def test_render_concentration_placeholders(self) -> None:
        """Concentration-related placeholders work."""
        template = {
            "id": "test",
            "template": "{asset_name} would reach {new_pct}% concentration"
        }
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=10000.0,
            current_position_value=15000.0,
            total_portfolio_value=100000.0,
        )
        
        text = render_template(template, request)
        assert "MSFT" in text
        assert "25" in text  # New concentration is 25%
    
    def test_render_removes_unfilled_placeholders(self) -> None:
        """Unfilled placeholders are removed."""
        template = {
            "id": "test",
            "template": "Asset {asset_name}, optional {undefined_field}"
        }
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=0.0,
            total_portfolio_value=100000.0,
        )
        
        text = render_template(template, request)
        assert "MSFT" in text
        assert "{" not in text
        assert "}" not in text


class TestConfidenceCalculation:
    """Test confidence scoring."""
    
    def test_confidence_baseline(self) -> None:
        """Confidence starts at 0.7 baseline."""
        template = {"id": "test", "trigger": []}
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=0.0,
            total_portfolio_value=100000.0,
        )
        
        conf = calculate_confidence(template, request, [])
        assert 0.6 <= conf <= 0.8  # Around baseline
    
    def test_confidence_trigger_bonus(self) -> None:
        """Matching triggers increase confidence."""
        template = {"id": "test", "trigger": ["amount_major", "concentration_breach"]}
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=30000.0,
            current_position_value=15000.0,
            total_portfolio_value=100000.0,
        )
        
        conf_no_match = calculate_confidence(template, request, [])
        conf_with_match = calculate_confidence(template, request, ["amount_major", "concentration_breach"])
        assert conf_with_match > conf_no_match
    
    def test_confidence_clamped(self) -> None:
        """Confidence is clamped to [0.0, 1.0]."""
        template = {"id": "test", "trigger": []}
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=0.0,
            total_portfolio_value=100000.0,
        )
        
        conf = calculate_confidence(template, request, [])
        assert 0.0 <= conf <= 1.0


class TestMultiViewReviewGeneration:
    """Test complete end-to-end review generation."""
    
    @pytest.fixture
    def templates(self) -> Dict[str, List[Dict[str, Any]]]:
        """Load templates for testing."""
        advisor = get_multi_view_advisor()
        # Load from real templates
        try:
            from infra.knowledge.multi_view_advisor import MultiViewAdvisor
            infra_advisor = advisor
            return infra_advisor._templates
        except Exception:
            # Fallback: create minimal templates for testing
            return {
                "conservative_graham": [
                    {
                        "id": "cv_safety_margin",
                        "trigger": ["amount_major"],
                        "template": "Consider safety margin with {asset_name}",
                    },
                ],
                "longterm_bogle": [
                    {
                        "id": "lt_horizon",
                        "trigger": ["amount_major"],
                        "template": "Think long-term with {asset_name}",
                    },
                ],
                "behavioral_kahneman": [
                    {
                        "id": "bh_bias",
                        "trigger": ["amount_major"],
                        "template": "Avoid bias with {asset_name}",
                    },
                ],
            }
    
    def test_generate_review_with_triggers(self, templates: Dict[str, List[Dict[str, Any]]]) -> None:
        """Review generates when triggers are met."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=30000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        
        review = generate_multi_view_review(request, templates)
        assert review is not None
        assert review.user_id == "user1"
        assert review.asset_name == "MSFT"
        assert review.conservative_view is not None
        assert review.longterm_view is not None
        assert review.behavioral_view is not None
    
    def test_generate_review_no_triggers(self, templates: Dict[str, List[Dict[str, Any]]]) -> None:
        """Review is None when no triggers are met."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        
        review = generate_multi_view_review(request, templates)
        assert review is None
    
    def test_review_has_all_fields(self, templates: Dict[str, List[Dict[str, Any]]]) -> None:
        """Generated review has all required fields."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=30000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        
        review = generate_multi_view_review(request, templates)
        assert review is not None
        assert review.decision_id is not None
        assert review.created_at > 0
        assert len(review.triggers_met) > 0
        # decision_context stores the request, so should have user_id and asset_name
        assert review.decision_context["user_id"] == "user1"
        assert review.decision_context["asset_name"] == "MSFT"


class TestPerspectiveTitles:
    """Test perspective display titles."""
    
    def test_titles_present(self) -> None:
        """All three perspectives have titles."""
        titles = get_perspective_titles()
        assert "conservative" in titles
        assert "longterm" in titles
        assert "behavioral" in titles
    
    def test_titles_localized(self) -> None:
        """Titles contain Chinese characters."""
        titles = get_perspective_titles()
        for title in titles.values():
            assert any(ord(c) > 0x4E00 for c in title)  # Contains Chinese


class TestMultiViewAdvisorProtocol:
    """Test MultiViewAdvisorProtocol compliance."""
    
    def test_protocol_importable(self) -> None:
        """Protocol can be imported."""
        from domain.protocols import MultiViewAdvisorProtocol
        assert MultiViewAdvisorProtocol is not None
    
    def test_protocol_in_init(self) -> None:
        """Protocol is exported from domain.protocols."""
        from domain.protocols import MultiViewAdvisorProtocol
        assert MultiViewAdvisorProtocol is not None
    
    def test_infra_advisor_satisfies_protocol(self) -> None:
        """Infra MultiViewAdvisor implements protocol."""
        from domain.protocols import MultiViewAdvisorProtocol
        from infra.knowledge.multi_view_advisor import get_multi_view_advisor
        
        advisor = get_multi_view_advisor()
        assert isinstance(advisor, MultiViewAdvisorProtocol)


class TestPerspectiveView:
    """Test PerspectiveView model."""
    
    def test_perspective_view_creation(self) -> None:
        """PerspectiveView can be created."""
        view = PerspectiveView(
            perspective=PerspectiveType.CONSERVATIVE,
            title="保守派",
            text="Conservative advice",
            template_id="cv_test",
            confidence=0.85,
        )
        assert view.perspective == PerspectiveType.CONSERVATIVE
        assert view.confidence == 0.85
    
    def test_perspective_view_frozen(self) -> None:
        """PerspectiveView is immutable."""
        view = PerspectiveView(
            perspective=PerspectiveType.CONSERVATIVE,
            title="保守派",
            text="Conservative advice",
            template_id="cv_test",
            confidence=0.85,
        )
        with pytest.raises(Exception):
            view.confidence = 0.5  # type: ignore


class TestMultiViewRequest:
    """Test MultiViewRequest model."""
    
    def test_request_creation(self) -> None:
        """MultiViewRequest can be created."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
        )
        assert request.user_id == "user1"
        assert request.asset_name == "MSFT"
    
    def test_request_to_dict(self) -> None:
        """Request can be converted to dict."""
        request = MultiViewRequest(
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            transaction_amount=5000.0,
            current_position_value=10000.0,
            total_portfolio_value=100000.0,
            recent_return_pct=5.0,
        )
        d = request.to_dict()
        assert d["user_id"] == "user1"
        assert d["asset_name"] == "MSFT"
        assert d["recent_return_pct"] == 5.0


class TestMultiViewReview:
    """Test MultiViewReview model."""
    
    def test_review_creation(self) -> None:
        """MultiViewReview can be created."""
        view1 = PerspectiveView(
            perspective=PerspectiveType.CONSERVATIVE,
            title="保守派",
            text="advice1",
            template_id="t1",
            confidence=0.8,
        )
        view2 = PerspectiveView(
            perspective=PerspectiveType.LONGTERM,
            title="长期派",
            text="advice2",
            template_id="t2",
            confidence=0.85,
        )
        view3 = PerspectiveView(
            perspective=PerspectiveType.BEHAVIORAL,
            title="行为派",
            text="advice3",
            template_id="t3",
            confidence=0.9,
        )
        
        review = MultiViewReview(
            decision_id="id1",
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            decision_context={},
            conservative_view=view1,
            longterm_view=view2,
            behavioral_view=view3,
            created_at=123456,
            triggers_met=["amount_major"],
        )
        
        assert review.decision_id == "id1"
        assert review.triggers_met == ["amount_major"]
    
    def test_review_to_dict(self) -> None:
        """Review can be converted to dict."""
        view1 = PerspectiveView(
            perspective=PerspectiveType.CONSERVATIVE,
            title="保守派",
            text="advice",
            template_id="t1",
            confidence=0.8,
        )
        view2 = PerspectiveView(
            perspective=PerspectiveType.LONGTERM,
            title="长期派",
            text="advice",
            template_id="t2",
            confidence=0.85,
        )
        view3 = PerspectiveView(
            perspective=PerspectiveType.BEHAVIORAL,
            title="行为派",
            text="advice",
            template_id="t3",
            confidence=0.9,
        )
        
        review = MultiViewReview(
            decision_id="id1",
            user_id="user1",
            asset_name="MSFT",
            asset_class="stocks",
            decision_context={},
            conservative_view=view1,
            longterm_view=view2,
            behavioral_view=view3,
            created_at=123456,
            triggers_met=["amount_major"],
        )
        
        d = review.to_dict()
        assert d["decision_id"] == "id1"
        assert d["user_id"] == "user1"
        assert "conservative_view" in d


class TestInfraKnowledgeAdvisor:
    """Test infrastructure knowledge advisor."""
    
    def test_advisor_singleton(self) -> None:
        """get_multi_view_advisor returns singleton."""
        advisor1 = get_multi_view_advisor()
        advisor2 = get_multi_view_advisor()
        assert advisor1 is advisor2
    
    def test_advisor_has_methods(self) -> None:
        """Advisor has required methods."""
        advisor = get_multi_view_advisor()
        assert hasattr(advisor, "generate_review")
        assert hasattr(advisor, "check_triggers")
        assert hasattr(advisor, "get_perspective_titles")
    
    def test_advisor_get_titles(self) -> None:
        """Advisor returns perspective titles."""
        advisor = get_multi_view_advisor()
        titles = advisor.get_perspective_titles()
        assert "conservative" in titles
        assert "longterm" in titles
        assert "behavioral" in titles


class TestUseCaseRunMultiViewReview:
    """Test use case orchestration."""
    
    def test_use_case_importable(self) -> None:
        """Use case module can be imported."""
        from use_cases import run_multi_view_review
        assert run_multi_view_review is not None
    
    def test_generate_portfolio_review_function(self) -> None:
        """generate_portfolio_review function exists."""
        from use_cases.run_multi_view_review import generate_portfolio_review
        assert callable(generate_portfolio_review)
    
    def test_check_decision_triggers_function(self) -> None:
        """check_decision_triggers function exists."""
        from use_cases.run_multi_view_review import check_decision_triggers
        assert callable(check_decision_triggers)


class TestAPIEndpoints:
    """Test API endpoint schemas and routing."""

    def test_decisions_multi_view_endpoint_importable(self) -> None:
        """POST /api/decisions/multi-view endpoint exists."""
        from api.decisions import post_multi_view_review
        assert callable(post_multi_view_review)

    def test_advisor_multi_view_endpoint_importable(self) -> None:
        """POST /api/advisor/multi-view endpoint exists."""
        from api.advisor import generate_multi_view_review
        assert callable(generate_multi_view_review)

    def test_advisor_check_triggers_endpoint_importable(self) -> None:
        """POST /api/advisor/check-triggers endpoint exists."""
        from api.advisor import check_triggers
        assert callable(check_triggers)

    def test_advisor_info_endpoint_importable(self) -> None:
        """GET /api/advisor/info endpoint exists."""
        from api.advisor import get_advisor_info
        assert callable(get_advisor_info)

    def test_multi_view_request_schema(self) -> None:
        """MultiViewReviewRequest Pydantic model validates correctly."""
        from api.decisions import MultiViewReviewRequest
        req = MultiViewReviewRequest(
            user_id="user1",
            asset_name="贵州茅台",
            asset_class="stock",
            transaction_amount=50000.0,
            total_portfolio_value=200000.0,
        )
        assert req.user_id == "user1"
        assert req.transaction_amount == 50000.0

    def test_multi_view_response_schema(self) -> None:
        """MultiViewReviewResponse Pydantic model serializes correctly."""
        from api.decisions import MultiViewReviewResponse
        resp = MultiViewReviewResponse(
            status="ok",
            review=None,
            triggered=False,
            triggers_met=[],
            summary_text="",
        )
        assert resp.triggered is False
        assert resp.review is None


class TestEndToEndIntegration:
    """Integration tests: request → service → template → response."""

    def test_full_flow_triggers_met(self) -> None:
        """Full flow when triggers are met produces valid review."""
        from domain.models.multi_perspective import MultiViewRequest
        from use_cases.run_multi_view_review import generate_portfolio_review
        from infra.knowledge.multi_view_advisor import get_multi_view_advisor

        advisor = get_multi_view_advisor()
        request = MultiViewRequest(
            user_id="integration_test",
            asset_name="沪深300ETF",
            asset_class="fund",
            transaction_amount=50000.0,  # 50% > 20% threshold
            current_position_value=0.0,
            total_portfolio_value=100000.0,
            recent_return_pct=-5.0,
            loss_pct=-5.0,
            days_since_rebalance=90,
        )

        review = generate_portfolio_review(request, advisor)
        assert review is not None
        assert review.user_id == "integration_test"
        assert review.asset_name == "沪深300ETF"
        assert len(review.triggers_met) >= 1
        assert "amount_major" in review.triggers_met
        # All three views present
        assert review.conservative_view.text != ""
        assert review.longterm_view.text != ""
        assert review.behavioral_view.text != ""
        # Summary text has all three
        assert "保守派" in review.summary_text
        assert "长期派" in review.summary_text
        assert "行为派" in review.summary_text

    def test_full_flow_no_triggers(self) -> None:
        """Full flow when no triggers returns None."""
        from domain.models.multi_perspective import MultiViewRequest
        from use_cases.run_multi_view_review import generate_portfolio_review
        from infra.knowledge.multi_view_advisor import get_multi_view_advisor

        advisor = get_multi_view_advisor()
        request = MultiViewRequest(
            user_id="integration_test",
            asset_name="余额宝",
            asset_class="money_market",
            transaction_amount=1000.0,  # 1% -- well below 20%
            current_position_value=5000.0,
            total_portfolio_value=100000.0,
        )

        review = generate_portfolio_review(request, advisor)
        assert review is None

    def test_invariant_no_market_prediction(self) -> None:
        """Invariant: review text never contains market predictions."""
        from domain.models.multi_perspective import MultiViewRequest
        from use_cases.run_multi_view_review import generate_portfolio_review
        from infra.knowledge.multi_view_advisor import get_multi_view_advisor

        advisor = get_multi_view_advisor()
        request = MultiViewRequest(
            user_id="invariant_test",
            asset_name="比特币",
            asset_class="crypto",
            transaction_amount=30000.0,
            current_position_value=0.0,
            total_portfolio_value=100000.0,
        )

        review = generate_portfolio_review(request, advisor)
        assert review is not None
        # Check no prediction-like language in templates
        forbidden_patterns = ["会涨", "会跌", "建议买入", "建议卖出", "目标价"]
        for view in review.all_views:
            for pattern in forbidden_patterns:
                assert pattern not in view.text, (
                    f"Invariant violation: '{pattern}' found in {view.perspective.value} view"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
