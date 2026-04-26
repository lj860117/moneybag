# MoneyBag Codebase Exploration Report
**Date:** 2026-04-26  
**Repository:** `/Users/leijiang/WorkBuddy/moneybag-for-claudecode`  
**Focus:** Four-layer architecture, domain models, protocols, test structure

---

## 1. Four-Layer Architecture

### 1.1 Directory Structure

```
backend/
├── api/                  # Layer 1: HTTP routing (21 router modules)
│   ├── shared_helpers.py # Common utilities
│   ├── family_profile.py # 6 routes: questionnaire/profile/members/sub-accounts CRUD
│   ├── balance_sheet.py  # 4 routes: balance sheet CRUD + staleness report
│   └── [18 other routers]
├── use_cases/            # Layer 2: Orchestration (2 modules)
│   ├── submit_family_questionnaire.py  # Family profile submission choreography
│   └── manage_balance_sheet.py          # Balance sheet CRUD choreography
├── domain/               # Layer 3: Business logic
│   ├── models/           # Value objects (frozen dataclasses)
│   ├── protocols/        # Interface contracts (runtime_checkable)
│   ├── services/         # Pure domain services (no IO)
│   └── rule_engine/      # Business rules & defaults
└── infra/                # Layer 4: Infrastructure
    ├── cache/
    ├── store/
    ├── llm/
    ├── data_source/
    └── [config/, knowledge/, events/]
```

---

## 2. Existing FamilyProfile and BalanceSheet Models

### 2.1 FamilyProfile Model
**File:** `backend/domain/models/family.py` (301 lines)

Key classes:
- **Member** - family member with member_id, role, age, income, is_decision_maker
- **SubAccount** - investment sub-account with account_id, purpose, target_allocation (frozen tuple), horizon_years, is_independent
- **FamilyProfile** - complete financial profile with members, sub_accounts, and 20+ questionnaire fields

All are frozen dataclasses (immutable). Key enums:
- VALID_RISK_PREFERENCES = ("conservative", "balanced", "aggressive")
- VALID_FAMILY_STAGES = ("single", "married_mortgage", "with_children", "near_retirement")
- VALID_DRAWDOWN_TOLERANCES = (-0.10, -0.20, -0.30, -0.50)

### 2.2 BalanceSheet Model
**File:** `backend/domain/models/balance_sheet.py` (267 lines)

Key classes:
- **BalanceSheetItem** - single line item with name, category, value, currency, last_updated, data_source
- **BalanceSheet** - four Tier 1 categories: cash_deposits, investments, real_estate, liabilities

Staleness detection: items > 30 days old marked stale. Computed properties: total_assets, total_liabilities, net_worth, all_items.

VALID_CATEGORIES = ("cash_deposits", "investments", "real_estate", "liabilities")

---

## 3. Existing Protocols in domain/protocols/

**File:** `backend/domain/protocols/__init__.py`

Six @runtime_checkable protocols:

1. **FamilyProfileProtocol** - load(family_id), save(family_id, data), exists(family_id), list_families()
2. **BalanceSheetProtocol** - same interface as above
3. **CacheProtocol** - get(key), set(key, value, ttl), delete(key), clear(), put(), expire(), has()
4. **StoreProtocol** - read/write/delete/exists/list_keys for collection-based persistence
5. **LLMClientProtocol** - for LLM calls
6. **DataSourceProtocol** - for data fetching

---

## 4. Rule Engine Structure

**File:** `backend/domain/rule_engine/decision_archive.py` (565 lines)

Contains:
- Decision log (hot/cold stratification, >30d archived)
- Custom alert rules (price_drop, price_rise, target_price)
- Context relay (last analysis conclusion)
- Auto-extract queue (deferred extraction)
- Batch extraction

**Status:** No defaults.py file exists (would contain allocation rules, thresholds, etc.)

---

## 5. Test Structure

**File:** `tests/test_skeleton_m1.py` (1288 lines, **72 test functions**)

Test categories:
1. Package importability (14 modules)
2. Protocol runtime checkability
3. Implementation satisfaction
4. LLMResponse round-trip
5. MemoryCache CRUD
6. FileStore CRUD

All tests passing ✅

---

## 6. Existing Allocation-Related Code

### 6.1 In config.py (Lines 92-243)

**ALLOCATION_PROFILES:**
```python
ALLOCATION_PROFILES = {
    "low": {"stock": 0.75, "bond": 0.15, "cash": 0.10},      # 低估(<20%)
    "mid": {"stock": 0.65, "bond": 0.25, "cash": 0.10},      # 适中(20-80%)
    "high": {"stock": 0.45, "bond": 0.35, "cash": 0.20},     # 高估(>80%)
}
```

**ALLOCATION_ADJUST:**
```python
ALLOCATION_ADJUST = {
    "valuation_extreme_high": {"s": -0.10, "b":  0.05, "c":  0.05},
    "valuation_high":         {"s": -0.05, "b":  0.03, "c":  0.02},
    "valuation_extreme_low":  {"s":  0.10, "b": -0.05, "c": -0.05},
    "valuation_low":          {"s":  0.05, "b": -0.03, "c": -0.02},
    "fgi_extreme_greed":      {"s": -0.05, "c":  0.05},
    "fgi_extreme_fear":       {"s":  0.05, "c": -0.05},
    "cash_floor":             0.15,
    "stock_min":              0.05,
    "stock_max":              0.90,
    "bond_max":               0.80,
}
```

**DCA_MULTIPLIERS:**
Scaling factors for intelligent investment by valuation level (2.0x → 0.0x)

### 6.2 In FamilyProfile.SubAccount
```python
target_allocation: Tuple[tuple[str, int], ...] = ()  # frozen-safe mapping
# E.g. (("stock_pct", 50), ("bond_pct", 30), ("cash_pct", 15), ("gold_pct", 5))
```

---

## 7. File Path Summary

**Core Models:**
- `backend/domain/models/family.py`
- `backend/domain/models/balance_sheet.py`

**Protocols:**
- `backend/domain/protocols/__init__.py`
- `backend/domain/protocols/family_profile.py`
- `backend/domain/protocols/balance_sheet.py`
- `backend/domain/protocols/cache.py`
- `backend/domain/protocols/store.py`
- `backend/domain/protocols/llm_client.py`
- `backend/domain/protocols/data_source.py`

**Services:**
- `backend/domain/services/family_profile_service.py`
- `backend/domain/services/balance_sheet_service.py`
- `backend/domain/services/user_preference_service.py`

**Rule Engine:**
- `backend/domain/rule_engine/__init__.py`
- `backend/domain/rule_engine/decision_archive.py`

**Use Cases:**
- `backend/use_cases/submit_family_questionnaire.py`
- `backend/use_cases/manage_balance_sheet.py`

**API Routes:**
- `backend/api/family_profile.py`
- `backend/api/balance_sheet.py`
- `backend/api/shared_helpers.py`

**Infrastructure:**
- `backend/infra/cache/memory_cache.py`
- `backend/infra/store/file_store.py`
- `backend/infra/store/family_profile_store.py`
- `backend/infra/store/balance_sheet_store.py`
- `backend/infra/llm/gateway.py`
- `backend/infra/data_source/__init__.py`

**Configuration:**
- `backend/config.py` (259 lines with ALLOCATION_PROFILES, ALLOCATION_ADJUST, DCA_MULTIPLIERS)

**Tests:**
- `tests/test_skeleton_m1.py` (1288 lines, 72 tests)

**Documentation:**
- `docs/PROGRESS.md`
- `docs/REFACTOR_STATUS.md`

---

## 8. Key Insights for Building Allocation Engine

1. **Foundation Ready:** Allocation profiles and adjustment rules exist in config.py
2. **SubAccount Model:** Already has target_allocation field (frozen tuple of (str, int) pairs)
3. **No Allocation Service:** Need to create domain/services/allocation_service.py
4. **No Defaults Module:** Need to create domain/rule_engine/defaults.py
5. **Test Scaffold:** 72 tests provide template; allocation tests should follow same pattern
6. **Protocol Pattern:** Create AllocationProtocol if needed for future multi-strategy support

**Next Steps:**
- Create `domain/rule_engine/defaults.py` with allocation rule matrices
- Create `domain/models/allocation.py` with AllocationState dataclass
- Create `domain/services/allocation_service.py` with compute/detect/suggest functions
- Create allocation-specific tests and API routes
