"""
ReportGeneratorProtocol -- interface contract for monthly report generation
============================================================================
Domain-semantic interface for generating and retrieving monthly decision
quality reports.

Implementations:
  - use_cases/generate_monthly_report.py (orchestrates domain service + archive)

Invariant #11: New cross-module interfaces must have a Protocol first.
Design doc: docs/design/07-decision-guard.md §四
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ReportGeneratorProtocol(Protocol):
    """Structural interface for monthly report generation and persistence.

    Generates monthly decision quality reports from decision review archives.
    """

    def generate_report(
        self, user_id: str, year_month: str
    ) -> Dict[str, object]:
        """Generate a monthly report for the given year_month (e.g., '2026-04').

        Returns the report as a dict. Idempotent — regenerates if called again.
        """
        ...

    def get_report(
        self, user_id: str, year_month: str
    ) -> Optional[Dict[str, object]]:
        """Get a previously generated report. Returns None if not found."""
        ...

    def list_reports(
        self, user_id: str, limit: int = 12
    ) -> List[Dict[str, object]]:
        """List available reports for a user (newest first, up to limit)."""
        ...
