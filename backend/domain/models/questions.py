"""
Socratic Questions Domain Models
==================================
Business value objects for the Socratic questioning system (09-advisor-features.md §一).

Contents:
  - QuestionCategory: categories of Socratic questions
  - QuestionTemplate: a single question template from the template bank
  - SocraticQuestion: a rendered question (template + context-filled text)
  - SocraticSession: a set of questions presented to a user before a trade

All models are frozen dataclasses (immutable facts).
Downstream consumers: advisor_questions_service, generate_socratic_questions use case,
                      api/decisions (socratic endpoint).

Design doc: docs/design/09-advisor-features.md §一
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


# ============================================================
# QuestionCategory — topic/dimension of the question
# ============================================================

class QuestionCategory(str, Enum):
    """Categories of Socratic questions (09-advisor-features.md §1.3).

    Each question targets one cognitive dimension the user should consider.
    """
    TIME_HORIZON = "time_horizon"
    RISK_TOLERANCE = "risk_tolerance"
    RISK_AWARENESS = "risk_awareness"
    OPPORTUNITY_COST = "opportunity_cost"
    CONCENTRATION = "concentration"
    TRACK_RECORD = "track_record"
    SAFETY_NET = "safety_net"
    TIMING = "timing"
    INFORMATION_SOURCE = "information_source"
    EXIT_PLAN = "exit_plan"
    BEHAVIORAL_BIAS = "behavioral_bias"
    POSITION_SIZING = "position_sizing"
    KNOWLEDGE_CHECK = "knowledge_check"
    DIVERSIFICATION = "diversification"
    FAMILY_FINANCE = "family_finance"
    FINANCIAL_GOAL = "financial_goal"
    COST = "cost"
    STRATEGY = "strategy"
    ASSET_ALLOCATION = "asset_allocation"
    FUNDAMENTAL = "fundamental"


# ============================================================
# QuestionTemplate — raw template from the bank
# ============================================================

@dataclass(frozen=True)
class QuestionTemplate:
    """A single question template from the question bank.

    Templates contain placeholder tokens (e.g. {concentration_pct})
    that are filled at runtime with user-specific data.

    Fields::

        id               -- unique slug (e.g. "q_sleep_test")
        category         -- QuestionCategory value
        text             -- question text (may contain {placeholders})
        applicable_when  -- list of trigger conditions this applies to
        weight           -- base priority weight (higher = more likely selected)
    """
    id: str
    category: str  # QuestionCategory value string
    text: str
    applicable_when: List[str] = field(default_factory=list)
    weight: int = 5

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "id": self.id,
            "category": self.category,
            "text": self.text,
            "applicable_when": list(self.applicable_when),
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QuestionTemplate":
        """Construct from JSON dict."""
        return cls(
            id=d["id"],
            category=d["category"],
            text=d["text"],
            applicable_when=d.get("applicable_when", []),
            weight=d.get("weight", 5),
        )


# ============================================================
# SocraticQuestion — rendered (context-filled) question
# ============================================================

@dataclass(frozen=True)
class SocraticQuestion:
    """A rendered Socratic question ready to present to the user.

    This is a QuestionTemplate with placeholders resolved to actual values.

    Fields::

        template_id      -- which template this came from
        category         -- question category
        text             -- fully rendered question text
        relevance_score  -- how relevant this question is (0-100)
    """
    template_id: str
    category: str
    text: str
    relevance_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API response."""
        return {
            "template_id": self.template_id,
            "category": self.category,
            "text": self.text,
            "relevance_score": round(self.relevance_score, 1),
        }


# ============================================================
# SocraticSession — a set of questions for one user action
# ============================================================

@dataclass(frozen=True)
class SocraticSession:
    """A complete set of Socratic questions for one pre-trade reflection.

    Presented to the user before they execute a trade.
    Typically 3-5 questions covering diverse categories.

    Fields::

        user_id          -- who is being asked
        action           -- what trade action triggered this (buy/sell/add/reduce)
        questions        -- ordered list of rendered questions
        context_summary  -- brief description of what triggered these questions
    """
    user_id: str
    action: str
    questions: List[SocraticQuestion] = field(default_factory=list)
    context_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API response."""
        return {
            "user_id": self.user_id,
            "action": self.action,
            "questions": [q.to_dict() for q in self.questions],
            "context_summary": self.context_summary,
            "question_count": len(self.questions),
        }
