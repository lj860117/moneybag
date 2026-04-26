"""
Manage Allocation Use Case
===========================
Orchestrates domain services (allocation_service) and infra (FamilyProfileProtocol)
to compute, analyze, and manage asset allocation targets.

Dependency rule: use_cases/ -> domain/ -> infra/ (never backward to api/).

Design doc: docs/design/03-rule-engine.md §2-8
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Tuple

from domain.models.allocation import AllocationTarget, AllocationState, DeviationAnalysis
from domain.models.family import FamilyProfile
from domain.protocols.family_profile import FamilyProfileProtocol
from domain.services.allocation_service import (
    analyze_deviation,
    compute_target_allocation,
    detect_rebalance_trigger,
    validate_allocation,
)


def compute_allocation_target(
    family_id: str,
    store: FamilyProfileProtocol,
    user_override: AllocationTarget | None = None,
) -> Tuple[AllocationTarget, List[str]]:
    """Compute target allocation for a family.

    Steps:
      1. Load family profile from store
      2. Compute target using profile + optional user override
      3. Validate target allocation
      4. Return (target, errors)

    Args:
        family_id: unique family identifier
        store: injected persistence (FamilyProfileProtocol)
        user_override: optional user-provided allocation target

    Returns:
        Tuple of (AllocationTarget, error_list).
        If error_list is non-empty, target is still returned but flagged as invalid.
    """
    # 1. Load profile
    profile_data = store.load(family_id)
    if profile_data is None:
        return AllocationTarget(0, 0, 0, 0), [f"Profile for family {family_id} not found"]

    profile = FamilyProfile.from_dict(profile_data)

    # 2. Compute target
    try:
        target = compute_target_allocation(profile, user_override)
    except ValueError as e:
        return AllocationTarget(0, 0, 0, 0), [str(e)]

    # 3. Validate
    errors = validate_allocation(target)

    return target, errors


def analyze_allocation_deviation(
    current: AllocationState,
    target: AllocationTarget,
) -> DeviationAnalysis:
    """Analyze allocation deviation from target.

    Pure computation, no I/O.

    Args:
        current: actual current allocation
        target: target allocation

    Returns:
        DeviationAnalysis with severity and recommendation
    """
    return analyze_deviation(current, target)


def check_rebalance_need(
    analysis: DeviationAnalysis,
    last_rebalance_date: str = "",
) -> Tuple[bool, str]:
    """Check if rebalancing is needed based on deviation and time.

    Args:
        analysis: deviation analysis from analyze_allocation_deviation()
        last_rebalance_date: ISO 8601 date of last rebalance (empty = never rebalanced)

    Returns:
        Tuple of (should_rebalance: bool, reason: str)
    """
    if not last_rebalance_date:
        days_since = 999999  # Never rebalanced -> large number
    else:
        try:
            last_date = datetime.fromisoformat(last_rebalance_date)
            days_since = (datetime.now() - last_date).days
        except (ValueError, TypeError):
            days_since = 999999  # Invalid date -> treat as never

    should_rebalance, reason = detect_rebalance_trigger(analysis, days_since)
    return should_rebalance, reason


def save_allocation_override(
    family_id: str,
    override: AllocationTarget,
    store: FamilyProfileProtocol,
) -> Tuple[AllocationTarget, List[str]]:
    """Save user-provided allocation override to profile.

    Steps:
      1. Load profile
      2. Validate override allocation
      3. Save override in metadata/user_preferences (if applicable)
      4. Return validated target

    Note: This is a placeholder for M2 W4. Full implementation requires
    extending FamilyProfile with user_preferences or separate override store.

    Args:
        family_id: unique family identifier
        override: user-provided allocation target
        store: injected persistence (FamilyProfileProtocol)

    Returns:
        Tuple of (AllocationTarget, error_list)
    """
    # Validate override
    errors = validate_allocation(override)
    if errors:
        return override, errors

    # In M2 W4, we accept the override but don't persist (no extension to FamilyProfile yet)
    # TODO: Extend FamilyProfile with user_preferences field in M2 W5+
    return override, []


__all__ = [
    "compute_allocation_target",
    "analyze_allocation_deviation",
    "check_rebalance_need",
    "save_allocation_override",
]
