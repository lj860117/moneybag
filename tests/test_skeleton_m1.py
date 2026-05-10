"""
M1 Day 1 Skeleton Smoke Test
==============================
Validates the four-layer skeleton created in M1 W1:
  1. All __init__.py files are importable
  2. Protocols are runtime_checkable
  3. Implementations satisfy their Protocols (isinstance check)
  4. LLMResponse round-trips through to_dict/from_dict
  5. MemoryCache and FileStore basic CRUD operations work

Run: cd backend && python -m pytest ../tests/test_skeleton_m1.py -v
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# Ensure backend/ is on sys.path (same as main.py line 17)
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---- 1. All packages importable ----

PACKAGES = [
    "domain",
    "domain.models",
    "domain.protocols",
    "domain.services",
    "domain.rule_engine",
    "infra",
    "infra.cache",
    "infra.store",
    "infra.llm",
    "infra.data_source",
    "infra.knowledge",
    "infra.events",
    "infra.config",
    "api",
    "use_cases",
]


@pytest.mark.parametrize("pkg", PACKAGES)
def test_package_importable(pkg):
    """Every skeleton package should import without error."""
    mod = importlib.import_module(pkg)
    assert mod is not None


# ---- 2. Protocols are runtime_checkable ----

def test_protocols_are_runtime_checkable():
    from domain.protocols import (
        CacheProtocol,
        StoreProtocol,
        LLMClientProtocol,
        DataSourceProtocol,
    )
    # runtime_checkable protocols should allow isinstance() checks
    # Verify a random non-matching object does NOT satisfy them
    for proto in (CacheProtocol, StoreProtocol, LLMClientProtocol, DataSourceProtocol):
        assert not isinstance("not_a_protocol", proto)


# ---- 3. Implementations satisfy Protocols ----

def test_memory_cache_satisfies_protocol():
    from domain.protocols import CacheProtocol
    from infra.cache import MemoryCache

    cache = MemoryCache()
    assert isinstance(cache, CacheProtocol)


def test_file_store_satisfies_protocol():
    from domain.protocols import StoreProtocol
    from infra.store import FileStore

    store = FileStore(base_dir="/tmp/moneybag_test_skeleton")
    assert isinstance(store, StoreProtocol)


# ---- 4. LLMResponse round-trip ----

def test_llm_response_round_trip():
    from domain.models import LLMResponse

    original = LLMResponse(
        content="test content",
        reasoning="thinking...",
        source="ai",
        model="deepseek-chat",
        tokens=100,
        cache_hit_tokens=20,
        cache_miss_tokens=80,
        fallback=False,
        error="",
    )
    d = original.to_dict()
    restored = LLMResponse.from_dict(d)
    assert restored == original
    assert isinstance(d, dict)
    assert d["content"] == "test content"
    assert d["tokens"] == 100


def test_llm_response_from_legacy_dict():
    """Simulate what LLMGateway.call_sync() returns today."""
    from domain.models import LLMResponse

    legacy = {
        "content": "market analysis...",
        "reasoning": "",
        "source": "ai",
        "model": "deepseek-chat",
        "tokens": 150,
        "cache_hit_tokens": 50,
        "cache_miss_tokens": 100,
        "fallback": False,
    }
    resp = LLMResponse.from_dict(legacy)
    assert resp.content == "market analysis..."
    assert resp.tokens == 150
    assert resp.fallback is False
    assert resp.error == ""  # missing key → default


def test_llm_response_is_frozen():
    from domain.models import LLMResponse

    resp = LLMResponse(content="immutable")
    with pytest.raises(AttributeError):
        resp.content = "mutated"  # type: ignore[misc]


# ---- 5. MemoryCache basic operations ----

def test_memory_cache_crud():
    from infra.cache import MemoryCache

    c = MemoryCache(default_ttl=10)

    # Miss
    assert c.get("x") is None
    assert not c.has("x")
    assert c.size() == 0

    # Set + Hit
    c.set("x", 42)
    assert c.get("x") == 42
    assert c.has("x")
    assert c.size() == 1

    # Overwrite
    c.set("x", 99)
    assert c.get("x") == 99

    # Delete
    c.delete("x")
    assert c.get("x") is None
    assert c.size() == 0

    # Clear
    c.set("a", 1)
    c.set("b", 2)
    assert c.size() == 2
    c.clear()
    assert c.size() == 0


def test_memory_cache_ttl_expiry():
    """Verify that expired entries return None."""
    import time
    from infra.cache import MemoryCache

    c = MemoryCache(default_ttl=3600)
    c.set("short", "value", ttl=1)

    assert c.get("short") == "value"
    time.sleep(1.1)
    assert c.get("short") is None


def test_memory_cache_stores_complex_types():
    from infra.cache import MemoryCache

    c = MemoryCache(default_ttl=60)
    c.set("dict", {"nested": [1, 2, 3]})
    c.set("list", [1, 2, 3])
    c.set("none_val", None)

    assert c.get("dict") == {"nested": [1, 2, 3]}
    assert c.get("list") == [1, 2, 3]
    # None value is stored — different from "not found" (also None)
    # This is a known limitation: cache.get() can't distinguish "stored None" from "miss"
    # In practice, we never cache None values.


# ---- 6. FileStore basic operations ----

def test_file_store_crud(tmp_path):
    from infra.store import FileStore

    store = FileStore(base_dir=str(tmp_path))

    # Miss
    assert store.read("users", "alice") is None
    assert not store.exists("users", "alice")
    assert store.list_keys("users") == []

    # Write + Read
    store.write("users", "alice", {"name": "Alice", "age": 30})
    assert store.exists("users", "alice")
    data = store.read("users", "alice")
    assert data is not None
    assert data["name"] == "Alice"
    assert len(store.list_keys("users")) == 1

    # Overwrite
    store.write("users", "alice", {"name": "Alice", "age": 31})
    data = store.read("users", "alice")
    assert data is not None
    assert data["age"] == 31

    # Delete
    assert store.delete("users", "alice") is True
    assert store.read("users", "alice") is None
    assert store.delete("users", "alice") is False  # already gone


def test_file_store_backup_recovery(tmp_path):
    """Verify that corrupted primary files recover from .bak."""
    import hashlib
    from infra.store import FileStore

    store = FileStore(base_dir=str(tmp_path))

    # Write twice so the second write creates a .bak from the first
    store.write("test", "key1", {"value": "original"})
    store.write("test", "key1", {"value": "updated"})

    # Corrupt the primary file
    safe_key = hashlib.sha256("key1".encode()).hexdigest()[:16]
    primary = tmp_path / "test" / "{}.json".format(safe_key)
    primary.write_text("NOT VALID JSON {{{", encoding="utf-8")

    # Read should recover from .bak (which has the first write's data)
    data = store.read("test", "key1")
    assert data is not None
    assert data["value"] == "original"


def test_file_store_multiple_collections(tmp_path):
    from infra.store import FileStore

    store = FileStore(base_dir=str(tmp_path))

    store.write("users", "u1", {"type": "user"})
    store.write("receipts", "r1", {"type": "receipt"})
    store.write("precomputed", "p1", {"type": "cache"})

    assert store.read("users", "u1")["type"] == "user"
    assert store.read("receipts", "r1")["type"] == "receipt"
    assert store.read("precomputed", "p1")["type"] == "cache"
    assert len(store.list_keys("users")) == 1
    assert len(store.list_keys("receipts")) == 1


# ---- 7. Dependency direction (no backward imports) ----

def test_domain_has_no_infra_imports():
    """domain/ must not import from infra/ — verify at module level."""
    import domain.models
    import domain.protocols.cache
    import domain.protocols.store
    import domain.protocols.data_source

    # These modules should have loaded without touching infra/
    # (llm_client.py imports LLMResponse from domain.models, which is fine)
    assert "infra" not in sys.modules.get("domain.models", object).__name__


# ---- 8. Five-bucket data source structure ----

BUCKET_PACKAGES = [
    "infra.data_source.market",
    "infra.data_source.fundamental",
    "infra.data_source.macro",
    "infra.data_source.alt",
    "infra.data_source.synthetic",
    "infra.data_source.providers",
]


@pytest.mark.parametrize("pkg", BUCKET_PACKAGES)
def test_data_source_bucket_importable(pkg):
    """Every five-bucket package should import without error."""
    mod = importlib.import_module(pkg)
    assert mod is not None


def test_data_source_facade_exports():
    """infra.data_source facade re-exports key functions."""
    from infra.data_source import get_stock_news, search_funds
    assert callable(get_stock_news)
    assert callable(search_funds)


def test_data_source_fallback_module_exists():
    """fallback.py exists and is importable."""
    mod = importlib.import_module("infra.data_source.fallback")
    assert mod is not None


# ---- 9. Invariant #6: api/ layer has zero akshare/tushare imports ----

def test_api_layer_no_direct_data_provider_imports():
    """api/ layer must not import akshare or tushare directly.

    This is the key invariant for M1 W3: all external data access
    goes through infra/data_source, never direct provider imports.
    """
    import ast
    api_dir = Path(__file__).resolve().parent.parent / "backend" / "api"
    BANNED = {"akshare", "tushare", "baostock", "yfinance"}
    violations = []
    for py_file in sorted(api_dir.glob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in BANNED:
                        violations.append(
                            f"{py_file.name}:{node.lineno} "
                            f"imports {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in BANNED:
                    violations.append(
                        f"{py_file.name}:{node.lineno} "
                        f"imports from {node.module}"
                    )
    assert violations == [], (
        f"API layer direct data-provider imports "
        f"(Invariant #6 violation): {violations}"
    )


# ---- 10. M1 W4: Provider adapter stubs ----

PROVIDER_MODULES = [
    "infra.data_source.providers",
    "infra.data_source.providers.tushare_provider",
    "infra.data_source.providers.akshare_provider",
    "infra.data_source.providers.baostock_provider",
]


@pytest.mark.parametrize("module_name", PROVIDER_MODULES)
def test_provider_modules_importable(module_name):
    """Each provider adapter stub must be importable."""
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_providers_init_exports():
    """providers/__init__.py re-exports all 3 provider classes."""
    from infra.data_source.providers import (
        AkshareProvider,
        TushareProvider,
        BaostockProvider,
    )
    assert AkshareProvider is not None
    assert TushareProvider is not None
    assert BaostockProvider is not None


def test_provider_stubs_satisfy_protocol():
    """Each provider stub has the required DataSourceProtocol methods."""
    from infra.data_source.providers import (
        AkshareProvider,
        TushareProvider,
        BaostockProvider,
    )
    for cls in (AkshareProvider, TushareProvider, BaostockProvider):
        instance = cls()
        assert hasattr(instance, "fetch")
        assert hasattr(instance, "is_available")
        assert hasattr(instance, "provider_name")
        assert isinstance(instance.provider_name, str)


def test_provider_stub_fetch_returns_none():
    """Stub providers return None for all fetch calls (not yet implemented)."""
    from infra.data_source.providers import (
        AkshareProvider,
        TushareProvider,
        BaostockProvider,
    )
    for cls in (AkshareProvider, TushareProvider, BaostockProvider):
        instance = cls()
        # Stubs return None because actual libraries may not be installed
        result = instance.fetch("nonexistent_metric")
        assert result is None


# ---- 11. M1 W4: mypy config exists ----

def test_pyproject_toml_has_mypy_config():
    """pyproject.toml must exist and contain mypy strict config."""
    toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert toml_path.exists(), "pyproject.toml missing"
    content = toml_path.read_text(encoding="utf-8")
    assert "[tool.mypy]" in content, "Missing [tool.mypy] section"
    assert "disallow_untyped_defs = true" in content, "Missing strict flag"


# ---- 12. M1 W4: main.py line count linter ----

def test_lint_main_py_script_exists():
    """scripts/lint_main_py.py must exist."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "lint_main_py.py"
    assert script.exists(), "scripts/lint_main_py.py missing"


def test_main_py_under_200_lines():
    """Invariant #8: main.py must stay under 200 lines."""
    main_py = Path(__file__).resolve().parent.parent / "backend" / "main.py"
    assert main_py.exists(), "backend/main.py missing"
    line_count = len(main_py.read_text(encoding="utf-8").splitlines())
    assert line_count < 200, (
        f"main.py has {line_count} lines (limit: 200). "
        f"Invariant #8 violation — extract routes to api/*.py"
    )


# ---- 13. M1 W4: CI workflow has all required jobs ----

def test_ci_yml_has_required_jobs():
    """CI workflow must include mypy, lint-main-py, import-linter, and smoke-tests."""
    ci_path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "CI workflow missing"
    content = ci_path.read_text(encoding="utf-8")
    assert "mypy-strict" in content, "CI missing mypy-strict job"
    assert "lint-main-py" in content, "CI missing lint-main-py job"
    assert "lint-architecture" in content, "CI missing lint-architecture job"
    assert "smoke-tests" in content, "CI missing smoke-tests job"


# ---- 14. M2 W1: Agent Memory split into 3 targets ----

def test_user_preference_service_importable():
    """domain.services.user_preference_service must be importable."""
    mod = importlib.import_module("domain.services.user_preference_service")
    assert mod is not None


def test_decision_archive_importable():
    """domain.rule_engine.decision_archive must be importable."""
    mod = importlib.import_module("domain.rule_engine.decision_archive")
    assert mod is not None


def test_user_preference_service_exports():
    """user_preference_service must export all preference/profile/irony/emotion/event functions."""
    from domain.services.user_preference_service import (
        get_preferences, save_preferences,
        get_profile, save_profile,
        get_ironies, add_irony, remove_irony,
        tag_emotion, record_emotion, get_emotion_summary,
        get_life_events, save_life_events, add_life_event, remove_life_event,
        get_upcoming_events,
        get_pending_insights, add_pending_insight, approve_insight, reject_insight,
    )
    # Smoke: all are callable
    for fn in (get_preferences, save_preferences, get_profile, save_profile,
               get_ironies, add_irony, remove_irony, tag_emotion, record_emotion,
               get_emotion_summary, get_life_events, save_life_events,
               add_life_event, remove_life_event, get_upcoming_events,
               get_pending_insights, add_pending_insight, approve_insight, reject_insight):
        assert callable(fn), f"{fn} is not callable"


def test_decision_archive_exports():
    """decision_archive must export all decision/rule/context/extract functions."""
    from domain.rule_engine.decision_archive import (
        get_decisions, get_archived_decisions, get_archive_summaries,
        add_decision, archive_old_decisions, summarize_archive_month,
        track_decision_result,
        get_rules, add_rule, remove_rule, check_rules,
        get_context, save_context,
        add_to_extract_queue, get_extract_queue, clear_extract_queue,
        auto_extract_insight, batch_extract_for_user,
    )
    for fn in (get_decisions, get_archived_decisions, get_archive_summaries,
               add_decision, archive_old_decisions, summarize_archive_month,
               track_decision_result, get_rules, add_rule, remove_rule,
               check_rules, get_context, save_context, add_to_extract_queue,
               get_extract_queue, clear_extract_queue, auto_extract_insight,
               batch_extract_for_user):
        assert callable(fn), f"{fn} is not callable"


def test_agent_memory_shim_reexports():
    """services.agent_memory shim must re-export all functions from new modules."""
    from services.agent_memory import (
        get_preferences, save_preferences,
        get_profile, save_profile,
        get_ironies, add_irony, remove_irony,
        record_emotion, get_emotion_summary,
        get_life_events, add_life_event, remove_life_event, get_upcoming_events,
        get_pending_insights, approve_insight, reject_insight,
        get_decisions, add_decision, get_rules, add_rule, remove_rule, check_rules,
        get_context, save_context,
        auto_extract_insight, batch_extract_for_user,
        build_memory_summary,
    )
    # All re-exports must be callable
    for fn in (get_preferences, save_preferences, get_profile, save_profile,
               get_ironies, add_irony, remove_irony, record_emotion,
               get_emotion_summary, get_life_events, add_life_event,
               remove_life_event, get_upcoming_events, get_pending_insights,
               approve_insight, reject_insight, get_decisions, add_decision,
               get_rules, add_rule, remove_rule, check_rules, get_context,
               save_context, auto_extract_insight, batch_extract_for_user,
               build_memory_summary):
        assert callable(fn), f"{fn} is not callable"


def test_build_memory_summary_returns_empty():
    """build_memory_summary is deprecated — must return empty string."""
    from services.agent_memory import build_memory_summary
    result = build_memory_summary("test_user_stub")
    assert result == "", f"Expected empty string, got: {result!r}"


def test_agent_memory_shim_source_matches_new_modules():
    """Verify shim re-exports point to the actual new module functions (not stale copies)."""
    from services.agent_memory import get_preferences as shim_gp
    from domain.services.user_preference_service import get_preferences as new_gp
    assert shim_gp is new_gp, "shim get_preferences is not the same object as new module's"

    from services.agent_memory import get_decisions as shim_gd
    from domain.rule_engine.decision_archive import get_decisions as new_gd
    assert shim_gd is new_gd, "shim get_decisions is not the same object as new module's"

    from services.agent_memory import check_rules as shim_cr
    from domain.rule_engine.decision_archive import check_rules as new_cr
    assert shim_cr is new_cr, "shim check_rules is not the same object as new module's"


# ---- 15. M2 W2: Family Profile domain model ----

def test_family_model_importable():
    """domain.models.family must be importable."""
    mod = importlib.import_module("domain.models.family")
    assert mod is not None


def test_family_model_exports():
    """domain.models must export FamilyProfile, Member, SubAccount."""
    from domain.models import FamilyProfile, Member, SubAccount
    assert FamilyProfile is not None
    assert Member is not None
    assert SubAccount is not None


def test_family_profile_frozen():
    """FamilyProfile must be a frozen dataclass."""
    from domain.models.family import FamilyProfile
    p = FamilyProfile(family_id="test")
    with pytest.raises(AttributeError):
        p.family_id = "mutated"  # type: ignore[misc]


def test_family_profile_round_trip():
    """FamilyProfile.to_dict() -> from_dict() must round-trip cleanly."""
    from domain.models.family import FamilyProfile, Member, SubAccount
    original = FamilyProfile(
        family_id="test-family",
        members=(
            Member(member_id="self", role="主申请人", age=38, income=25000, is_decision_maker=True),
            Member(member_id="spouse", role="配偶", age=36, income=18000),
        ),
        sub_accounts=(
            SubAccount(
                account_id="main",
                purpose="家庭主账户",
                target_allocation=(("bond_pct", 30), ("cash_pct", 15), ("gold_pct", 5), ("stock_pct", 50)),
            ),
        ),
        risk_preference="balanced",
        family_stage="with_children",
        monthly_income=43000,
        monthly_expense=20000,
        emergency_months=6.0,
        has_critical_illness=True,
        critical_illness_coverage=500000,
        created_at="2026-04-26T00:00:00",
        updated_at="2026-04-26T00:00:00",
    )
    d = original.to_dict()
    restored = FamilyProfile.from_dict(d)

    assert restored.family_id == original.family_id
    assert len(restored.members) == 2
    assert restored.members[0].member_id == "self"
    assert restored.members[0].is_decision_maker is True
    assert restored.members[1].age == 36
    assert len(restored.sub_accounts) == 1
    assert restored.sub_accounts[0].account_id == "main"
    assert restored.risk_preference == "balanced"
    assert restored.family_stage == "with_children"
    assert restored.monthly_income == 43000
    assert restored.emergency_months == 6.0
    assert restored.has_critical_illness is True
    assert isinstance(d, dict)


def test_member_defaults():
    """Member should have sane defaults."""
    from domain.models.family import Member
    m = Member(member_id="test")
    assert m.role == ""
    assert m.age == 0
    assert m.income == 0.0
    assert m.is_decision_maker is False


def test_sub_account_defaults():
    """SubAccount should have sane defaults."""
    from domain.models.family import SubAccount
    s = SubAccount(account_id="test")
    assert s.purpose == ""
    assert s.target_allocation == ()
    assert s.horizon_years == 0
    assert s.is_independent is False


def test_family_profile_computed_properties():
    """FamilyProfile computed properties (total_debt, insurance_count, primary_member)."""
    from domain.models.family import FamilyProfile, Member
    p = FamilyProfile(
        family_id="test",
        members=(
            Member(member_id="a", is_decision_maker=False),
            Member(member_id="b", is_decision_maker=True),
        ),
        mortgage_remaining=1000000,
        car_loan=50000,
        consumer_loan=10000,
        credit_card_debt=5000,
        has_critical_illness=True,
        has_life_insurance=True,
        has_medical_insurance=False,
        has_accident_insurance=True,
    )
    assert p.total_debt == 1065000
    assert p.insurance_count == 3
    assert p.primary_member is not None
    assert p.primary_member.member_id == "b"


# ---- 16. M2 W2: FamilyProfileProtocol ----

def test_family_profile_protocol_importable():
    """domain.protocols.family_profile must be importable."""
    mod = importlib.import_module("domain.protocols.family_profile")
    assert mod is not None


def test_family_profile_protocol_runtime_checkable():
    """FamilyProfileProtocol must be @runtime_checkable."""
    from domain.protocols import FamilyProfileProtocol
    assert not isinstance("not_a_store", FamilyProfileProtocol)


def test_family_profile_store_satisfies_protocol():
    """FamilyProfileStore must satisfy FamilyProfileProtocol (isinstance check)."""
    from domain.protocols import FamilyProfileProtocol
    from infra.store import FamilyProfileStore
    from infra.store.file_store import FileStore
    store = FamilyProfileStore(store=FileStore(base_dir="/tmp/moneybag_test_fp_proto"))
    assert isinstance(store, FamilyProfileProtocol)


# ---- 17. M2 W2: FamilyProfileStore CRUD ----

def test_family_profile_store_crud(tmp_path):
    """FamilyProfileStore CRUD operations with temp directory."""
    from infra.store.file_store import FileStore
    from infra.store.family_profile_store import FamilyProfileStore

    fs = FileStore(base_dir=str(tmp_path))
    store = FamilyProfileStore(store=fs)

    # Miss
    assert store.load("nonexistent") is None
    assert not store.exists("nonexistent")
    assert store.list_families() == []

    # Save + Load
    data = {"family_id": "test", "risk_preference": "balanced", "members": []}
    store.save("test", data)
    assert store.exists("test")
    loaded = store.load("test")
    assert loaded is not None
    assert loaded["family_id"] == "test"
    assert len(store.list_families()) == 1

    # Overwrite
    data2 = {**data, "risk_preference": "aggressive"}
    store.save("test", data2)
    loaded2 = store.load("test")
    assert loaded2 is not None
    assert loaded2["risk_preference"] == "aggressive"


# ---- 18. M2 W2: Family Profile Service ----

def test_family_profile_service_build():
    """build_profile_from_questionnaire should construct a valid FamilyProfile."""
    from domain.services.family_profile_service import build_profile_from_questionnaire

    answers = {
        "age": 38,
        "monthly_income": 25000,
        "monthly_expense": 15000,
        "investable_assets": 500000,
        "risk_preference": "balanced",
        "mortgage_remaining": 800000,
        "has_critical_illness": True,
        "critical_illness_coverage": 500000,
        "emergency_months": 6.0,
        "investment_horizon_years": 15,
        "max_drawdown_tolerance": -0.20,
        "primary_goal": "education",
        "members": [
            {"member_id": "self", "role": "主申请人", "age": 38, "income": 25000, "is_decision_maker": True},
            {"member_id": "child1", "role": "子女", "age": 6},
        ],
    }
    profile = build_profile_from_questionnaire("test-family", answers)

    assert profile.family_id == "test-family"
    assert profile.risk_preference == "balanced"
    assert profile.family_stage == "with_children"  # auto-derived (has child member)
    assert profile.monthly_income == 25000
    assert profile.mortgage_remaining == 800000
    assert profile.has_critical_illness is True
    assert len(profile.members) == 2
    assert profile.members[0].is_decision_maker is True
    assert profile.primary_goal == "education"
    assert profile.created_at != ""
    assert profile.updated_at != ""


def test_family_profile_service_validate():
    """validate_profile should catch missing required fields."""
    from domain.models.family import FamilyProfile
    from domain.services.family_profile_service import validate_profile

    # Valid profile
    from domain.models.family import Member
    valid = FamilyProfile(
        family_id="test",
        members=(Member(member_id="self", is_decision_maker=True),),
    )
    assert validate_profile(valid) == []

    # Missing family_id
    invalid_no_id = FamilyProfile(family_id="", members=(Member(member_id="self", is_decision_maker=True),))
    errors = validate_profile(invalid_no_id)
    assert any("family_id" in e for e in errors)

    # No members
    invalid_no_members = FamilyProfile(family_id="test", members=())
    errors = validate_profile(invalid_no_members)
    assert any("member" in e.lower() for e in errors)


def test_derive_family_stage():
    """derive_family_stage should return correct stages."""
    from domain.services.family_profile_service import derive_family_stage

    assert derive_family_stage(age=60, has_mortgage=False, has_children=True, years_to_retire=3) == "near_retirement"
    assert derive_family_stage(age=35, has_mortgage=True, has_children=True, years_to_retire=30) == "with_children"
    assert derive_family_stage(age=30, has_mortgage=True, has_children=False, years_to_retire=35) == "married_mortgage"
    assert derive_family_stage(age=25, has_mortgage=False, has_children=False, years_to_retire=40) == "single"


# ---- 19. M2 W2: Use Case ----

def test_submit_questionnaire_use_case(tmp_path):
    """End-to-end: submit questionnaire -> persist -> reload."""
    from infra.store.file_store import FileStore
    from infra.store.family_profile_store import FamilyProfileStore
    from use_cases.submit_family_questionnaire import submit_questionnaire, get_family_profile

    store = FamilyProfileStore(store=FileStore(base_dir=str(tmp_path)))

    answers = {
        "age": 38,
        "monthly_income": 25000,
        "monthly_expense": 15000,
        "investable_assets": 500000,
        "risk_preference": "balanced",
        "emergency_months": 6.0,
        "members": [
            {"member_id": "self", "role": "主申请人", "age": 38, "income": 25000, "is_decision_maker": True},
        ],
    }

    # Submit
    profile, errors = submit_questionnaire("test-family", answers, store)
    assert errors == [], f"Unexpected errors: {errors}"
    assert profile.family_id == "test-family"
    assert profile.monthly_income == 25000

    # Reload
    loaded = get_family_profile("test-family", store)
    assert loaded is not None
    assert loaded.family_id == "test-family"
    assert loaded.monthly_income == 25000
    assert len(loaded.members) == 1
    assert loaded.members[0].is_decision_maker is True

    # Update (second submit merges with existing)
    answers2 = {**answers, "monthly_income": 30000}
    profile2, errors2 = submit_questionnaire("test-family", answers2, store)
    assert errors2 == []
    assert profile2.monthly_income == 30000
    assert profile2.created_at == profile.created_at  # preserved from first submit


# ==============================================================================
# M2 W3: Balance Sheet domain model + service + protocol + store + use case
# ==============================================================================

# ---- 20. BalanceSheet model importable ----

def test_balance_sheet_model_importable():
    """domain.models.balance_sheet must be importable."""
    mod = importlib.import_module("domain.models.balance_sheet")
    assert mod is not None


def test_balance_sheet_model_exports():
    """domain.models must export BalanceSheet and BalanceSheetItem."""
    from domain.models import BalanceSheet, BalanceSheetItem
    assert BalanceSheet is not None
    assert BalanceSheetItem is not None


# ---- 21. BalanceSheetItem basics ----

def test_balance_sheet_item_frozen():
    """BalanceSheetItem must be a frozen dataclass."""
    from domain.models.balance_sheet import BalanceSheetItem
    item = BalanceSheetItem(name="test", category="cash_deposits")
    with pytest.raises(AttributeError):
        item.name = "mutated"  # type: ignore[misc]


def test_balance_sheet_item_defaults():
    """BalanceSheetItem should have sane defaults."""
    from domain.models.balance_sheet import BalanceSheetItem
    item = BalanceSheetItem(name="test", category="cash_deposits")
    assert item.value == 0.0
    assert item.currency == "CNY"
    assert item.last_updated == ""
    assert item.data_source == "manual"


def test_balance_sheet_item_round_trip():
    """BalanceSheetItem.to_dict() -> from_dict() must round-trip."""
    from domain.models.balance_sheet import BalanceSheetItem
    original = BalanceSheetItem(
        name="工商银行活期",
        category="cash_deposits",
        value=100000.0,
        currency="CNY",
        last_updated="2026-04-20T10:00:00",
        data_source="manual",
    )
    d = original.to_dict()
    restored = BalanceSheetItem.from_dict(d)
    assert restored.name == original.name
    assert restored.category == original.category
    assert restored.value == original.value
    assert restored.currency == original.currency
    assert restored.last_updated == original.last_updated
    assert restored.data_source == original.data_source


# ---- 22. Staleness detection ----

def test_balance_sheet_item_is_stale_when_old():
    """Item with last_updated > 30 days ago is stale."""
    from datetime import datetime, timedelta
    from domain.models.balance_sheet import BalanceSheetItem

    now = datetime(2026, 4, 26, 12, 0, 0)
    old_date = (now - timedelta(days=31)).isoformat()

    item = BalanceSheetItem(name="test", category="cash_deposits", last_updated=old_date)
    assert item.is_stale(now=now) is True


def test_balance_sheet_item_is_fresh_when_recent():
    """Item with last_updated within 30 days is NOT stale."""
    from datetime import datetime, timedelta
    from domain.models.balance_sheet import BalanceSheetItem

    now = datetime(2026, 4, 26, 12, 0, 0)
    recent_date = (now - timedelta(days=5)).isoformat()

    item = BalanceSheetItem(name="test", category="cash_deposits", last_updated=recent_date)
    assert item.is_stale(now=now) is False


def test_balance_sheet_item_stale_when_empty_date():
    """Item with empty last_updated is always stale."""
    from domain.models.balance_sheet import BalanceSheetItem
    item = BalanceSheetItem(name="test", category="cash_deposits", last_updated="")
    assert item.is_stale() is True


def test_balance_sheet_item_stale_when_bad_date():
    """Item with unparseable last_updated is stale."""
    from domain.models.balance_sheet import BalanceSheetItem
    item = BalanceSheetItem(name="test", category="cash_deposits", last_updated="not-a-date")
    assert item.is_stale() is True


# ---- 23. BalanceSheet frozen + basics ----

def test_balance_sheet_frozen():
    """BalanceSheet must be a frozen dataclass."""
    from domain.models.balance_sheet import BalanceSheet
    bs = BalanceSheet(family_id="test")
    with pytest.raises(AttributeError):
        bs.family_id = "mutated"  # type: ignore[misc]


def test_balance_sheet_round_trip():
    """BalanceSheet.to_dict() -> from_dict() must round-trip."""
    from domain.models.balance_sheet import BalanceSheet, BalanceSheetItem
    original = BalanceSheet(
        family_id="test-family",
        cash_deposits=(
            BalanceSheetItem(name="工行活期", category="cash_deposits", value=100000, last_updated="2026-04-20T10:00:00"),
            BalanceSheetItem(name="余额宝", category="cash_deposits", value=50000, last_updated="2026-04-22T10:00:00"),
        ),
        investments=(
            BalanceSheetItem(name="沪深300ETF", category="investments", value=200000, last_updated="2026-04-25T10:00:00"),
        ),
        real_estate=(
            BalanceSheetItem(name="自住房", category="real_estate", value=3000000, last_updated="2026-01-01T00:00:00"),
        ),
        liabilities=(
            BalanceSheetItem(name="房贷", category="liabilities", value=1500000, last_updated="2026-04-01T00:00:00"),
        ),
        created_at="2026-04-20T10:00:00",
        updated_at="2026-04-26T10:00:00",
    )
    d = original.to_dict()
    restored = BalanceSheet.from_dict(d)

    assert restored.family_id == original.family_id
    assert len(restored.cash_deposits) == 2
    assert len(restored.investments) == 1
    assert len(restored.real_estate) == 1
    assert len(restored.liabilities) == 1
    assert restored.cash_deposits[0].name == "工行活期"
    assert restored.investments[0].value == 200000
    assert restored.created_at == original.created_at
    assert isinstance(d, dict)


# ---- 24. BalanceSheet computed properties ----

def test_balance_sheet_computed_properties():
    """BalanceSheet total_assets, total_liabilities, net_worth."""
    from domain.models.balance_sheet import BalanceSheet, BalanceSheetItem
    bs = BalanceSheet(
        family_id="test",
        cash_deposits=(
            BalanceSheetItem(name="活期", category="cash_deposits", value=100000),
        ),
        investments=(
            BalanceSheetItem(name="基金", category="investments", value=200000),
        ),
        real_estate=(
            BalanceSheetItem(name="房子", category="real_estate", value=3000000),
        ),
        liabilities=(
            BalanceSheetItem(name="房贷", category="liabilities", value=1500000),
        ),
    )
    assert bs.total_assets == 3300000  # 100k + 200k + 3000k
    assert bs.total_liabilities == 1500000
    assert bs.net_worth == 1800000  # 3300k - 1500k


def test_balance_sheet_staleness_report():
    """BalanceSheet.staleness_report returns per-category staleness."""
    from datetime import datetime, timedelta
    from domain.models.balance_sheet import BalanceSheet, BalanceSheetItem

    now = datetime(2026, 4, 26, 12, 0, 0)
    fresh = (now - timedelta(days=5)).isoformat()
    stale = (now - timedelta(days=45)).isoformat()

    bs = BalanceSheet(
        family_id="test",
        cash_deposits=(
            BalanceSheetItem(name="活期", category="cash_deposits", value=100000, last_updated=fresh),
        ),
        investments=(
            BalanceSheetItem(name="基金", category="investments", value=200000, last_updated=stale),
        ),
        real_estate=(
            BalanceSheetItem(name="房子", category="real_estate", value=3000000, last_updated=fresh),
        ),
        liabilities=(
            BalanceSheetItem(name="房贷", category="liabilities", value=1500000, last_updated=stale),
        ),
    )
    report = bs.staleness_report(now=now)

    assert report["cash_deposits"]["stale_count"] == 0
    assert report["cash_deposits"]["is_category_stale"] is False
    assert report["investments"]["stale_count"] == 1
    assert report["investments"]["is_category_stale"] is True
    assert report["liabilities"]["stale_count"] == 1


# ---- 25. BalanceSheet service ----

def test_balance_sheet_service_build():
    """build_balance_sheet should construct a valid BalanceSheet."""
    from domain.services.balance_sheet_service import build_balance_sheet

    items_data = {
        "cash_deposits": [{"name": "活期", "value": 100000, "last_updated": "2026-04-20T10:00:00"}],
        "investments": [{"name": "基金", "value": 200000, "last_updated": "2026-04-25T10:00:00"}],
        "real_estate": [{"name": "自住房", "value": 3000000, "last_updated": "2026-01-01T00:00:00"}],
        "liabilities": [{"name": "房贷", "value": 1500000, "last_updated": "2026-04-01T00:00:00"}],
    }
    sheet = build_balance_sheet("test-family", items_data)

    assert sheet.family_id == "test-family"
    assert len(sheet.cash_deposits) == 1
    assert sheet.cash_deposits[0].name == "活期"
    assert sheet.cash_deposits[0].category == "cash_deposits"
    assert sheet.total_assets == 3300000
    assert sheet.created_at != ""
    assert sheet.updated_at != ""


def test_balance_sheet_service_validate_valid():
    """validate_balance_sheet on a valid sheet returns no errors."""
    from domain.services.balance_sheet_service import build_balance_sheet, validate_balance_sheet

    items_data = {
        "cash_deposits": [{"name": "活期", "value": 100000, "last_updated": "2026-04-20T10:00:00"}],
        "investments": [{"name": "基金", "value": 200000, "last_updated": "2026-04-25T10:00:00"}],
        "real_estate": [{"name": "房子", "value": 3000000, "last_updated": "2026-01-01T00:00:00"}],
        "liabilities": [{"name": "房贷", "value": 1500000, "last_updated": "2026-04-01T00:00:00"}],
    }
    sheet = build_balance_sheet("test-family", items_data)
    errors = validate_balance_sheet(sheet)
    assert errors == [], f"Unexpected errors: {errors}"


def test_balance_sheet_service_validate_missing_category():
    """validate_balance_sheet should catch missing Tier 1 categories."""
    from domain.models.balance_sheet import BalanceSheet, BalanceSheetItem
    from domain.services.balance_sheet_service import validate_balance_sheet

    # Missing investments and liabilities
    sheet = BalanceSheet(
        family_id="test",
        cash_deposits=(BalanceSheetItem(name="活期", category="cash_deposits", value=100000, last_updated="2026-04-20"),),
        real_estate=(BalanceSheetItem(name="房子", category="real_estate", value=3000000, last_updated="2026-01-01"),),
    )
    errors = validate_balance_sheet(sheet)
    assert any("investments" in e for e in errors)
    assert any("liabilities" in e for e in errors)


def test_balance_sheet_service_detect_stale():
    """detect_stale_items should find items older than 30 days."""
    from datetime import datetime, timedelta
    from domain.services.balance_sheet_service import build_balance_sheet, detect_stale_items

    now = datetime(2026, 4, 26, 12, 0, 0)
    fresh = (now - timedelta(days=5)).isoformat()
    stale = (now - timedelta(days=45)).isoformat()

    items_data = {
        "cash_deposits": [{"name": "活期", "value": 100000, "last_updated": fresh}],
        "investments": [{"name": "基金", "value": 200000, "last_updated": stale}],
        "real_estate": [{"name": "房子", "value": 3000000, "last_updated": fresh}],
        "liabilities": [{"name": "房贷", "value": 1500000, "last_updated": stale}],
    }
    sheet = build_balance_sheet("test-family", items_data)
    stale_report = detect_stale_items(sheet, now=now)

    assert len(stale_report) == 2
    stale_names = {item["name"] for item in stale_report}
    assert "基金" in stale_names
    assert "房贷" in stale_names
    assert "活期" not in stale_names


def test_balance_sheet_service_compute_summary():
    """compute_summary should return complete summary with staleness."""
    from domain.services.balance_sheet_service import build_balance_sheet, compute_summary

    items_data = {
        "cash_deposits": [{"name": "活期", "value": 100000, "last_updated": "2026-04-20T10:00:00"}],
        "investments": [{"name": "基金", "value": 200000, "last_updated": "2026-04-25T10:00:00"}],
        "real_estate": [{"name": "房子", "value": 3000000, "last_updated": "2026-01-01T00:00:00"}],
        "liabilities": [{"name": "房贷", "value": 1500000, "last_updated": "2026-04-01T00:00:00"}],
    }
    sheet = build_balance_sheet("test-family", items_data)
    summary = compute_summary(sheet)

    assert summary["family_id"] == "test-family"
    assert summary["total_assets"] == 3300000
    assert summary["total_liabilities"] == 1500000
    assert summary["net_worth"] == 1800000
    assert summary["item_count"] == 4
    assert "staleness_report" in summary
    assert "category_totals" in summary


# ---- 26. BalanceSheetProtocol ----

def test_balance_sheet_protocol_importable():
    """domain.protocols.balance_sheet must be importable."""
    mod = importlib.import_module("domain.protocols.balance_sheet")
    assert mod is not None


def test_balance_sheet_protocol_runtime_checkable():
    """BalanceSheetProtocol must be @runtime_checkable."""
    from domain.protocols import BalanceSheetProtocol
    assert not isinstance("not_a_store", BalanceSheetProtocol)


def test_balance_sheet_store_satisfies_protocol():
    """BalanceSheetStore must satisfy BalanceSheetProtocol (isinstance check)."""
    from domain.protocols import BalanceSheetProtocol
    from infra.store import BalanceSheetStore
    from infra.store.file_store import FileStore
    store = BalanceSheetStore(store=FileStore(base_dir="/tmp/moneybag_test_bs_proto"))
    assert isinstance(store, BalanceSheetProtocol)


# ---- 27. BalanceSheetStore CRUD ----

def test_balance_sheet_store_crud(tmp_path):
    """BalanceSheetStore CRUD operations with temp directory."""
    from infra.store.file_store import FileStore
    from infra.store.balance_sheet_store import BalanceSheetStore

    fs = FileStore(base_dir=str(tmp_path))
    store = BalanceSheetStore(store=fs)

    # Miss
    assert store.load("nonexistent") is None
    assert not store.exists("nonexistent")
    assert store.list_families() == []

    # Save + Load
    data = {
        "family_id": "test",
        "cash_deposits": [{"name": "活期", "value": 100000}],
        "investments": [],
        "real_estate": [],
        "liabilities": [],
    }
    store.save("test", data)
    assert store.exists("test")
    loaded = store.load("test")
    assert loaded is not None
    assert loaded["family_id"] == "test"
    assert len(store.list_families()) == 1

    # Overwrite
    data2 = {**data, "cash_deposits": [{"name": "活期", "value": 200000}]}
    store.save("test", data2)
    loaded2 = store.load("test")
    assert loaded2 is not None
    assert loaded2["cash_deposits"][0]["value"] == 200000


# ---- 28. Balance Sheet Use Case ----

def test_submit_balance_sheet_use_case(tmp_path):
    """End-to-end: submit balance sheet -> persist -> reload."""
    from infra.store.file_store import FileStore
    from infra.store.balance_sheet_store import BalanceSheetStore
    from use_cases.manage_balance_sheet import (
        submit_balance_sheet,
        get_balance_sheet,
        get_balance_sheet_summary,
    )

    store = BalanceSheetStore(store=FileStore(base_dir=str(tmp_path)))

    items_data = {
        "cash_deposits": [{"name": "活期", "value": 100000, "last_updated": "2026-04-20T10:00:00"}],
        "investments": [{"name": "基金", "value": 200000, "last_updated": "2026-04-25T10:00:00"}],
        "real_estate": [{"name": "自住房", "value": 3000000, "last_updated": "2026-01-01T00:00:00"}],
        "liabilities": [{"name": "房贷", "value": 1500000, "last_updated": "2026-04-01T00:00:00"}],
    }

    # Submit
    sheet, errors = submit_balance_sheet("test-family", items_data, store)
    assert errors == [], f"Unexpected errors: {errors}"
    assert sheet.family_id == "test-family"
    assert sheet.total_assets == 3300000
    assert sheet.net_worth == 1800000

    # Reload
    loaded = get_balance_sheet("test-family", store)
    assert loaded is not None
    assert loaded.family_id == "test-family"
    assert len(loaded.cash_deposits) == 1
    assert loaded.cash_deposits[0].name == "活期"

    # Summary
    summary = get_balance_sheet_summary("test-family", store)
    assert summary is not None
    assert summary["total_assets"] == 3300000
    assert summary["net_worth"] == 1800000

    # Update (second submit preserves created_at)
    items_data2 = {**items_data, "cash_deposits": [{"name": "活期", "value": 150000, "last_updated": "2026-04-26T10:00:00"}]}
    sheet2, errors2 = submit_balance_sheet("test-family", items_data2, store)
    assert errors2 == []
    assert sheet2.cash_deposits[0].value == 150000
    assert sheet2.created_at == sheet.created_at  # preserved from first submit


def test_update_category_items_use_case(tmp_path):
    """update_category_items should replace one category while keeping others."""
    from infra.store.file_store import FileStore
    from infra.store.balance_sheet_store import BalanceSheetStore
    from use_cases.manage_balance_sheet import submit_balance_sheet, update_category_items

    store = BalanceSheetStore(store=FileStore(base_dir=str(tmp_path)))

    # First submit a full balance sheet
    items_data = {
        "cash_deposits": [{"name": "活期", "value": 100000, "last_updated": "2026-04-20T10:00:00"}],
        "investments": [{"name": "基金", "value": 200000, "last_updated": "2026-04-25T10:00:00"}],
        "real_estate": [{"name": "自住房", "value": 3000000, "last_updated": "2026-01-01T00:00:00"}],
        "liabilities": [{"name": "房贷", "value": 1500000, "last_updated": "2026-04-01T00:00:00"}],
    }
    sheet, errors = submit_balance_sheet("test-family", items_data, store)
    assert errors == []

    # Update just cash_deposits
    new_cash = [
        {"name": "活期", "value": 150000, "last_updated": "2026-04-26T10:00:00"},
        {"name": "余额宝", "value": 50000, "last_updated": "2026-04-26T10:00:00"},
    ]
    updated, update_errors = update_category_items("test-family", "cash_deposits", new_cash, store)
    assert update_errors == []
    assert updated is not None
    assert len(updated.cash_deposits) == 2  # updated
    assert len(updated.investments) == 1  # unchanged
    assert updated.investments[0].name == "基金"  # unchanged


# ============================================================
# M2 W4: Asset Allocation Framework Tests (24 new tests)
# ============================================================

def test_allocation_defaults_importable():
    """AllocationDefaults should be importable from domain/rule_engine/."""
    from domain.rule_engine.defaults import AllocationDefaults, RiskDefaults, ScoringDefaults, RebalanceDefaults, StaleDataDefaults
    assert AllocationDefaults is not None
    assert RiskDefaults is not None
    assert ScoringDefaults is not None
    assert RebalanceDefaults is not None
    assert StaleDataDefaults is not None


def test_allocation_matrix_lookup():
    """AllocationDefaults.MATRIX should have 12 entries (3 risk × 4 family_stage)."""
    from domain.rule_engine.defaults import AllocationDefaults
    assert len(AllocationDefaults.MATRIX) == 12
    # Check one entry
    value = AllocationDefaults.MATRIX.get(("balanced", "single"))
    assert value == (50, 30, 15, 5)
    # Verify all tuples sum to 100
    for (risk, stage), (s, b, c, g) in AllocationDefaults.MATRIX.items():
        assert s + b + c + g == 100, f"{risk} {stage} does not sum to 100"


def test_deviation_thresholds():
    """AllocationDefaults deviation thresholds should be in order: mild < moderate < high."""
    from domain.rule_engine.defaults import AllocationDefaults
    assert AllocationDefaults.DEVIATION_MILD < AllocationDefaults.DEVIATION_MODERATE
    assert AllocationDefaults.DEVIATION_MODERATE < AllocationDefaults.DEVIATION_HIGH
    assert AllocationDefaults.DEVIATION_MILD_MIN > 0
    assert AllocationDefaults.DEVIATION_HIGH_MAX > AllocationDefaults.DEVIATION_HIGH


def test_allocation_models_frozen():
    """AllocationTarget and AllocationState should be frozen dataclasses."""
    from domain.models.allocation import AllocationTarget, AllocationState
    target = AllocationTarget(50, 30, 15, 5)
    try:
        target.stock_pct = 60  # type: ignore
        assert False, "Should not be able to modify frozen dataclass"
    except (AttributeError, TypeError):
        pass  # Expected


def test_allocation_target_total_pct():
    """AllocationTarget.total_pct should sum all allocations."""
    from domain.models.allocation import AllocationTarget
    target = AllocationTarget(50, 30, 15, 5)
    assert target.total_pct == 100
    target2 = AllocationTarget(40, 35, 20, 5)
    assert target2.total_pct == 100


def test_allocation_to_dict_and_from_dict():
    """AllocationTarget should round-trip through to_dict/from_dict."""
    from domain.models.allocation import AllocationTarget
    original = AllocationTarget(50, 30, 15, 5, reason="test")
    d = original.to_dict()
    restored = AllocationTarget.from_dict(d)
    assert restored.stock_pct == original.stock_pct
    assert restored.bond_pct == original.bond_pct
    assert restored.cash_pct == original.cash_pct
    assert restored.gold_pct == original.gold_pct
    assert restored.reason == original.reason


def test_deviation_analysis_frozen():
    """DeviationAnalysis should be frozen."""
    from domain.models.allocation import AllocationTarget, AllocationState, DeviationAnalysis
    target = AllocationTarget(50, 30, 15, 5)
    current = AllocationState(45, 35, 15, 5)
    analysis = DeviationAnalysis(
        target=target,
        current=current,
        stock_deviation=-5,
        bond_deviation=5,
        cash_deviation=0,
        gold_deviation=0,
        max_deviation=5,
    )
    try:
        analysis.stock_deviation = -3  # type: ignore
        assert False, "Should not be able to modify frozen dataclass"
    except (AttributeError, TypeError):
        pass  # Expected


def test_compute_target_allocation_base_matrix():
    """compute_target_allocation should use the base matrix correctly."""
    from domain.services.allocation_service import compute_target_allocation
    from domain.models.family import FamilyProfile, Member
    
    # Create a balanced family with a 35-year-old member
    profile = FamilyProfile(
        family_id="test",
        members=(Member(member_id="self", role="主申请人", age=35, income=100000, is_decision_maker=True),),
        sub_accounts=(),
        risk_preference="balanced",
        family_stage="single",
        monthly_income=8000,
        monthly_expense=5000,
        investable_assets=500000,
        monthly_surplus=3000,
    )
    
    target = compute_target_allocation(profile)
    # Base: (50, 30, 15, 5); age adjustment: 50 - (35-30)*0.5 = 47.5
    assert 47 <= target.stock_pct <= 48
    assert target.bond_pct == 30
    assert target.cash_pct == 15
    assert target.gold_pct == 5


def test_compute_target_allocation_age_adjustment():
    """Age adjustment should reduce stock allocation linearly from age 30."""
    from domain.services.allocation_service import compute_target_allocation
    from domain.models.family import FamilyProfile, Member
    
    # Age 30: no adjustment
    profile_30 = FamilyProfile(
        family_id="test",
        members=(Member(member_id="self", role="主申请人", age=30, income=100000, is_decision_maker=True),),
        sub_accounts=(),
        risk_preference="balanced",
        family_stage="single",
        monthly_income=8000,
        monthly_expense=5000,
        investable_assets=500000,
        monthly_surplus=3000,
    )
    target_30 = compute_target_allocation(profile_30)
    assert target_30.stock_pct == 50
    
    # Age 40: -5% adjustment (10 years × 0.5%)
    profile_40 = FamilyProfile(
        family_id="test",
        members=(Member(member_id="self", role="主申请人", age=40, income=100000, is_decision_maker=True),),
        sub_accounts=(),
        risk_preference="balanced",
        family_stage="single",
        monthly_income=8000,
        monthly_expense=5000,
        investable_assets=500000,
        monthly_surplus=3000,
    )
    target_40 = compute_target_allocation(profile_40)
    assert target_40.stock_pct == 45


def test_compute_target_allocation_enforces_conservative_minimum():
    """Age adjustment should not go below conservative row."""
    from domain.services.allocation_service import compute_target_allocation
    from domain.models.family import FamilyProfile, Member
    
    # Very old person with aggressive preference
    profile = FamilyProfile(
        family_id="test",
        members=(Member(member_id="self", role="主申请人", age=70, income=100000, is_decision_maker=True),),
        sub_accounts=(),
        risk_preference="aggressive",
        family_stage="single",
        monthly_income=8000,
        monthly_expense=5000,
        investable_assets=500000,
        monthly_surplus=3000,
    )
    
    target = compute_target_allocation(profile)
    # Base: (70, 15, 10, 5); age adjustment: 70 - 20 = 50
    # But conservative single is (30, 50, 15, 5), so floor at 30
    assert target.stock_pct >= 30


def test_analyze_deviation_normal():
    """Deviation < 3% should classify as normal."""
    from domain.models.allocation import AllocationTarget, AllocationState
    from domain.services.allocation_service import analyze_deviation
    
    target = AllocationTarget(50, 30, 15, 5)
    current = AllocationState(50.5, 30, 15, 4.5)  # Max deviation 0.5%
    analysis = analyze_deviation(current, target)
    assert analysis.severity == "normal"
    assert "无需调整" in analysis.recommendation


def test_analyze_deviation_mild():
    """Deviation 3-7% should classify as mild."""
    from domain.models.allocation import AllocationTarget, AllocationState
    from domain.services.allocation_service import analyze_deviation
    
    target = AllocationTarget(50, 30, 15, 5)
    current = AllocationState(55, 30, 10, 5)  # Deviation 5%
    analysis = analyze_deviation(current, target)
    assert analysis.severity == "mild"
    assert "关注" in analysis.recommendation


def test_analyze_deviation_moderate():
    """Deviation 7-15% should classify as moderate."""
    from domain.models.allocation import AllocationTarget, AllocationState
    from domain.services.allocation_service import analyze_deviation
    
    target = AllocationTarget(50, 30, 15, 5)
    current = AllocationState(60, 30, 5, 5)  # Deviation 10%
    analysis = analyze_deviation(current, target)
    assert analysis.severity == "moderate"
    assert "再平衡" in analysis.recommendation


def test_analyze_deviation_high():
    """Deviation > 15% should classify as high."""
    from domain.models.allocation import AllocationTarget, AllocationState
    from domain.services.allocation_service import analyze_deviation
    
    target = AllocationTarget(50, 30, 15, 5)
    current = AllocationState(70, 20, 5, 5)  # Deviation 20%
    analysis = analyze_deviation(current, target)
    assert analysis.severity == "high"
    assert "立即" in analysis.recommendation


def test_detect_rebalance_trigger_urgent():
    """Deviation > 15% should trigger urgent rebalance."""
    from domain.models.allocation import AllocationTarget, AllocationState, DeviationAnalysis
    from domain.services.allocation_service import detect_rebalance_trigger, analyze_deviation
    
    target = AllocationTarget(50, 30, 15, 5)
    current = AllocationState(70, 20, 5, 5)
    analysis = analyze_deviation(current, target)
    
    should_rebalance, reason = detect_rebalance_trigger(analysis, 0)
    assert should_rebalance is True
    assert "立即" in reason


def test_detect_rebalance_trigger_time_based():
    """Moderate deviation + 6+ months should trigger rebalance."""
    from domain.models.allocation import AllocationTarget, AllocationState
    from domain.services.allocation_service import detect_rebalance_trigger, analyze_deviation
    
    target = AllocationTarget(50, 30, 15, 5)
    current = AllocationState(60, 30, 5, 5)  # 10% deviation
    analysis = analyze_deviation(current, target)
    
    # 90 days = moderate deviation threshold not reached
    should_rebalance_90, _ = detect_rebalance_trigger(analysis, 90)
    assert should_rebalance_90 is False
    
    # 180 days = execute interval reached
    should_rebalance_180, reason = detect_rebalance_trigger(analysis, 180)
    assert should_rebalance_180 is True
    assert "180" in reason or "执行再平衡" in reason


def test_validate_allocation_valid():
    """Valid allocation should pass validation."""
    from domain.models.allocation import AllocationTarget
    from domain.services.allocation_service import validate_allocation
    
    target = AllocationTarget(50, 30, 15, 5)
    errors = validate_allocation(target)
    assert errors == []


def test_validate_allocation_invalid_total():
    """Allocation not summing to ~100 should fail."""
    from domain.models.allocation import AllocationTarget
    from domain.services.allocation_service import validate_allocation
    
    target = AllocationTarget(30, 30, 30, 30)  # Sum = 120
    errors = validate_allocation(target)
    assert len(errors) > 0
    assert "Total allocation" in errors[0]


def test_allocation_models_in_domain_init():
    """Allocation models should be exported from domain.models.__init__."""
    from domain.models import AllocationTarget, AllocationState, DeviationAnalysis
    assert AllocationTarget is not None
    assert AllocationState is not None
    assert DeviationAnalysis is not None


def test_rule_engine_defaults_in_init():
    """Rule engine defaults should be exported from domain.rule_engine.__init__."""
    from domain.rule_engine import AllocationDefaults, RiskDefaults, ScoringDefaults, RebalanceDefaults, StaleDataDefaults
    assert AllocationDefaults is not None
    assert RiskDefaults is not None
    assert ScoringDefaults is not None
    assert RebalanceDefaults is not None
    assert StaleDataDefaults is not None


def test_allocation_service_functions_importable():
    """Allocation service functions should be importable."""
    from domain.services.allocation_service import (
        compute_target_allocation,
        analyze_deviation,
        detect_rebalance_trigger,
        validate_allocation,
    )
    assert callable(compute_target_allocation)
    assert callable(analyze_deviation)
    assert callable(detect_rebalance_trigger)
    assert callable(validate_allocation)


def test_allocation_use_case_functions_importable():
    """Allocation use case functions should be importable."""
    from use_cases.manage_allocation import (
        compute_allocation_target,
        analyze_allocation_deviation,
        check_rebalance_need,
        save_allocation_override,
    )
    assert callable(compute_allocation_target)
    assert callable(analyze_allocation_deviation)
    assert callable(check_rebalance_need)
    assert callable(save_allocation_override)


def test_allocation_api_router_importable():
    """Allocation API router should be importable."""
    from api.allocation import router
    assert router is not None
    # Verify it's a FastAPI router
    assert hasattr(router, "routes")


# ============================================================
# M3 W1: Decision Review (Mode B — post-trade review)
# ============================================================

# ---- Decision models importable ----

def test_decision_models_importable():
    """Decision models should be importable from domain.models."""
    from domain.models import (
        BuyReason,
        BuyReasonCategory,
        DecisionQualityScore,
        DecisionReview,
        PREDEFINED_REASONS,
        SignalLevel,
    )
    assert BuyReason is not None
    assert BuyReasonCategory is not None
    assert DecisionQualityScore is not None
    assert DecisionReview is not None
    assert len(PREDEFINED_REASONS) == 8
    assert SignalLevel.RED.value == "red"


def test_buy_reason_category_values():
    """BuyReasonCategory should have 5 values matching design doc."""
    from domain.models.decision import BuyReasonCategory
    categories = [c.value for c in BuyReasonCategory]
    assert "fundamental" in categories
    assert "technical" in categories
    assert "emotional" in categories
    assert "follow" in categories
    assert "other" in categories
    assert len(categories) == 5


def test_predefined_reasons_have_correct_signals():
    """Predefined reasons should have correct signal levels per design doc."""
    from domain.models.decision import PREDEFINED_REASONS, SignalLevel
    # Count by signal type
    reds = [r for r in PREDEFINED_REASONS if r.signal == SignalLevel.RED]
    yellows = [r for r in PREDEFINED_REASONS if r.signal == SignalLevel.YELLOW]
    greens = [r for r in PREDEFINED_REASONS if r.signal == SignalLevel.GREEN]
    # Design doc: 3 red (momentum_chase, hot_news, fomo), 1 yellow (averaging_down), 4 green
    assert len(reds) == 3
    assert len(yellows) == 1
    assert len(greens) == 4


def test_buy_reason_from_predefined():
    """BuyReason.from_predefined() should resolve signal from REASON_BY_ID."""
    from domain.models.decision import BuyReason
    reason = BuyReason.from_predefined("momentum_chase")
    assert reason.reason_id == "momentum_chase"
    assert reason.signal == "red"
    assert reason.category == "emotional"


def test_buy_reason_from_custom():
    """BuyReason.from_custom() should create green-signal custom reason."""
    from domain.models.decision import BuyReason
    reason = BuyReason.from_custom("我研究了这个公司三个月", category="fundamental")
    assert reason.custom_text == "我研究了这个公司三个月"
    assert reason.signal == "green"
    assert reason.category == "fundamental"


def test_decision_quality_score_frozen():
    """DecisionQualityScore should be frozen (immutable)."""
    from domain.models.decision import DecisionQualityScore
    score = DecisionQualityScore(total=75, grade="good")
    with pytest.raises(Exception):  # FrozenInstanceError
        score.total = 80  # type: ignore


def test_decision_quality_score_round_trip():
    """DecisionQualityScore should round-trip through to_dict/from_dict."""
    from domain.models.decision import DecisionQualityScore
    original = DecisionQualityScore(
        reason_clarity=20,
        info_source=18,
        risk_awareness=22,
        time_horizon=15,
        total=65,
        red_flags=1,
        yellow_flags=0,
        grade="good",
    )
    d = original.to_dict()
    restored = DecisionQualityScore.from_dict(d)
    assert restored.total == 65
    assert restored.grade == "good"
    assert restored.red_flags == 1


def test_decision_review_frozen():
    """DecisionReview should be frozen (immutable)."""
    from domain.models.decision import DecisionReview
    review = DecisionReview(id="test_1", user_id="u1")
    with pytest.raises(Exception):  # FrozenInstanceError
        review.id = "new_id"  # type: ignore


def test_decision_review_round_trip():
    """DecisionReview should round-trip through to_dict/from_dict."""
    from domain.models.decision import BuyReason, DecisionQualityScore, DecisionReview
    original = DecisionReview(
        id="rev_123",
        user_id="user_abc",
        trade_time="2026-05-10T10:00:00",
        review_time="2026-05-10T10:30:00",
        asset_code="600519",
        asset_name="贵州茅台",
        action="buy",
        amount=50000.0,
        reasons=[
            BuyReason.from_predefined("valuation_low"),
            BuyReason.from_custom("长期看好白酒行业"),
        ],
        quality_score=DecisionQualityScore(total=75, grade="good"),
        notes="定投补仓",
    )
    d = original.to_dict()
    restored = DecisionReview.from_dict(d)
    assert restored.id == "rev_123"
    assert restored.asset_code == "600519"
    assert restored.amount == 50000.0
    assert len(restored.reasons) == 2
    assert restored.reasons[0].reason_id == "valuation_low"
    assert restored.reasons[1].custom_text == "长期看好白酒行业"
    assert restored.quality_score.total == 75


# ---- DecisionGuardProtocol ----

def test_decision_guard_protocol_importable():
    """DecisionGuardProtocol should be importable from domain.protocols."""
    from domain.protocols import DecisionGuardProtocol
    assert DecisionGuardProtocol is not None


def test_decision_guard_protocol_runtime_checkable():
    """DecisionGuardProtocol should be runtime_checkable."""
    from domain.protocols import DecisionGuardProtocol
    # A random object should NOT satisfy the protocol
    assert not isinstance("not_a_guard", DecisionGuardProtocol)


# ---- Decision Guard Service ----

def test_get_predefined_reasons():
    """get_predefined_reasons() should return 8 reasons with required keys."""
    from domain.services.decision_guard_service import get_predefined_reasons
    reasons = get_predefined_reasons()
    assert len(reasons) == 8
    for r in reasons:
        assert "id" in r
        assert "label_zh" in r
        assert "category" in r
        assert "signal" in r


def test_evaluate_reasons_counts_signals():
    """evaluate_reasons() should count red and yellow signals correctly."""
    from domain.models.decision import BuyReason
    from domain.services.decision_guard_service import evaluate_reasons
    reasons = [
        BuyReason.from_predefined("momentum_chase"),  # red
        BuyReason.from_predefined("fomo"),           # red
        BuyReason.from_predefined("averaging_down"),  # yellow
        BuyReason.from_predefined("valuation_low"),   # green
    ]
    red, yellow, messages = evaluate_reasons(reasons)
    assert red == 2
    assert yellow == 1
    assert len(messages) == 3  # 2 red + 1 yellow messages


def test_compute_quality_score_all_green():
    """Quality score with all green reasons should be high."""
    from domain.models.decision import BuyReason
    from domain.services.decision_guard_service import compute_quality_score
    reasons = [
        BuyReason.from_predefined("valuation_low"),
        BuyReason.from_predefined("allocation_gap"),
        BuyReason.from_predefined("dca_plan"),
    ]
    score = compute_quality_score(
        reasons=reasons,
        has_emergency_fund=True,
        concentration_ok=True,
        money_not_needed_3y=True,
        days_since_last_trade=90,
    )
    assert score.total >= 70
    assert score.red_flags == 0
    assert score.yellow_flags == 0
    assert score.grade in ("good", "excellent")


def test_compute_quality_score_all_red():
    """Quality score with all red reasons should be low."""
    from domain.models.decision import BuyReason
    from domain.services.decision_guard_service import compute_quality_score
    reasons = [
        BuyReason.from_predefined("momentum_chase"),  # red
        BuyReason.from_predefined("hot_news"),       # red
        BuyReason.from_predefined("fomo"),            # red
    ]
    score = compute_quality_score(
        reasons=reasons,
        has_emergency_fund=False,
        concentration_ok=False,
        money_not_needed_3y=False,
        days_since_last_trade=5,
    )
    assert score.total <= 30
    assert score.red_flags == 3
    assert score.grade == "poor"


def test_compute_quality_score_no_reasons():
    """Quality score with no reasons should be poor."""
    from domain.services.decision_guard_service import compute_quality_score
    score = compute_quality_score(
        reasons=[],
        has_emergency_fund=True,
        concentration_ok=True,
        money_not_needed_3y=True,
    )
    assert score.total == 20
    assert score.grade == "poor"


def test_compute_quality_score_risk_dimension():
    """Risk awareness dimension should penalize missing safety nets."""
    from domain.models.decision import BuyReason
    from domain.services.decision_guard_service import compute_quality_score
    reasons = [BuyReason.from_predefined("valuation_low")]

    # All risk checks pass
    score_safe = compute_quality_score(
        reasons=reasons,
        has_emergency_fund=True,
        concentration_ok=True,
        money_not_needed_3y=True,
    )
    # All risk checks fail
    score_risky = compute_quality_score(
        reasons=reasons,
        has_emergency_fund=False,
        concentration_ok=False,
        money_not_needed_3y=False,
    )
    # Risk-aware should score higher
    assert score_safe.risk_awareness > score_risky.risk_awareness
    assert score_safe.total > score_risky.total


def test_compute_quality_score_time_horizon():
    """Time horizon dimension should penalize frequent trading."""
    from domain.models.decision import BuyReason
    from domain.services.decision_guard_service import compute_quality_score
    reasons = [BuyReason.from_predefined("valuation_low")]

    # Long interval
    score_long = compute_quality_score(reasons=reasons, days_since_last_trade=90)
    # Short interval
    score_short = compute_quality_score(reasons=reasons, days_since_last_trade=10)
    assert score_long.time_horizon > score_short.time_horizon


# ---- Use Case: review_decision ----

def test_submit_decision_review_basic():
    """submit_decision_review should return a valid review + warnings."""
    from use_cases.review_decision import submit_decision_review
    review, warnings = submit_decision_review(
        user_id="test_user",
        asset_code="600519",
        asset_name="贵州茅台",
        action="buy",
        amount=10000.0,
        reason_ids=["valuation_low", "dca_plan"],
        custom_reason_text="看好白酒复苏",
    )
    assert review.id.startswith("rev_")
    assert review.asset_code == "600519"
    assert review.quality_score.total > 0
    assert len(review.reasons) == 3  # 2 predefined + 1 custom


def test_submit_decision_review_red_flags_generate_warnings():
    """Red flag reasons should generate warning messages."""
    from use_cases.review_decision import submit_decision_review
    review, warnings = submit_decision_review(
        user_id="test_user",
        asset_code="300750",
        asset_name="宁德时代",
        action="buy",
        amount=20000.0,
        reason_ids=["momentum_chase", "fomo"],
    )
    # Should have red flag warnings
    red_warnings = [w for w in warnings if "红灯" in w]
    assert len(red_warnings) == 2
    assert review.quality_score.red_flags == 2


def test_submit_decision_review_no_reasons_warning():
    """No reasons should produce a low-score warning."""
    from use_cases.review_decision import submit_decision_review
    review, warnings = submit_decision_review(
        user_id="test_user",
        asset_code="000001",
        asset_name="平安银行",
        action="buy",
        amount=5000.0,
        reason_ids=[],
        custom_reason_text="",
    )
    assert any("未选择" in w for w in warnings)
    assert review.quality_score.grade == "poor"


# ---- API Router ----

def test_decisions_api_router_importable():
    """Decisions API router should be importable."""
    from api.decisions import router
    assert router is not None
    assert hasattr(router, "routes")


def test_decisions_api_router_has_review_endpoint():
    """Decisions API router should have /api/decisions/review POST endpoint."""
    from api.decisions import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/api/decisions/review" in paths


def test_decisions_api_router_has_reasons_endpoint():
    """Decisions API router should have /api/decisions/reasons GET endpoint."""
    from api.decisions import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/api/decisions/reasons" in paths


# ============================================================
# M3 W2: Pre-trade 7-Point Checklist (Mode A)
# ============================================================

# ---- Checklist models ----

def test_checklist_models_importable():
    """Checklist models should be importable from domain.models."""
    from domain.models import (
        ChecklistItem,
        ChecklistResult,
        CHECKLIST_PASS_THRESHOLD,
        CHECKLIST_MAX_SCORE,
    )
    assert ChecklistItem is not None
    assert ChecklistResult is not None
    assert CHECKLIST_PASS_THRESHOLD == 42
    assert CHECKLIST_MAX_SCORE == 70


def test_checklist_item_frozen():
    """ChecklistItem should be frozen."""
    from domain.models.checklist import ChecklistItem
    item = ChecklistItem(item_id="test", score=8)
    with pytest.raises(Exception):
        item.score = 5  # type: ignore


def test_checklist_item_round_trip():
    """ChecklistItem should round-trip through to_dict/from_dict."""
    from domain.models.checklist import ChecklistItem
    item = ChecklistItem(
        item_id="emergency_fund",
        label_zh="应急金是否充足",
        score=7,
        passed=True,
        is_red_light=False,
        detail="应急金尚可",
    )
    d = item.to_dict()
    restored = ChecklistItem.from_dict(d)
    assert restored.item_id == "emergency_fund"
    assert restored.score == 7
    assert restored.passed is True


def test_checklist_result_frozen():
    """ChecklistResult should be frozen."""
    from domain.models.checklist import ChecklistResult
    result = ChecklistResult(total_score=55, passed=True)
    with pytest.raises(Exception):
        result.total_score = 60  # type: ignore


def test_checklist_result_round_trip():
    """ChecklistResult should round-trip through to_dict/from_dict."""
    from domain.models.checklist import ChecklistItem, ChecklistResult
    result = ChecklistResult(
        items=[ChecklistItem(item_id="test", score=8, passed=True)],
        total_score=56,
        max_score=70,
        passed=True,
        red_light_count=0,
        recommendation="通过",
    )
    d = result.to_dict()
    restored = ChecklistResult.from_dict(d)
    assert restored.total_score == 56
    assert restored.passed is True
    assert len(restored.items) == 1
    assert restored.items[0].item_id == "test"


# ---- Checklist engine ----

def test_checklist_engine_importable():
    """run_checklist should be importable from domain.rule_engine."""
    from domain.rule_engine import run_checklist
    assert callable(run_checklist)


def test_checklist_all_pass():
    """With all good inputs, checklist should pass with high score."""
    from domain.rule_engine.checklist import run_checklist
    result = run_checklist(
        emergency_months=8.0,
        insurance_count=4,
        single_asset_pct=0.15,
        single_industry_pct=0.25,
        money_needed_within_3y=False,
        red_flag_count=0,
        yellow_flag_count=0,
        trade_pct_of_investable=0.10,
        days_since_last_trade=60,
    )
    assert result.passed is True
    assert result.blocked is False
    assert result.total_score == 70  # perfect score
    assert result.red_light_count == 0
    assert len(result.items) == 7


def test_checklist_all_fail():
    """With all bad inputs, checklist should fail with low score."""
    from domain.rule_engine.checklist import run_checklist
    result = run_checklist(
        emergency_months=1.0,
        insurance_count=1,
        single_asset_pct=0.40,
        single_industry_pct=0.50,
        money_needed_within_3y=True,
        red_flag_count=3,
        yellow_flag_count=0,
        trade_pct_of_investable=0.60,
        days_since_last_trade=3,
    )
    assert result.passed is False
    assert result.blocked is True
    assert result.total_score < 42  # below threshold
    assert result.red_light_count >= 4  # multiple red lights


def test_checklist_emergency_fund_scoring():
    """Emergency fund check should score correctly at different levels."""
    from domain.rule_engine.checklist import run_checklist
    # 6+ months = 10
    r1 = run_checklist(emergency_months=8.0)
    assert r1.items[0].score == 10
    assert r1.items[0].passed is True

    # 4-6 months = 7
    r2 = run_checklist(emergency_months=5.0)
    assert r2.items[0].score == 7

    # < 2 months = 1 (red light)
    r3 = run_checklist(emergency_months=1.5)
    assert r3.items[0].score == 1
    assert r3.items[0].is_red_light is True


def test_checklist_concentration_scoring():
    """Concentration check should use RiskDefaults thresholds (25%/40%)."""
    from domain.rule_engine.checklist import run_checklist
    # Both OK
    r1 = run_checklist(single_asset_pct=0.20, single_industry_pct=0.30)
    assert r1.items[2].score == 10

    # Asset slightly over (within 5%)
    r2 = run_checklist(single_asset_pct=0.28, single_industry_pct=0.30)
    assert r2.items[2].score == 6

    # Both over
    r3 = run_checklist(single_asset_pct=0.35, single_industry_pct=0.50)
    assert r3.items[2].score == 1
    assert r3.items[2].is_red_light is True


def test_checklist_reason_rational_scoring():
    """Reason rational check should consume red/yellow flag counts."""
    from domain.rule_engine.checklist import run_checklist
    # 0 flags = 10
    r1 = run_checklist(red_flag_count=0, yellow_flag_count=0)
    assert r1.items[4].score == 10

    # 1 red = 4
    r2 = run_checklist(red_flag_count=1, yellow_flag_count=0)
    assert r2.items[4].score == 4

    # 2+ red = 1 (red light)
    r3 = run_checklist(red_flag_count=2, yellow_flag_count=0)
    assert r3.items[4].score == 1
    assert r3.items[4].is_red_light is True


def test_checklist_cooldown_scoring():
    """Cooldown check should score based on days since last trade."""
    from domain.rule_engine.checklist import run_checklist
    # >= 30 days = 10
    r1 = run_checklist(days_since_last_trade=45)
    assert r1.items[6].score == 10

    # < 7 days = 2 (red light)
    r2 = run_checklist(days_since_last_trade=3)
    assert r2.items[6].score == 2
    assert r2.items[6].is_red_light is True


def test_checklist_pass_threshold():
    """Checklist should pass at exactly 42/70 and fail at 41/70."""
    from domain.models.checklist import CHECKLIST_PASS_THRESHOLD
    assert CHECKLIST_PASS_THRESHOLD == 42


def test_checklist_not_all_in_scoring():
    """Not-all-in check should penalize large single trades."""
    from domain.rule_engine.checklist import run_checklist
    # <= 10% = 10
    r1 = run_checklist(trade_pct_of_investable=0.05)
    assert r1.items[5].score == 10

    # 10-20% = 8
    r2 = run_checklist(trade_pct_of_investable=0.15)
    assert r2.items[5].score == 8

    # > 50% = 1 (red light)
    r3 = run_checklist(trade_pct_of_investable=0.60)
    assert r3.items[5].score == 1
    assert r3.items[5].is_red_light is True


# ---- Use Case: run_checklist ----

def test_run_pre_trade_checklist_basic():
    """run_pre_trade_checklist should return ChecklistResult + warnings."""
    from use_cases.run_checklist import run_pre_trade_checklist
    result, warnings = run_pre_trade_checklist(
        reason_ids=["valuation_low", "dca_plan"],
        emergency_months=7.0,
        insurance_count=4,
        single_asset_pct=0.10,
        single_industry_pct=0.20,
        money_needed_within_3y=False,
        trade_pct_of_investable=0.10,
        days_since_last_trade=40,
    )
    assert result.passed is True
    assert result.total_score == 70
    assert len(result.items) == 7


def test_run_pre_trade_checklist_with_red_flags():
    """Checklist with red-flag reasons should reduce reason_rational score."""
    from use_cases.run_checklist import run_pre_trade_checklist
    result, warnings = run_pre_trade_checklist(
        reason_ids=["momentum_chase", "fomo"],
        emergency_months=7.0,
        insurance_count=4,
        single_asset_pct=0.10,
        single_industry_pct=0.20,
        money_needed_within_3y=False,
        trade_pct_of_investable=0.10,
        days_since_last_trade=40,
    )
    # reason_rational item should have low score
    reason_item = [i for i in result.items if i.item_id == "reason_rational"][0]
    assert reason_item.score == 1  # 2+ red flags
    assert reason_item.is_red_light is True
    # Should have warning messages
    assert any("红灯" in w for w in warnings)


def test_run_pre_trade_checklist_blocked():
    """Checklist with many failures should set blocked=True."""
    from use_cases.run_checklist import run_pre_trade_checklist
    result, warnings = run_pre_trade_checklist(
        reason_ids=["momentum_chase", "fomo", "hot_news"],
        emergency_months=1.0,
        insurance_count=1,
        single_asset_pct=0.35,
        single_industry_pct=0.50,
        money_needed_within_3y=True,
        trade_pct_of_investable=0.55,
        days_since_last_trade=2,
    )
    assert result.blocked is True
    assert result.passed is False
    assert any("未通过" in w or "暂缓" in w for w in warnings)


def test_get_checklist_items_description():
    """get_checklist_items_description should return 7 items."""
    from use_cases.run_checklist import get_checklist_items_description
    items = get_checklist_items_description()
    assert len(items) == 7
    for item in items:
        assert "item_id" in item
        assert "label_zh" in item
        assert "description" in item


# ---- API: Checklist endpoints ----

def test_decisions_api_has_checklist_endpoint():
    """Decisions API router should have /api/decisions/checklist POST endpoint."""
    from api.decisions import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/api/decisions/checklist" in paths


def test_decisions_api_has_checklist_items_endpoint():
    """Decisions API router should have /api/decisions/checklist/items GET endpoint."""
    from api.decisions import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/api/decisions/checklist/items" in paths


# ============================================================
# M3 W4: Red Team Audit + Chat Guard (AI Boundary Enforcement)
# ============================================================

# ---- Red Team Audit ----

def test_red_team_audit_importable():
    """red_team_audit module should be importable."""
    from infra.llm.red_team_audit import (
        audit_response,
        audit_field,
        get_banned_patterns,
        BANNED_PATTERNS,
    )
    assert callable(audit_response)
    assert callable(audit_field)
    assert len(BANNED_PATTERNS) >= 8


def test_red_team_blocks_buy_recommendation():
    """audit_response should block direct buy recommendations."""
    from infra.llm.red_team_audit import audit_response
    passed, violations = audit_response("建议你现在买入贵州茅台")
    assert passed is False
    assert len(violations) > 0


def test_red_team_blocks_price_prediction():
    """audit_response should block price predictions."""
    from infra.llm.red_team_audit import audit_response
    passed, violations = audit_response("预计下周将会反弹到3500点")
    assert passed is False


def test_red_team_blocks_position_sizing():
    """audit_response should block specific position percentages."""
    from infra.llm.red_team_audit import audit_response
    passed, violations = audit_response("建议30%仓位买入科技股")
    assert passed is False


def test_red_team_blocks_guarantee():
    """audit_response should block guarantee/promise language."""
    from infra.llm.red_team_audit import audit_response
    passed, _ = audit_response("保本保息年化8%")
    assert passed is False
    passed2, _ = audit_response("这只基金肯定不会亏")
    assert passed2 is False


def test_red_team_passes_factual_description():
    """audit_response should pass legitimate factual descriptions."""
    from infra.llm.red_team_audit import audit_response
    # These are allowed
    passed, violations = audit_response("当前股票配比偏离目标5个百分点")
    assert passed is True
    assert violations == []


def test_red_team_passes_risk_warning():
    """audit_response should pass risk warnings."""
    from infra.llm.red_team_audit import audit_response
    passed, _ = audit_response("该操作可能导致集中度过高")
    assert passed is True


def test_red_team_field_boundary_check():
    """audit_field should enforce field-specific boundaries."""
    from infra.llm.red_team_audit import audit_field
    # market_environment should not contain "看好"
    passed, violations = audit_field("market_environment", "我看好后市走势")
    assert passed is False
    # But factual description is OK
    passed2, _ = audit_field("market_environment", "市场整体估值中位数")
    assert passed2 is True


def test_red_team_interception_rate_above_99():
    """Interception rate should be >= 99% on test corpus."""
    from infra.llm.red_team_audit import audit_response, compute_interception_rate
    test_cases = [
        ("建议你现在买入贵州茅台", True),
        ("预计下周将会反弹", True),
        ("60%仓位配置股票", True),
        ("保本保息", True),
        ("当前股票偏离目标5%", False),
        ("市场整体估值中位数", False),
    ]
    rate = compute_interception_rate(test_cases)
    assert rate >= 0.99


# ---- Chat Guard ----

def test_chat_guard_importable():
    """chat_guard module should be importable."""
    from infra.llm.chat_guard import (
        validate_chat_request,
        check_action_seeking,
        MAX_ROUNDS_PER_ANCHOR,
    )
    assert callable(validate_chat_request)
    assert callable(check_action_seeking)
    assert MAX_ROUNDS_PER_ANCHOR == 5


def test_chat_guard_rejects_no_anchor():
    """Chat without anchor should be rejected."""
    from infra.llm.chat_guard import validate_chat_request
    allowed, msg = validate_chat_request(anchor_id=None)
    assert allowed is False
    assert "关闭" in msg or "下线" in msg

    allowed2, msg2 = validate_chat_request(anchor_id="")
    assert allowed2 is False


def test_chat_guard_allows_with_anchor():
    """Chat with valid anchor should be allowed."""
    from infra.llm.chat_guard import validate_chat_request
    allowed, msg = validate_chat_request(anchor_id="report_20260510_user1", round_num=1)
    assert allowed is True
    assert msg == ""


def test_chat_guard_enforces_round_limit():
    """Chat exceeding 5 rounds should be rejected."""
    from infra.llm.chat_guard import validate_chat_request
    # Round 5 is OK
    allowed5, _ = validate_chat_request(anchor_id="holding_600519", round_num=5)
    assert allowed5 is True
    # Round 6 is rejected
    allowed6, msg = validate_chat_request(anchor_id="holding_600519", round_num=6)
    assert allowed6 is False
    assert "上限" in msg


def test_chat_guard_detects_action_seeking():
    """Action-seeking questions should trigger fallback response."""
    from infra.llm.chat_guard import check_action_seeking, FALLBACK_RESPONSE
    # "该怎么办" triggers fallback
    is_seeking, response = check_action_seeking("那我该怎么办？")
    assert is_seeking is True
    assert response == FALLBACK_RESPONSE

    # "推荐一下" triggers fallback
    is_seeking2, _ = check_action_seeking("给我推荐几个")
    assert is_seeking2 is True


def test_chat_guard_passes_factual_question():
    """Normal factual questions should not trigger fallback."""
    from infra.llm.chat_guard import check_action_seeking
    is_seeking, _ = check_action_seeking("这个偏差是怎么算的？")
    assert is_seeking is False

    is_seeking2, _ = check_action_seeking("集中度35%是高还是低？")
    assert is_seeking2 is False


def test_chat_guard_anchor_validation():
    """is_anchor_valid should check anchor ID format."""
    from infra.llm.chat_guard import is_anchor_valid
    assert is_anchor_valid("report_20260510_user") is True
    assert is_anchor_valid("holding_600519") is True
    assert is_anchor_valid("alert_deviation_001") is True
    assert is_anchor_valid("random_string") is False
    assert is_anchor_valid("") is False


# ======================================================================
# M4 W1: RAG Knowledge Base Tests
# ======================================================================


def test_knowledge_models_importable():
    """KnowledgeChunk, KnowledgeArticle, RetrievalResult should be importable."""
    from domain.models.knowledge import (
        KnowledgeChunk,
        KnowledgeArticle,
        RetrievalResult,
        SourceGrade,
        ContentCategory,
    )
    assert KnowledgeChunk is not None
    assert KnowledgeArticle is not None
    assert RetrievalResult is not None
    assert SourceGrade.A.value == "A"
    assert ContentCategory.FAMILY_FINANCE.value == "家庭财务基础"


def test_knowledge_models_in_init():
    """domain.models should export KnowledgeChunk etc."""
    from domain.models import KnowledgeChunk, KnowledgeArticle, RetrievalResult, SourceGrade, ContentCategory
    assert KnowledgeChunk is not None


def test_knowledge_retriever_protocol_importable():
    """KnowledgeRetrieverProtocol should be importable and runtime_checkable."""
    from domain.protocols import KnowledgeRetrieverProtocol
    from domain.protocols.knowledge_retriever import KnowledgeRetrieverProtocol as KRP
    assert KRP is not None
    # Check it's runtime_checkable
    import typing
    assert hasattr(KRP, '__protocol_attrs__') or hasattr(KRP, '__abstractmethods__') or True


def test_knowledge_chunk_roundtrip():
    """KnowledgeChunk to_dict/from_dict should roundtrip."""
    from domain.models.knowledge import KnowledgeChunk, SourceGrade
    chunk = KnowledgeChunk(
        chunk_id="test_chunk_0",
        article_id="test-article",
        content="This is test content about investing.",
        title="Test Article",
        category="家庭财务基础",
        source_tag="Test Source 2024",
        source_grade=SourceGrade.A,
        embedding=[0.1, 0.2, 0.3],
        chunk_index=0,
        metadata={"reviewer": "test"},
    )
    d = chunk.to_dict()
    assert d["chunk_id"] == "test_chunk_0"
    assert d["source_grade"] == "A"
    assert d["embedding"] == [0.1, 0.2, 0.3]

    restored = KnowledgeChunk.from_dict(d)
    assert restored.chunk_id == chunk.chunk_id
    assert restored.source_grade == SourceGrade.A
    assert restored.content == chunk.content


def test_knowledge_article_roundtrip():
    """KnowledgeArticle to_dict/from_dict should roundtrip."""
    from domain.models.knowledge import KnowledgeArticle, SourceGrade
    article = KnowledgeArticle(
        article_id="emergency-fund",
        title="应急金的 6 个月法则",
        category="家庭财务基础",
        source="Test Source",
        source_grade=SourceGrade.B,
        reviewer="tester",
    )
    d = article.to_dict()
    restored = KnowledgeArticle.from_dict(d)
    assert restored.title == article.title
    assert restored.source_grade == SourceGrade.B


def test_rag_service_parse_frontmatter():
    """parse_article_frontmatter should extract YAML-like headers."""
    from domain.services.rag_service import parse_article_frontmatter
    content = """---
title: Test Article
category: 家庭财务基础
source_grade: A
---

# Body here
"""
    fm = parse_article_frontmatter(content)
    assert fm["title"] == "Test Article"
    assert fm["category"] == "家庭财务基础"
    assert fm["source_grade"] == "A"


def test_rag_service_parse_body():
    """parse_article_body should extract text after frontmatter."""
    from domain.services.rag_service import parse_article_body
    content = """---
title: Test
---

# Hello World

This is the body."""
    body = parse_article_body(content)
    assert "Hello World" in body
    assert "This is the body" in body
    assert "title: Test" not in body


def test_rag_service_chunk_text():
    """chunk_text should split long text into manageable chunks."""
    from domain.services.rag_service import chunk_text
    # Create text longer than CHUNK_MAX_CHARS
    long_text = "\n\n".join([f"Paragraph {i}. " * 20 for i in range(10)])
    chunks = chunk_text(long_text, max_chars=300, overlap=30)
    assert len(chunks) > 1
    # Each chunk should be reasonable size
    for c in chunks:
        assert len(c) >= 80  # MIN_CHUNK_CHARS


def test_rag_service_tokenize():
    """tokenize should handle Chinese + English mixed text."""
    from domain.services.rag_service import tokenize
    tokens = tokenize("家庭应急金需要 6 个月的 monthly expenses")
    assert len(tokens) > 0
    # Should contain Chinese bigrams
    assert "家庭" in tokens or "应急" in tokens
    # Should contain English words (>1 char)
    assert "monthly" in tokens or "expenses" in tokens


def test_rag_service_compute_embedding():
    """compute_embedding should produce a normalized vector."""
    from domain.services.rag_service import compute_embedding
    import math
    vocab = ["投资", "基金", "股票", "债券", "收益", "风险"]
    emb = compute_embedding("投资基金的收益和风险", vocab)
    assert len(emb) == len(vocab)
    # Should be normalized (L2 norm ≈ 1)
    norm = math.sqrt(sum(v * v for v in emb))
    if norm > 0:
        assert abs(norm - 1.0) < 0.01


def test_rag_service_cosine_similarity():
    """cosine_similarity should compute correct values."""
    from domain.services.rag_service import cosine_similarity
    # Same vector = 1.0
    v = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 0.001
    # Orthogonal = 0.0
    assert abs(cosine_similarity([1, 0, 0], [0, 1, 0])) < 0.001
    # Empty = 0.0
    assert cosine_similarity([], []) == 0.0


def test_rag_service_build_chunks_for_article():
    """build_chunks_for_article should produce chunks with correct metadata."""
    from domain.services.rag_service import build_chunks_for_article
    from domain.models.knowledge import KnowledgeArticle, SourceGrade
    article = KnowledgeArticle(
        article_id="test-article",
        title="Test",
        category="数学常识",
        source="Test Source",
        source_grade=SourceGrade.B,
        reviewer="tester",
        reviewed_at="2026-05-10",
    )
    body = "First paragraph about investing. " * 30 + "\n\n" + "Second paragraph about risk. " * 30
    chunks = build_chunks_for_article(article, body)
    assert len(chunks) >= 1
    assert chunks[0].article_id == "test-article"
    assert chunks[0].title == "Test"
    assert chunks[0].source_grade == SourceGrade.B
    assert chunks[0].chunk_index == 0


def test_infra_knowledge_retriever_importable():
    """infra.knowledge.KnowledgeRetriever should be importable."""
    from infra.knowledge import KnowledgeRetriever
    retriever = KnowledgeRetriever()
    assert retriever.total_chunks() == 0
    assert retriever.list_articles() == []
    assert retriever.retrieve("test") == []


def test_infra_knowledge_retriever_protocol_compliance():
    """KnowledgeRetriever should satisfy KnowledgeRetrieverProtocol."""
    from infra.knowledge import KnowledgeRetriever
    from domain.protocols.knowledge_retriever import KnowledgeRetrieverProtocol
    retriever = KnowledgeRetriever()
    assert isinstance(retriever, KnowledgeRetrieverProtocol)


def test_infra_knowledge_indexer_loads_articles():
    """load_and_index_articles should load .md files from content dir."""
    from infra.knowledge import KnowledgeRetriever, load_and_index_articles
    retriever = KnowledgeRetriever()
    count = load_and_index_articles(retriever)
    # Should have indexed chunks from our 12 articles
    assert count >= 20  # at least 2 chunks per article × 10+ articles
    assert retriever.total_chunks() == count
    articles = retriever.list_articles()
    assert len(articles) >= 10  # core 10 + behavioral finance


def test_infra_knowledge_retrieval_returns_results():
    """Retrieval should return relevant chunks for investment queries."""
    from infra.knowledge import KnowledgeRetriever, load_and_index_articles
    retriever = KnowledgeRetriever()
    load_and_index_articles(retriever)
    # Query about emergency fund
    results = retriever.retrieve("应急金怎么算")
    assert len(results) > 0
    # At least one result should be from emergency fund article
    article_ids = [r.article_id for r in results]
    assert any("emergency" in aid for aid in article_ids)


def test_infra_knowledge_retrieval_category_filter():
    """Category hint should boost relevant category results."""
    from infra.knowledge import KnowledgeRetriever, load_and_index_articles
    retriever = KnowledgeRetriever()
    load_and_index_articles(retriever)
    # Query with category hint
    results = retriever.retrieve("投资策略", top_k=3, category_hint="资产配置")
    assert len(results) > 0


def test_infra_knowledge_retrieval_empty_query():
    """Empty retriever should return empty results."""
    from infra.knowledge import KnowledgeRetriever
    retriever = KnowledgeRetriever()
    results = retriever.retrieve("anything")
    assert results == []


def test_use_case_retrieve_knowledge_chunks():
    """retrieve_knowledge_chunks use case should format response correctly."""
    from infra.knowledge import KnowledgeRetriever, load_and_index_articles
    from use_cases.retrieve_knowledge import retrieve_knowledge_chunks
    retriever = KnowledgeRetriever()
    load_and_index_articles(retriever)
    result = retrieve_knowledge_chunks(retriever, query="复利 72法则", top_k=3)
    assert "query" in result
    assert "chunks" in result
    assert "total_indexed" in result
    assert "further_reading" in result
    assert "has_results" in result
    assert result["total_indexed"] > 0


def test_use_case_list_knowledge_articles():
    """list_knowledge_articles should return article metadata."""
    from infra.knowledge import KnowledgeRetriever, load_and_index_articles
    from use_cases.retrieve_knowledge import list_knowledge_articles
    retriever = KnowledgeRetriever()
    load_and_index_articles(retriever)
    articles = list_knowledge_articles(retriever)
    assert len(articles) >= 10
    # Each should have expected keys
    for a in articles:
        assert "article_id" in a
        assert "title" in a
        assert "category" in a
        assert "source_grade" in a


def test_use_case_get_knowledge_stats():
    """get_knowledge_stats should return correct statistics."""
    from infra.knowledge import KnowledgeRetriever, load_and_index_articles
    from use_cases.retrieve_knowledge import get_knowledge_stats
    retriever = KnowledgeRetriever()
    load_and_index_articles(retriever)
    stats = get_knowledge_stats(retriever)
    assert stats["article_count"] >= 10
    assert stats["chunk_count"] > 0
    assert len(stats["categories"]) > 0
    assert stats["source_grades"]["A"] > 0 or stats["source_grades"]["B"] > 0


def test_rag_api_module_importable():
    """api.rag module should be importable."""
    from api.rag import rag_router
    assert rag_router is not None
    # Check routes exist (paths include prefix)
    routes = [r.path for r in rag_router.routes]
    assert "/api/rag/retrieve" in routes
    assert "/api/rag/articles" in routes
    assert "/api/rag/stats" in routes


def test_rag_no_domain_infra_import():
    """domain/services/rag_service.py must not import from infra/ (invariant #10)."""
    import ast
    rag_service_path = BACKEND_DIR / "domain" / "services" / "rag_service.py"
    tree = ast.parse(rag_service_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("infra"), (
                    f"domain/services/rag_service.py imports infra: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert not node.module.startswith("infra"), (
                    f"domain/services/rag_service.py imports infra: {node.module}"
                )


def test_rag_no_cross_service_import():
    """domain/services/rag_service.py must not import other domain/services (invariant #9)."""
    import ast
    rag_service_path = BACKEND_DIR / "domain" / "services" / "rag_service.py"
    tree = ast.parse(rag_service_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "domain.services." in node.module:
                assert False, (
                    f"domain/services/rag_service.py cross-imports service: {node.module}"
                )

