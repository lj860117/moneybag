"""
Rule Engine — Domain business logic for allocation, risk, and decisions
========================================================================
Contains allocation matrices, risk thresholds, and decision-guard rules.

Reference: docs/design/03-rule-engine.md

Invariant #11: All interfaces are Protocols in domain/protocols/; no imports from infra/.
"""
from domain.rule_engine.defaults import (
    AllocationDefaults,
    RiskDefaults,
    ScoringDefaults,
    RebalanceDefaults,
    StaleDataDefaults,
)

__all__ = [
    "AllocationDefaults",
    "RiskDefaults",
    "ScoringDefaults",
    "RebalanceDefaults",
    "StaleDataDefaults",
]
