# MB-008 Fix Summary: Mixed/QDII Fund Classification Bug

**Issue Date:** 2026-05-15  
**Fix Date:** 2026-05-20  
**Status:** ✅ FIXED  
**Severity:** 🔴 Critical (Asset allocation display bug)

---

## Problem Statement

Users with mixed/QDII funds (混合基金/QDII) were seeing incorrect asset allocation display:
- **Symptom:** User had 5 mixed/QDII funds totaling ¥499, but system displayed "股票 100%" instead of proper equity/bond/cash split
- **False Alert:** Triggered "股票仓位超出目标60%" warning (false positive)
- **Root Cause:** Fund classification logic only recognized "货币" and "债券" keywords, defaulting all other funds to equity

### Example User Data
```json
{
  "name": "华夏成长混合",
  "costNav": 1.5,
  "shares": 50
}
```
Expected: Split into ~50% equity + 35% bond + 15% cash  
Actual (buggy): 100% equity ❌

---

## Technical Root Causes

### 1. Primary Bug: `backend/services/portfolio_overview.py` (Lines 62-69)

```python
# BEFORE (buggy):
name = (h.get("name") or "").lower()
if any(k in name for k in ["货币", "money", "余额", "现金"]):
    fund_money_type += cost
elif any(k in name for k in ["债", "bond", "纯债", "信用"]):
    fund_bond_type += cost
else:
    fund_stock_type += cost  # ❌ Mixed/QDII defaulted here
```

**Missing keywords:** "混合", "灵活配置", "QDII", "偏股", "偏债"

### 2. Secondary Bug: `backend/services/risk.py` (Lines 49-52)

Same missing keywords in classification lists used by risk control module.

### 3. No Proportional Allocation

Mixed funds need intelligent allocation of their equity/bond/cash split, not binary classification.

---

## Solution Overview

### Step 1: Create Unified Classifier Module

**File:** `backend/services/fund_classifier.py` (NEW)

- Centralized fund classification logic with complete keyword coverage
- Support for mixed funds with intelligent allocation inference
- Returns detailed classification with `type`, `keywords`, `is_mixed` flag, and `allocation` breakdown
- Two APIs:
  - `classify_fund(code, name)` → classification metadata
  - `classify_and_allocate(code, name, nav_cost, shares)` → direct allocation calculation

**Key Features:**
```python
def classify_fund(code="", name="") -> dict:
    """
    Returns:
    {
        "type": "equity" | "bond" | "money" | "gold" | "mixed" | "unknown",
        "keywords": ["matched", "keywords"],
        "is_mixed": bool,
        "allocation": {"equity": 0.6, "bond": 0.3, "money": 0.1},  # if mixed
    }
    """
```

### Step 2: Update Portfolio Overview

**File:** `backend/services/portfolio_overview.py` (UPDATED)

- Import `classify_and_allocate` from new classifier
- Replace lines 62-69 with classifier-based allocation
- Support mixed funds with multi-component allocation

**Before:**
```python
fund_equity = 0
fund_bond = 0
fund_money = 0
# ... simple binary classification ...
```

**After:**
```python
fund_equity = 0
fund_bond = 0
fund_money = 0
fund_gold = 0

for h in fund_holdings:
    allocation = classify_and_allocate(
        code=h.get("code", ""),
        name=h.get("name", ""),
        nav_cost=h.get("costNav", 0),
        shares=h.get("shares", 0),
    )
    fund_equity += allocation["equity"]
    fund_bond += allocation["bond"]
    fund_money += allocation["money"]
    fund_gold += allocation["gold"]
```

### Step 3: Update Risk Module

**File:** `backend/services/risk.py` (UPDATED)

- Import `classify_fund` from new classifier
- Replace internal `_classify_asset()` with delegating wrapper
- Maintains backward compatibility

---

## Classification Rules

### Fund Type Keywords

```
货币基金:
  - "货币", "money", "余额", "现金", "宝宝", "理财"

债券基金:
  - "债", "bond", "纯债", "信用", "利率", "可转"

混合基金 (新增):
  - "混合", "灵活配置", "配置", "QDII", "偏股", "偏债"

股票基金:
  - "股票", "沪深", "创业", "科创", "医药", "消费", "新能源", "半导体", "ETF", "300", "500", "50"

黄金:
  - "黄金", "金ETF", "贵金属"
```

### Mixed Fund Allocation Rules

```
偏股混合:         {"equity": 0.70, "bond": 0.20, "money": 0.10}
偏债混合:         {"equity": 0.25, "bond": 0.60, "money": 0.15}
灵活配置混合:      {"equity": 0.60, "bond": 0.30, "money": 0.10}
QDII:           {"equity": 0.65, "bond": 0.25, "money": 0.10}
标准混合（默认）:   {"equity": 0.50, "bond": 0.35, "money": 0.15}
```

---

## Test Results

### Test Suite: `backend/tests/test_fund_classifier.py`

✅ **All tests passed**

```
=== 1. 纯正基金类型 ===
✓ 易方达消费行业 → equity
✓ 货币基金-中国银河 → money
✓ 中国债券基金 → bond
✓ 华泰柏瑞黄金ETF → gold

=== 2. 混合基金分类 ===
✓ 华夏成长混合 → mixed (is_mixed: True)
✓ 东方灵活配置混合 → mixed (is_mixed: True)
✓ 嘉实QDII基金 → mixed (is_mixed: True)
... (all mixed types correctly identified)

=== 3. MB-008 真实场景 ===
用户 5 只混合/QDII 基金 ¥541 总成本:
  权益类: 54.9% (previously 100% ❌)
  债券类: 33.4% (previously 0% ❌)
  现金类: 11.7% (previously 0% ❌)

✅ MB-008 bug 已修复！
```

---

## Impact Analysis

### Files Modified
1. ✅ `backend/services/portfolio_overview.py` - Updated to use new classifier
2. ✅ `backend/services/risk.py` - Updated to delegate to new classifier
3. ✅ `backend/services/fund_classifier.py` - NEW utility module

### APIs Affected
- `/api/cfo-summary` - Asset allocation calculation (fixed)
- `/api/steward/review` - Included via CFO summary (fixed)
- `/api/steward/briefing` - Included via CFO summary (fixed)
- Risk control checks - Now accurate for mixed funds (fixed)

### User Impact
- ✅ Correct asset allocation display (no more false 100% equity)
- ✅ No more false "股票仓位超出目标" alerts
- ✅ Accurate risk control thresholds applied
- ✅ Better portfolio health scoring

### Backward Compatibility
- ✅ All existing pure fund types still work correctly
- ✅ No changes to data persistence layer
- ✅ No changes to API contracts
- ✅ Transparent to frontend (same JSON structure)

---

## Deployment Checklist

- [x] Create new classifier module
- [x] Update portfolio_overview.py
- [x] Update risk.py
- [x] Write comprehensive tests
- [x] Verify all test cases pass
- [x] Document classification rules
- [ ] Deploy to production
- [ ] Monitor CFO dashboard metrics
- [ ] Verify user alerts

---

## Future Improvements

1. **Machine Learning Classification:** Train model on fund names/codes to auto-infer allocation ratios
2. **Real-time Allocation Data:** Integrate fund factsheets API to get actual allocation instead of heuristics
3. **User Feedback Loop:** Allow users to manually correct misclassified funds
4. **Audit Trail:** Log all fund classifications for future analysis
5. **A/B Testing:** Compare heuristic vs. API-based allocation

---

## References

- **Bug Report:** MB-008 (Asset allocation calculation)
- **Related Issues:** 
  - MB-019 "Stock position exceeds target" false alert
  - Risk control accuracy on mixed funds
- **Classification Standards:** Chinese fund naming conventions (AMAC)
- **Author:** Claude Code 2026-05-20

---

## Verification Commands

```bash
# Run tests
python3 backend/tests/test_fund_classifier.py

# Check imports
grep -r "from services.fund_classifier import" backend/

# Verify no breakage
grep -r "_classify_asset" backend/services/risk.py  # Should exist as wrapper
```

---

## Sign-off

**Status:** ✅ Ready for deployment  
**QA:** Pass all test cases  
**Performance:** No measurable impact (classifier is O(n) where n = keyword count ≈ 20)  
**Risk:** Low (isolated change, comprehensive test coverage)
