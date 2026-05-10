"""
Run Multi-View Review Use Case
===============================
Orchestrates infra (template loader) + domain service.

Dependency rule: use_cases/ → domain/ → infra/ (never backward).
"""
from __future__ import annotations

from typing import Dict, Any

from domain.models.multi_perspective import MultiViewReview, MultiViewRequest
from domain.protocols.multi_view_advisor import MultiViewAdvisorProtocol


def generate_portfolio_review(
    request: MultiViewRequest,
    advisor: MultiViewAdvisorProtocol,
) -> MultiViewReview | None:
    """Generate multi-perspective review for a portfolio decision.
    
    Args:
        request: Decision context (asset, amount, portfolio state)
        advisor: Injected MultiViewAdvisorProtocol implementation
    
    Returns:
        MultiViewReview if triggers met and all views generated,
        None if triggers not met or generation fails.
    """
    return advisor.generate_review(request)


def check_decision_triggers(
    request: MultiViewRequest,
    advisor: MultiViewAdvisorProtocol,
) -> Dict[str, bool]:
    """Check which trigger conditions are met for a decision.
    
    Args:
        request: Decision context
        advisor: Injected MultiViewAdvisorProtocol implementation
    
    Returns:
        Dict mapping trigger names to boolean (met or not).
    """
    return advisor.check_triggers(request)


def get_review_format_hints() -> Dict[str, Any]:
    """Return formatting hints for frontend.
    
    Returns:
        {
            "perspectives": ["conservative", "longterm", "behavioral"],
            "titles": {...},
            "min_chars": 50,
            "max_chars": 80,
            "trigger_conditions": [...],
        }
    """
    return {
        "perspectives": ["conservative", "longterm", "behavioral"],
        "titles": {
            "conservative": "保守派 (Graham)",
            "longterm": "长期派 (Bogle)",
            "behavioral": "行为派 (Kahneman)",
        },
        "min_chars": 50,
        "max_chars": 80,
        "trigger_conditions": [
            {
                "id": "amount_major",
                "name": "交易金额 > 20% 投资组合",
                "description": "单笔投入超过总资产 1/5 时触发",
            },
            {
                "id": "asset_class_change",
                "name": "首次配置大类资产",
                "description": "首次购买黄金、房产、加密等时触发",
            },
            {
                "id": "concentration_breach",
                "name": "单资产突破 25% 集中度",
                "description": "加仓后单只资产占比超过 1/4 时触发",
            },
        ],
    }
