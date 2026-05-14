# MyPy Strict Check Investigation — Complete Report Index

**Date:** May 15, 2026  
**Status:** 21 errors identified and documented  
**Scope:** New architecture layers (domain/, infra/cache/, infra/store/, infra/llm/, use_cases/, infra/data_source)

---

## 📚 Report Files

Choose your reading style:

### 1. **MYPY_STRICT_ANALYSIS.md** (Executive Summary + Deep Dive)
- **Best for:** Understanding the full context
- **Includes:**
  - MyPy configuration breakdown (pyproject.toml analysis)
  - CI workflow integration details
  - Comprehensive root cause analysis for each error group
  - Type annotation issues summary
  - Remediation options (A, B, C strategies)
  - Recommended action plan by priority
  - Key configuration facts
- **Read time:** 15-20 minutes
- **Audience:** Tech leads, architects, those making remediation decisions

### 2. **MYPY_ERROR_LOCATIONS.md** (Line-by-Line Reference)
- **Best for:** Developers fixing the issues
- **Includes:**
  - Visual error count breakdown by file
  - Exact line numbers for all 21 errors
  - Code snippets showing before/after
  - Specific fix strategies for each error pattern
  - Implementation examples for each solution
  - Summary table with severity ratings
  - Fix order recommendation with time estimates
- **Read time:** 10-15 minutes
- **Audience:** Developers implementing fixes

### 3. **MYPY_ERRORS_SUMMARY.txt** (Quick Reference)
- **Best for:** Quick understanding of the problem
- **Includes:**
  - Configuration overview
  - CI workflow quick facts
  - Error breakdown table
  - Detailed error patterns with code
  - Recommended fixes by priority
  - Key facts bullet list
- **Read time:** 5-10 minutes
- **Audience:** Anyone who needs the TL;DR

---

## 🎯 Quick Facts

| Metric | Value |
|--------|-------|
| Total Errors | 21 |
| Files Affected | 4 |
| Primary Culprit | akshare_provider.py (12 errors) |
| Configuration Status | ✅ Correct |
| Other Layers Status | ✅ 0 errors |
| Estimated Fix Time | 40 minutes |
| Recommended Approach | Fix type annotations (not disable mypy) |

---

## 📊 Error Distribution

```
akshare_provider.py        ████████████ 12 (57%)
market/stocks.py           ████ 5 (24%)
baostock_provider.py       ███ 3 (14%)
fallback.py                █ 1 (5%)
```

---

## 🔍 Error Categories

1. **No-Any-Return Errors (15)** — Helper methods return `Any` instead of declared union type
   - akshare_provider.py: 12 errors
   - baostock_provider.py: 3 errors
   - **Fix:** Change `-> Any` to `-> pd.DataFrame | None`

2. **Arg-Type Errors (3)** — FallbackRunner kwargs type mismatch
   - market/stocks.py: 3 errors
   - **Fix:** Use explicit parameter passing instead of `**dict` unpacking

3. **Attr-Defined Error (1)** — Missing TencentProvider class
   - fallback.py: 1 error
   - **Fix:** Create class wrapper or refactor provider handling

4. **Union-Attr Errors (2)** — Type narrowing failure
   - market/stocks.py: 2 errors
   - **Fix:** Add `isinstance()` checks before using DataFrame-specific methods

---

## 🚀 Recommended Reading Path

### For Understanding the Problem (30 min total):
1. Read **MYPY_ERRORS_SUMMARY.txt** (5 min)
2. Skim **MYPY_ERROR_LOCATIONS.md** sections 1-2 (10 min)
3. Review **MYPY_STRICT_ANALYSIS.md** section III (15 min)

### For Implementing Fixes (45 min total):
1. Reference **MYPY_ERROR_LOCATIONS.md** for exact line numbers
2. Copy code snippets from "Fix Strategy" sections
3. Follow "Recommended Fix Order" at bottom of locations file
4. Test with: `cd backend && python -m mypy infra/data_source/ --config-file ../pyproject.toml`

### For Architecture Review (20 min total):
1. Read **MYPY_STRICT_ANALYSIS.md** section I (configuration)
2. Skim section VI (remediation options A/B/C)
3. Review section VII (recommended action plan)

---

## 📝 Key Takeaways

### What's Working Well ✅
- **Configuration:** Strict mode is correctly configured in pyproject.toml
- **CI Integration:** CI job properly checks architecture layers
- **Other Layers:** domain/, use_cases/, cache/, store/, llm/ have **0 errors**
- **Best Practice:** Transitively checks providers via imports

### What Needs Fixing ❌
- **Provider Types:** Helper methods use bare `Any` instead of explicit types
- **TencentProvider:** Function-based API but class expected by fallback.py
- **FallbackRunner Usage:** Kwargs unpacking conflicts with typed parameters
- **Union Type Guards:** Missing narrowing checks for DataFrame operations

### Why It Matters 🎯
- **Type Safety:** Incomplete types defeat purpose of strict checking
- **Maintainability:** Developers can't rely on types for IDE autocomplete
- **Data Layer:** Infrastructure layer should have highest type safety
- **CI Enforcement:** Build will fail until fixed

---

## 🔧 Implementation Checklist

- [ ] Read MYPY_ERROR_LOCATIONS.md
- [ ] Create TencentProvider wrapper class
- [ ] Fix 12 errors in akshare_provider.py (change `-> Any` to explicit types)
- [ ] Fix 3 errors in baostock_provider.py (same pattern)
- [ ] Fix 3 FallbackRunner call sites (explicit params or refactor)
- [ ] Add isinstance() checks in market/stocks.py
- [ ] Run: `cd backend && python -m mypy infra/data_source/ --config-file ../pyproject.toml`
- [ ] Verify: 0 errors
- [ ] Commit with message: "fix: complete type annotations in data_source layer"

---

## 💡 Pro Tips

1. **Test incrementally:** Fix one file at a time, test after each
2. **Use modern syntax:** Python 3.11 supports `X | Y` instead of `Union[X, Y]`
3. **pandas imports:** Add `import pandas as pd` for type hints
4. **Check dependencies:** Ensure tushare_provider.py types are also correct
5. **Review patterns:** Once you understand error group 1, error groups 2-4 are straightforward

---

## 📞 Questions?

If unclear on any aspect:
- **Configuration:** See MYPY_STRICT_ANALYSIS.md sections I-II
- **Specific line:** See MYPY_ERROR_LOCATIONS.md with exact line numbers
- **Fix strategy:** Each error has 2-3 recommended approaches
- **Priority:** See "Recommended Fix Order" in MYPY_ERROR_LOCATIONS.md

---

## 📋 Metadata

- **Investigation Date:** 2026-05-15
- **Mypy Version:** Latest (3.11 compatible)
- **Python Version:** 3.11
- **Project:** MoneyBag (新架构 M1 W4)
- **Report Generated:** By Claude Code automated analysis
