"""
JSON Question Bank — loads Socratic templates from disk
========================================================
Implements QuestionBankProtocol using JSON files from
infra/knowledge/content/questions/.

Design doc: docs/design/09-advisor-features.md §一.3
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from domain.models.questions import QuestionTemplate

logger = logging.getLogger(__name__)

# Default directory for question template JSON files
_DEFAULT_QUESTIONS_DIR = Path(__file__).parent / "content" / "questions"

# Module-level singleton
_bank_instance: Optional["JsonQuestionBank"] = None


class JsonQuestionBank:
    """JSON-file-backed question template bank.

    Loads all .json files from the questions directory, parses them
    as lists of QuestionTemplate objects, and provides lookup methods.

    Implements QuestionBankProtocol.
    """

    def __init__(self, questions_dir: Optional[str] = None) -> None:
        self._dir = Path(questions_dir) if questions_dir else _DEFAULT_QUESTIONS_DIR
        self._templates: List[QuestionTemplate] = []
        self._by_id: Dict[str, QuestionTemplate] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load templates on first access."""
        if self._loaded:
            return
        self._load_all()
        self._loaded = True

    def _load_all(self) -> None:
        """Load all JSON files from the questions directory."""
        if not self._dir.exists():
            logger.warning(f"Questions directory not found: {self._dir}")
            return

        for json_file in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        tmpl = QuestionTemplate.from_dict(item)
                        self._templates.append(tmpl)
                        self._by_id[tmpl.id] = tmpl
                logger.info(f"Loaded {len(data)} templates from {json_file.name}")
            except Exception as e:
                logger.error(f"Failed to load {json_file}: {e}")

        logger.info(f"Question bank total: {len(self._templates)} templates")

    def load_templates(self) -> List[QuestionTemplate]:
        """Load all question templates from the bank."""
        self._ensure_loaded()
        return list(self._templates)

    def get_templates_by_trigger(self, trigger: str) -> List[QuestionTemplate]:
        """Get templates matching a specific trigger condition."""
        self._ensure_loaded()
        return [
            t for t in self._templates
            if trigger in t.applicable_when
        ]

    def get_template_by_id(self, template_id: str) -> Optional[QuestionTemplate]:
        """Look up a single template by its ID."""
        self._ensure_loaded()
        return self._by_id.get(template_id)

    @property
    def template_count(self) -> int:
        """Number of loaded templates."""
        self._ensure_loaded()
        return len(self._templates)


def get_question_bank(
    questions_dir: Optional[str] = None,
) -> JsonQuestionBank:
    """Get or create the question bank singleton.

    Args:
        questions_dir: override questions directory path

    Returns:
        JsonQuestionBank instance implementing QuestionBankProtocol.
    """
    global _bank_instance

    if _bank_instance is not None:
        return _bank_instance

    _bank_instance = JsonQuestionBank(questions_dir=questions_dir)
    return _bank_instance


def reset_question_bank() -> None:
    """Reset the singleton (for testing)."""
    global _bank_instance
    _bank_instance = None
