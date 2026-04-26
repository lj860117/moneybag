"""
Submit Family Questionnaire Use Case
======================================
First use_case in the system (M2 W2).

Orchestrates domain service (family_profile_service) and infra (FamilyProfileProtocol)
to process and persist a family questionnaire submission.

Dependency rule: use_cases/ -> domain/ -> infra/ (never backward to api/).

Design doc: docs/design/06-family-profile.md
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from domain.models.family import FamilyProfile
from domain.protocols.family_profile import FamilyProfileProtocol
from domain.services.family_profile_service import (
    build_profile_from_questionnaire,
    validate_profile,
)


def submit_questionnaire(
    family_id: str,
    answers: Dict[str, Any],
    store: FamilyProfileProtocol,
) -> Tuple[FamilyProfile, List[str]]:
    """Process and persist a family questionnaire submission.

    Steps:
      1. Load existing profile (if updating)
      2. Build FamilyProfile from answers (merging with existing)
      3. Validate the profile
      4. If valid, persist via store
      5. Return (profile, errors)

    Args:
        family_id: unique family identifier
        answers: flat dict from the questionnaire form
        store: injected persistence (FamilyProfileProtocol)

    Returns:
        Tuple of (FamilyProfile, error_list).
        If error_list is non-empty, the profile was NOT saved.
    """
    # 1. Load existing profile for merge (update scenario)
    existing: FamilyProfile | None = None
    existing_data = store.load(family_id)
    if existing_data is not None:
        existing = FamilyProfile.from_dict(existing_data)

    # 2. Build profile from answers
    profile = build_profile_from_questionnaire(family_id, answers, existing)

    # 3. Validate
    errors = validate_profile(profile)
    if errors:
        return profile, errors

    # 4. Persist
    store.save(family_id, profile.to_dict())

    return profile, []


def get_family_profile(
    family_id: str,
    store: FamilyProfileProtocol,
) -> FamilyProfile | None:
    """Load an existing family profile.

    Returns None if not found.
    """
    data = store.load(family_id)
    if data is None:
        return None
    return FamilyProfile.from_dict(data)


def update_members(
    family_id: str,
    members_data: List[Dict[str, Any]],
    store: FamilyProfileProtocol,
) -> Tuple[FamilyProfile | None, List[str]]:
    """Update the members list of an existing family profile.

    Returns (updated_profile, errors). If family not found, returns (None, [error]).
    """
    data = store.load(family_id)
    if data is None:
        return None, [f"Family '{family_id}' not found"]

    profile = FamilyProfile.from_dict(data)
    from domain.models.family import Member
    new_members = tuple(Member.from_dict(m) for m in members_data)

    # Create updated profile with new members
    updated = FamilyProfile.from_dict({
        **profile.to_dict(),
        "members": [m.to_dict() for m in new_members],
    })

    errors = validate_profile(updated)
    if errors:
        return updated, errors

    store.save(family_id, updated.to_dict())
    return updated, []


def update_sub_accounts(
    family_id: str,
    sub_accounts_data: List[Dict[str, Any]],
    store: FamilyProfileProtocol,
) -> Tuple[FamilyProfile | None, List[str]]:
    """Update the sub-accounts list of an existing family profile.

    Returns (updated_profile, errors). If family not found, returns (None, [error]).
    """
    data = store.load(family_id)
    if data is None:
        return None, [f"Family '{family_id}' not found"]

    profile = FamilyProfile.from_dict(data)
    from domain.models.family import SubAccount
    new_subs = tuple(SubAccount.from_dict(s) for s in sub_accounts_data)

    updated = FamilyProfile.from_dict({
        **profile.to_dict(),
        "sub_accounts": [s.to_dict() for s in new_subs],
    })

    errors = validate_profile(updated)
    if errors:
        return updated, errors

    store.save(family_id, updated.to_dict())
    return updated, []
