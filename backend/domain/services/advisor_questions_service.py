"""
Advisor Questions Service — Socratic question selection logic
==============================================================
Pure functions that select 3-5 questions from the template bank
based on user action + profile + portfolio context.

Design doc: docs/design/09-advisor-features.md §一

Key constraint (from design doc):
  AI **只挑选 + 填空**，不自由发挥。

Invariant #9: No cross-imports between domain services.
Invariant: Pure functions — no I/O, no side effects.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from domain.models.questions import (
    QuestionTemplate,
    SocraticQuestion,
    SocraticSession,
)


# ============================================================
# Trigger derivation — map user action + context to triggers
# ============================================================

def derive_triggers(
    action: str,
    reason_ids: List[str],
    context: Dict[str, Any],
) -> List[str]:
    """Derive applicable trigger conditions from user action + context.

    Pure function: maps structured context to a list of trigger strings
    that match the "applicable_when" field in question templates.

    Args:
        action: user action ("buy", "sell", "add", "reduce")
        reason_ids: predefined reason IDs the user selected
        context: dict with optional keys:
            - concentration_pct: float (single asset concentration after trade)
            - days_since_last_trade: int
            - emergency_months: float
            - insurance_count: int
            - amount_pct: float (trade as % of investable)
            - has_debt: bool
            - diversification_count: int (number of asset classes)

    Returns:
        List of trigger strings (e.g. ["any_buy", "high_concentration", "fomo"])
    """
    triggers: List[str] = []

    # Action-based triggers
    action_map = {
        "buy": "any_buy",
        "sell": "any_sell",
        "add": "any_add",
        "reduce": "any_reduce",
    }
    if action in action_map:
        triggers.append(action_map[action])

    # Reason-based triggers (direct from reason_ids)
    reason_triggers = {
        "momentum_chase", "hot_news", "fomo", "averaging_down",
        "valuation_low", "sector_logic", "allocation_gap", "dca_plan",
    }
    for rid in reason_ids:
        if rid in reason_triggers:
            triggers.append(rid)

    # Context-derived triggers
    concentration_pct = context.get("concentration_pct", 0.0)
    if concentration_pct > 0.25:
        triggers.append("high_concentration")

    days_since_last = context.get("days_since_last_trade", 999)
    if days_since_last < 30:
        triggers.append("short_cooldown")
        triggers.append("recent_trade")

    if days_since_last < 14:
        triggers.append("frequent_trade")

    emergency_months = context.get("emergency_months", 6.0)
    if emergency_months < 6.0:
        triggers.append("low_emergency_fund")

    insurance_count = context.get("insurance_count", 4)
    if insurance_count < 3:
        triggers.append("low_insurance")

    amount_pct = context.get("amount_pct", 0.0)
    if amount_pct > 0.15:
        triggers.append("large_amount")

    if context.get("has_debt", False):
        triggers.append("has_debt")

    diversification_count = context.get("diversification_count", 5)
    if diversification_count < 3:
        triggers.append("low_diversification")

    # Volatility / panic triggers
    if context.get("high_volatility", False):
        triggers.append("high_volatility")
    if context.get("panic_sell", False):
        triggers.append("panic_sell")
    if context.get("is_single_stock", False):
        triggers.append("single_stock_buy")
    if context.get("illiquid", False):
        triggers.append("illiquid_asset")

    holding_days = context.get("planned_holding_days", 1000)
    if holding_days < 365:
        triggers.append("short_hold")

    return triggers


# ============================================================
# Question scoring — rank templates by relevance
# ============================================================

def score_template(
    template: QuestionTemplate,
    triggers: List[str],
) -> float:
    """Score a template's relevance given current triggers.

    Higher score = more relevant to current situation.

    Scoring logic:
      - Base: template.weight
      - +10 per matching trigger in applicable_when
      - Bonus +5 if >2 triggers match (very specific question)

    Args:
        template: the question template to score
        triggers: derived trigger list from derive_triggers()

    Returns:
        Float relevance score (0+). Zero if no triggers match.
    """
    matches = sum(1 for t in template.applicable_when if t in triggers)

    if matches == 0:
        return 0.0

    score = float(template.weight) + (matches * 10.0)

    # Bonus for highly specific questions
    if matches >= 2:
        score += 5.0

    return score


# ============================================================
# Question selection — pick diverse top-k questions
# ============================================================

def select_questions(
    templates: List[QuestionTemplate],
    triggers: List[str],
    max_questions: int = 5,
    min_questions: int = 3,
) -> List[tuple[QuestionTemplate, float]]:
    """Select the best 3-5 questions from templates given triggers.

    Ensures category diversity: no two selected questions share the same category
    (unless there aren't enough categories).

    Args:
        templates: all available templates
        triggers: derived trigger list
        max_questions: maximum questions to select (default 5)
        min_questions: minimum questions to select (default 3)

    Returns:
        List of (template, score) tuples, ordered by score descending.
        Length between min_questions and max_questions.
    """
    # Score all templates
    scored: List[tuple[QuestionTemplate, float]] = []
    for t in templates:
        s = score_template(t, triggers)
        if s > 0:
            scored.append((t, s))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Select with category diversity
    selected: List[tuple[QuestionTemplate, float]] = []
    seen_categories: set[str] = set()

    # First pass: one per category (highest scored wins)
    for tmpl, scr in scored:
        if len(selected) >= max_questions:
            break
        if tmpl.category not in seen_categories:
            selected.append((tmpl, scr))
            seen_categories.add(tmpl.category)

    # Second pass: fill remaining slots from top-scored (even if category repeats)
    if len(selected) < min_questions:
        for tmpl, scr in scored:
            if len(selected) >= min_questions:
                break
            if tmpl not in [s[0] for s in selected]:
                selected.append((tmpl, scr))

    # Re-sort final selection by score
    selected.sort(key=lambda x: x[1], reverse=True)

    return selected[:max_questions]


# ============================================================
# Template rendering — fill placeholders with context data
# ============================================================

def render_question(
    template: QuestionTemplate,
    context: Dict[str, Any],
    score: float = 0.0,
) -> SocraticQuestion:
    """Render a template into a final SocraticQuestion.

    Fills placeholders like {concentration_pct}, {days_since_last} with
    actual values from the user context.

    Args:
        template: the selected template
        context: user context dict with values for placeholders
        score: relevance score from scoring phase

    Returns:
        A rendered SocraticQuestion with filled text.
    """
    # Build placeholder values
    fill_values: Dict[str, str] = {
        "concentration_pct": str(round(context.get("concentration_pct", 0) * 100, 1)),
        "days_since_last": str(context.get("days_since_last_trade", "?")),
        "amount_pct": str(round(context.get("amount_pct", 0) * 100, 1)),
        "emergency_months": str(context.get("emergency_months", "?")),
    }

    # Safe format: only replace known placeholders, leave unknown ones
    text = template.text
    for key, val in fill_values.items():
        text = text.replace(f"{{{key}}}", val)

    return SocraticQuestion(
        template_id=template.id,
        category=template.category,
        text=text,
        relevance_score=score,
    )


# ============================================================
# Session builder — main entry point for the service
# ============================================================

def build_socratic_session(
    templates: List[QuestionTemplate],
    user_id: str,
    action: str,
    reason_ids: List[str],
    context: Dict[str, Any],
    max_questions: int = 5,
    min_questions: int = 3,
) -> SocraticSession:
    """Build a complete Socratic questioning session.

    Main entry point for the service layer. Pure function that:
    1. Derives triggers from action + reasons + context
    2. Scores and selects templates
    3. Renders questions with context data
    4. Returns a SocraticSession ready for the API

    Args:
        templates: all available templates (from question bank)
        user_id: who is being asked
        action: trade action (buy/sell/add/reduce)
        reason_ids: selected reason IDs
        context: user + portfolio context data
        max_questions: max questions to return (default 5)
        min_questions: min questions to return (default 3)

    Returns:
        SocraticSession with 3-5 rendered questions.
    """
    triggers = derive_triggers(action, reason_ids, context)

    selected = select_questions(
        templates=templates,
        triggers=triggers,
        max_questions=max_questions,
        min_questions=min_questions,
    )

    questions = [
        render_question(tmpl, context, score=scr)
        for tmpl, scr in selected
    ]

    # Build context summary
    trigger_summary = ", ".join(triggers[:5])
    context_summary = f"action={action}, triggers=[{trigger_summary}]"

    return SocraticSession(
        user_id=user_id,
        action=action,
        questions=questions,
        context_summary=context_summary,
    )
