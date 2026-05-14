# MyPy Strict Check Analysis - Complete Report

## 📋 Quick Navigation

This analysis documents **21 type checking errors** found by mypy in the `moneybag-for-claudecode` project.

### Report Files

| File | Purpose | Audience |
|------|---------|----------|
| **MYPY_ERRORS_QUICK_REFERENCE.txt** | One-page error summary with fix priority | Quick lookup during fixing |
| **MYPY_STRICT_ANALYSIS.md** | Comprehensive 400+ line detailed analysis | Engineers implementing fixes |
| Other MYPY_*.md/txt files | Intermediate analysis files | Reference during investigation |

---

## 🎯 Quick Summary

**Status:** 21 errors in `infra/data_source/` layer only  
**Good News:** All new architecture layers (domain/, infra/cache/, infra/store/, infra/llm/, use_cases/) pass ✓  
**Effort to Fix:** ~75 minutes  
**Blocking CI:** Yes - mypy-strict job fails

### Error Breakdown

| Category | Count | Files | Root Cause |
|----------|-------|-------|-----------|
| Provider return type (Any) | 15 | akshare (12), baostock (3) | Untyped external libraries |
| Missing TencentProvider class | 1 | fallback.py | Incomplete refactor |
| FallbackRunner type mismatch | 3 | market/stocks.py | Signature design flaw |
| Union type narrowing | 2 | market/stocks.py | Missing isinstance check |

---

## 🚀 To Fix These Errors

### Step 1: Start Here
Read: `MYPY_ERRORS_QUICK_REFERENCE.txt` (2 mins)

### Step 2: Detailed Analysis
Read: `MYPY_STRICT_ANALYSIS.md` (10 mins)
- Skip to the specific section for the error you're fixing
- Each section has code examples and multiple fix options

### Step 3: Apply Fixes (Priority Order)

1. **TencentProvider class** (15 min, 1 error)
   - File: `backend/infra/data_source/providers/tencent_provider.py`
   - See MYPY_STRICT_ANALYSIS.md → "Priority 1" section

2. **Provider return types** (30 min, 15 errors)
   - Files: `akshare_provider.py`, `baostock_provider.py`
   - See MYPY_STRICT_ANALYSIS.md → "Priority 2" section

3. **FallbackRunner calls** (20 min, 3 errors)
   - File: `market/stocks.py`
   - See MYPY_STRICT_ANALYSIS.md → "Priority 3" section

4. **Union type narrowing** (10 min, 2 errors)
   - File: `market/stocks.py` line 155
   - See MYPY_STRICT_ANALYSIS.md → "Priority 4" section

### Step 4: Verify
```bash
cd backend && python -m mypy \
  domain/ \
  infra/cache/ \
  infra/store/ \
  infra/llm/ \
  use_cases/ \
  --config-file ../pyproject.toml
```

Expected: **0 errors**

---

## 📊 Architecture Status

✅ **New Core Architecture (HEALTHY)**
- `domain/` → 0 errors
- `infra/cache/` → 0 errors
- `infra/store/` → 0 errors
- `infra/llm/` → 0 errors
- `use_cases/` → 0 errors

⚠️ **Integration Layer (NEEDS FIXES)**
- `infra/data_source/` → 21 errors
  - `providers/` → 16 errors (untyped external libs)
  - `market/` → 5 errors (design + type narrowing)

---

## 🔧 Configuration Context

**MyPy Config:** `pyproject.toml` (lines 5-89)
- Python 3.11
- Strict mode: enabled for new architecture layers
- Legacy code (services/, api/): exempted with ignore_errors
- External libs (akshare, baostock, etc.): exempted with ignore_missing_imports

**CI Check:** `.github/workflows/ci.yml` (lines 25-45)
- Job: `mypy-strict`
- Status: FAILING

---

## 💡 Key Insights

### Why These Errors Exist

1. **External Libraries Have No Type Stubs**
   - akshare, baostock, tushare libraries don't provide type information
   - When these return values, mypy infers type as `Any`
   - Providers promise specific return types but call untyped libraries

2. **Incomplete Refactoring**
   - tencent_provider.py is function-based, not class-based like others
   - Fallback orchestrator expects all providers to be classes

3. **Type System Gaps**
   - Union types (dict | list | Any) used without narrowing
   - Optional parameters mixed with **kwargs confuses type checker

### Why Core Architecture Passes

- Domain logic doesn't call external untyped libraries
- Uses clear, typed interfaces between layers
- Proper type annotations throughout
- No union type shenanigans

---

## 📞 Questions?

- **Error is confusing?** → Search MYPY_STRICT_ANALYSIS.md for your error code
- **Need code example?** → See Priority sections in MYPY_STRICT_ANALYSIS.md
- **Want a fix option?** → Each Priority section has Option A/B/C with tradeoffs
- **Need quick lookup?** → Use MYPY_ERRORS_QUICK_REFERENCE.txt

---

## 📁 All Affected Files

```
backend/infra/data_source/
├── providers/
│   ├── akshare_provider.py           [12 errors]
│   ├── baostock_provider.py          [3 errors]
│   └── tencent_provider.py           [1 error - missing class]
├── fallback.py                       [1 error - import fails]
└── market/stocks.py                  [5 errors - 3 arg-type, 2 union-attr]
```

---

Generated: 2026-05-15  
Analysis Tool: mypy 1.x strict mode  
Architecture: Layered (domain/infra/use_cases pattern)
