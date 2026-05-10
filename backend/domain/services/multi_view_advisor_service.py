"""
Multi-View Advisor Service
===========================
Pure functions for three-perspective review generation.
- Derives trigger conditions
- Scores template applicability
- Selects best template per perspective
- Renders filled templates

Invariant #12: service functions are pure (no I/O, no side effects).
"""
from __future__ import annotations

import time
import random
from typing import Dict, List, Tuple, Any, Optional

from domain.models.multi_perspective import (
    PerspectiveView,
    PerspectiveType,
    MultiViewReview,
    MultiViewRequest,
    TriggerCondition,
)


# ============================================================================
# Trigger Condition Checking
# ============================================================================

def check_amount_major(request: MultiViewRequest) -> Tuple[bool, Dict[str, Any]]:
    """Check if amount > 20% of portfolio."""
    pct = request.transaction_amount / request.total_portfolio_value * 100
    met = pct > 20.0
    return met, {"transaction_pct": round(pct, 1), "threshold": 20.0}


def check_asset_class_change(request: MultiViewRequest) -> Tuple[bool, Dict[str, Any]]:
    """Check if this is a new major asset class (gold, RE, crypto first time)."""
    major_classes = {"real_estate", "crypto", "gold", "commodities"}
    # Simplified: assume position_value == 0 means new class
    is_new = request.current_position_value == 0.0
    met = is_new and request.asset_class in major_classes
    return met, {"asset_class": request.asset_class, "is_new": is_new}


def check_concentration_breach(request: MultiViewRequest) -> Tuple[bool, Dict[str, Any]]:
    """Check if single asset would exceed 25% after this trade."""
    new_value = request.current_position_value + request.transaction_amount
    new_pct = new_value / request.total_portfolio_value * 100
    met = new_pct > 25.0
    return met, {"new_concentration_pct": round(new_pct, 1), "threshold": 25.0}


def derive_triggers(request: MultiViewRequest) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """Evaluate all trigger conditions.
    
    Returns:
        (list of triggered condition names, dict of metadata per condition)
    """
    conditions = [
        ("amount_major", check_amount_major),
        ("asset_class_change", check_asset_class_change),
        ("concentration_breach", check_concentration_breach),
    ]
    
    triggered = []
    metadata = {}
    
    for name, checker in conditions:
        met, meta = checker(request)
        if met:
            triggered.append(name)
            metadata[name] = meta
    
    return triggered, metadata


# ============================================================================
# Template Selection & Rendering
# ============================================================================

def score_template_applicability(
    template: Dict[str, Any],
    request: MultiViewRequest,
    triggered: List[str],
) -> float:
    """Score how well a template matches this decision context.
    
    Higher score = better match.
    """
    score = 0.0
    
    # Bonus if template triggers match
    template_triggers = set(template.get("trigger", []))
    matched_triggers = len(template_triggers & set(triggered))
    score += matched_triggers * 3.0
    
    # Bonus if enough context is available
    context_items = sum([
        request.recent_return_pct is not None,
        request.historical_return_pct is not None,
        request.loss_pct is not None,
        request.days_since_rebalance is not None,
    ])
    score += context_items * 0.5
    
    return score


def select_template_for_perspective(
    templates: List[Dict[str, Any]],
    request: MultiViewRequest,
    triggered: List[str],
) -> Optional[Dict[str, Any]]:
    """Select best template for a perspective."""
    if not templates:
        return None
    
    scored = [
        (t, score_template_applicability(t, request, triggered))
        for t in templates
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    
    # Return top-1 with some randomization (top-2 50/50 if close)
    best = scored[0]
    if len(scored) > 1 and abs(scored[0][1] - scored[1][1]) < 0.5:
        return random.choice([scored[0][0], scored[1][0]])
    
    return best[0]


def render_template(
    template: Dict[str, Any],
    request: MultiViewRequest,
) -> str:
    """Fill template with request context.
    
    Simple template format: "{field_name}" → request attribute
    """
    text = template.get("template", "")
    
    # Build substitution dict
    subs = {
        "asset_name": request.asset_name,
        "asset_class": request.asset_class,
        "transaction_amount": round(request.transaction_amount, 0),
        "current_position_value": round(request.current_position_value, 0),
        "total_portfolio_value": round(request.total_portfolio_value, 0),
        "transaction_pct": round(
            request.transaction_amount / request.total_portfolio_value * 100, 1
        ),
        "current_pct": round(
            request.current_position_value / request.total_portfolio_value * 100, 1
        ),
        "new_pct": round(
            (request.current_position_value + request.transaction_amount)
            / request.total_portfolio_value * 100,
            1,
        ),
        "max_pct": 15,  # Domain default
        "decline_pct": 30,  # Scenario for Graham
        "recent_return_pct": round(request.recent_return_pct or 0, 1),
        "historical_return_pct": round(request.historical_return_pct or 0, 1),
        "loss_pct": round(request.loss_pct or 0, 1),
        "days_since_rebalance": request.days_since_rebalance or 0,
    }
    
    # Fill placeholders
    for key, value in subs.items():
        text = text.replace(f"{{{key}}}", str(value))
    
    # Remove any remaining unfilled placeholders
    import re
    text = re.sub(r"\{[^}]+\}", "", text)
    
    return text.strip()


def calculate_confidence(
    template: Dict[str, Any],
    request: MultiViewRequest,
    triggered: List[str],
) -> float:
    """Confidence in this perspective (0.0-1.0).
    
    Higher = template is better matched to context.
    """
    base = 0.7  # Start with 70% baseline
    
    # Bonus for trigger matches
    template_triggers = set(template.get("trigger", []))
    matched = len(template_triggers & set(triggered))
    base += min(matched * 0.1, 0.2)  # Up to +20%
    
    # Penalty if context incomplete
    context_available = sum([
        request.recent_return_pct is not None,
        request.historical_return_pct is not None,
        request.loss_pct is not None,
        request.days_since_rebalance is not None,
    ])
    if context_available < 2:
        base -= 0.1
    
    return min(max(base, 0.0), 1.0)


# ============================================================================
# Orchestration
# ============================================================================

def generate_multi_view_review(
    request: MultiViewRequest,
    all_templates: Dict[str, List[Dict[str, Any]]],
) -> Optional[MultiViewReview]:
    """Generate complete multi-perspective review.
    
    Args:
        request: Decision context
        all_templates: Dict of templates by perspective
                      e.g., {"conservative_graham": [...], ...}
    
    Returns:
        Complete review if triggers met and all views generated,
        None otherwise.
    """
    # 1. Check triggers
    triggered, trigger_meta = derive_triggers(request)
    if not triggered:
        return None  # No major decision, skip review
    
    # 2. Generate each perspective
    perspectives_data = [
        (PerspectiveType.CONSERVATIVE, "保守派 (Graham)", all_templates.get("conservative_graham", [])),
        (PerspectiveType.LONGTERM, "长期派 (Bogle)", all_templates.get("longterm_bogle", [])),
        (PerspectiveType.BEHAVIORAL, "行为派 (Kahneman)", all_templates.get("behavioral_kahneman", [])),
    ]
    
    views = {}
    for ptype, title, templates_for_perspective in perspectives_data:
        selected = select_template_for_perspective(templates_for_perspective, request, triggered)
        if not selected:
            return None  # Can't generate all views, fail
        
        text = render_template(selected, request)
        confidence = calculate_confidence(selected, request, triggered)
        
        views[ptype] = PerspectiveView(
            perspective=ptype,
            title=title,
            text=text,
            template_id=selected.get("id", "unknown"),
            confidence=confidence,
        )
    
    # 3. Build review
    decision_id = f"{request.user_id}:{request.asset_name}:{int(time.time())}"
    
    return MultiViewReview(
        decision_id=decision_id,
        user_id=request.user_id,
        asset_name=request.asset_name,
        asset_class=request.asset_class,
        decision_context=request.to_dict(),
        conservative_view=views[PerspectiveType.CONSERVATIVE],
        longterm_view=views[PerspectiveType.LONGTERM],
        behavioral_view=views[PerspectiveType.BEHAVIORAL],
        created_at=time.time(),
        triggers_met=triggered,
    )


def get_perspective_titles() -> Dict[str, str]:
    """Return display titles."""
    return {
        "conservative": "保守派 (Graham)",
        "longterm": "长期派 (Bogle)",
        "behavioral": "行为派 (Kahneman)",
    }
