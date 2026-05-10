"""
Generate Socratic Questions Use Case
======================================
Orchestrates the generation of Socratic questions for pre-trade reflection.

Flow:
  1. Load question templates from the question bank (infra)
  2. Derive context from user input (action, reasons, portfolio state)
  3. Call domain service to select and render questions
  4. Return SocraticSession ready for API response

Design doc: docs/design/09-advisor-features.md §一

Invariant #10: use_cases/ -> domain/ -> infra/ (one-way dependency).
Note: use_cases/ may import both domain/ and infra/ layers.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from domain.models.questions import SocraticSession
from domain.services.advisor_questions_service import build_socratic_session


def generate_socratic_questions(
    user_id: str,
    action: str,
    reason_ids: List[str],
    context: Dict[str, Any],
    max_questions: int = 5,
    min_questions: int = 3,
) -> SocraticSession:
    """Generate Socratic questions for a user's trade action.

    Main use-case entry point. Orchestrates:
      1. Loading templates from the question bank
      2. Building a Socratic session using domain service

    Args:
        user_id: who is being asked
        action: trade action (buy/sell/add/reduce)
        reason_ids: selected predefined reason IDs
        context: portfolio/profile context dict with keys:
            - concentration_pct: float (0-1)
            - days_since_last_trade: int
            - emergency_months: float
            - insurance_count: int (0-4)
            - amount_pct: float (0-1)
            - has_debt: bool
            - diversification_count: int
            - high_volatility: bool
            - panic_sell: bool
            - is_single_stock: bool
            - illiquid: bool
            - planned_holding_days: int
        max_questions: max questions to return (default 5)
        min_questions: min questions to return (default 3)

    Returns:
        SocraticSession with 3-5 rendered, context-specific questions.
    """
    from infra.knowledge.question_bank import get_question_bank

    # 1. Load templates
    bank = get_question_bank()
    templates = bank.load_templates()

    # 2. Build session using domain service (pure function)
    session = build_socratic_session(
        templates=templates,
        user_id=user_id,
        action=action,
        reason_ids=reason_ids,
        context=context,
        max_questions=max_questions,
        min_questions=min_questions,
    )

    return session
