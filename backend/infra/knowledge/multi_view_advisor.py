"""
MultiViewAdvisor Implementation
================================
Loads templates from JSON and implements MultiViewAdvisorProtocol.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Any

from domain.models.multi_perspective import (
    MultiViewReview,
    MultiViewRequest,
)
from domain.services.multi_view_advisor_service import (
    generate_multi_view_review,
    derive_triggers,
    get_perspective_titles,
)
from domain.protocols.multi_view_advisor import MultiViewAdvisorProtocol

logger = logging.getLogger(__name__)


class MultiViewAdvisor:
    """MultiViewAdvisorProtocol implementation."""
    
    def __init__(self, templates_path: str | None = None) -> None:
        self._templates: Dict[str, List[Dict[str, Any]]] = {}
        if templates_path is None:
            templates_path = str(
                Path(__file__).parent / "content" / "multiviews" / "templates.json"
            )
        self._load_templates(templates_path)
    
    def _load_templates(self, path: str) -> None:
        """Load templates from JSON file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._templates = data
            logger.info(
                f"Loaded multi-view templates: "
                f"{len(data.get('conservative_graham', []))} conservative, "
                f"{len(data.get('longterm_bogle', []))} long-term, "
                f"{len(data.get('behavioral_kahneman', []))} behavioral"
            )
        except Exception as e:
            logger.error(f"Failed to load multi-view templates: {e}")
            self._templates = {
                "conservative_graham": [],
                "longterm_bogle": [],
                "behavioral_kahneman": [],
            }
    
    def generate_review(self, request: MultiViewRequest) -> MultiViewReview | None:
        """Generate multi-perspective review."""
        try:
            return generate_multi_view_review(request, self._templates)
        except Exception as e:
            logger.error(f"Error generating multi-view review: {e}")
            return None
    
    def check_triggers(self, request: MultiViewRequest) -> Dict[str, bool]:
        """Check which trigger conditions are met."""
        triggered, _ = derive_triggers(request)
        return {
            "amount_major": "amount_major" in triggered,
            "asset_class_change": "asset_class_change" in triggered,
            "concentration_breach": "concentration_breach" in triggered,
        }
    
    def get_perspective_titles(self) -> Dict[str, str]:
        """Return perspective titles."""
        return get_perspective_titles()


_advisor_instance: MultiViewAdvisor | None = None


def get_multi_view_advisor() -> MultiViewAdvisor:
    """Lazy-load singleton instance."""
    global _advisor_instance
    if _advisor_instance is None:
        _advisor_instance = MultiViewAdvisor()
    return _advisor_instance
