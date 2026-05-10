"""
Question Bank Protocol
========================
Interface for loading and querying Socratic question templates.

Invariant #11: post-M1, any new cross-module interface needs a Protocol first.

Design doc: docs/design/09-advisor-features.md §一

Usage::

    from domain.protocols import QuestionBankProtocol

    class JsonQuestionBank:
        def load_templates(self) -> list[QuestionTemplate]:
            ...
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from domain.models.questions import QuestionTemplate


@runtime_checkable
class QuestionBankProtocol(Protocol):
    """Protocol for question template bank access.

    Implementations: infra/knowledge/question_bank.py

    Design doc: docs/design/09-advisor-features.md §一.3
    """

    def load_templates(self) -> List[QuestionTemplate]:
        """Load all question templates from the bank.

        Returns:
            List of QuestionTemplate objects from the template store.
            Empty list if no templates found.
        """
        ...

    def get_templates_by_trigger(self, trigger: str) -> List[QuestionTemplate]:
        """Get templates matching a specific trigger condition.

        Args:
            trigger: one of the applicable_when values (e.g. "any_buy", "fomo")

        Returns:
            List of templates whose applicable_when contains this trigger.
        """
        ...

    def get_template_by_id(self, template_id: str) -> QuestionTemplate | None:
        """Look up a single template by its ID.

        Args:
            template_id: unique template slug (e.g. "q_sleep_test")

        Returns:
            The template if found, None otherwise.
        """
        ...
