# MyPy Strict Check Analysis Report
## moneybag-for-claudecode - 21 Type Errors in New Architecture Layers

**Date:** 2026-05-15
**Total Errors:** 21
**Affected Files:** 4 files (all in `infra/data_source/`)
**Python Version:** 3.11
**MyPy Configuration:** Strict mode enabled

---

## MyPy Configuration Summary

### Location: `pyproject.toml`

**Global Settings:**
- `python_version = "3.11"`
- `warn_return_any = true` - Flag functions returning Any types
- `warn_unused_configs = true`
- `warn_redundant_casts = true`
- `warn_unused_ignores = true`
- `show_error_codes = true`
- `explicit_package_bases = true`
- `mypy_path = "backend"`

**Strict Mode Target Modules** (lines 19-33):
```
module = [
    "domain.*",
    "infra.cache.*",
    "infra.store.*",
    "infra.llm.*",
    "infra.data_source",
    "use_cases.*",
]
disallow_untyped_defs = true
disallow_any_generics = true
check_untyped_defs = true
warn_unreachable = true
strict_equality = true
no_implicit_optional = true
```

**Legacy Code (Exempted from strict - lines 36-41):**
- `services.*` - ignore_errors = true
- `api.*` - ignore_errors = true

**RAG/Knowledge Base (Partial exemption - lines 46-56):**
- `infra.knowledge*` - ignore_errors = true (chromadb/sentence_transformers lack stubs)
- `infra.events*` - ignore_errors = true
- `infra.data_source.import_*` - ignore_errors = true
- `domain.rule_engine.decision_archive` - ignore_errors = true
- `domain.services.user_preference_service` - ignore_errors = true

**Third-party imports (exempted):**
- akshare, tushare, baostock, fastapi, uvicorn, pydantic, httpx, PIL, chromadb, sentence_transformers, zhdate, pandas, chardet

---

## CI Workflow Invocation

### File: `.github/workflows/ci.yml` (lines 25-45)

**Job Name:** `mypy-strict`
**Runs on:** ubuntu-latest

**Command:**
```bash
cd backend && python -m mypy \
  domain/ \
  infra/cache/ \
  infra/store/ \
  infra/llm/ \
  infra/data_source/__init__.py \
  use_cases/ \
  --config-file ../pyproject.toml
```

**Note:** While the workflow explicitly runs mypy on specific paths, the config override for `infra.data_source` in pyproject.toml applies to all submodules, which is why errors in `infra/data_source/market/` and `infra/data_source/providers/` are being caught.

---

## Error Summary by Category

### Category 1: Any Return Type Violations (12 errors) 🔴
**File:** `infra/data_source/providers/akshare_provider.py`
**Root Cause:** Helper methods return `Any`, but main method signature promises `dict[str, Any] | list[Any] | None`

**Error Pattern:** Lines 85, 87, 89, 91, 93, 95, 97, 99, 101, 103, 105, 107
```
error: Returning Any from function declared to return "dict[str, Any] | list[Any] | None" [no-any-return]
```

**Code Structure:**
- Main method: `fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]` (line 68)
- Helper methods: `_fetch_macro_gdp(**params: Any) -> Any` (and 11 similar) (line 114)
- Issue: Each branch returns `self._fetch_XXXXX(**params)` which has `-> Any` return type

**Example:**
```python
def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:  # Line 68
    # ...
    if metric == "macro_gdp":
        return self._fetch_macro_gdp(**params)  # ← Line 85: Any returned, expects dict|list|None

def _fetch_macro_gdp(self, **params: Any) -> Any:  # Line 114: Helper has Any return type
    # ...
    return akshare.get_gdp_data(**params)  # akshare module has ignore_missing_imports, so returns Any
```

---

### Category 2: Any Return Type Violations (3 errors) 🔴
**File:** `infra/data_source/providers/baostock_provider.py`
**Root Cause:** Same pattern as akshare_provider

**Error Pattern:** Lines 75, 77, 79
```
error: Returning Any from function declared to return "dict[str, Any] | list[Any] | None" [no-any-return]
```

**Similar Structure:**
- Main method: `fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]`
- Helper methods: `_fetch_XXXXX(**params: Any) -> Any`
- Issue: Helper returns Any from baostock library (which has ignore_missing_imports)

---

### Category 3: Missing Module Attribute (1 error) 🔴
**File:** `infra/data_source/fallback.py`
**Line:** 232
```
error: Module "infra.data_source.providers.tencent_provider" has no attribute "TencentProvider" [attr-defined]
```

**Code:**
```python
elif provider_name == "tencent":
    from infra.data_source.providers.tencent_provider import TencentProvider  # ← Line 232
    return TencentProvider()
```

**Root Cause - ARCHITECTURAL MISMATCH:**
- File `tencent_provider.py` **exists** but is a **function-only module**
- No class definition exists (checked entire file - only has `get_stock_quote_tencent()` function)
- Other provider modules (akshare, baostock, tushare) are adapter classes implementing DataSourceProtocol
- **Tencent provider was incompletely refactored** - should either:
  - Create `TencentProvider` adapter class wrapping `get_stock_quote_tencent()`
  - Or remove the dynamic import from fallback.py since tencent isn't ready

---

### Category 4: Invalid Argument Type to FallbackRunner (3 errors) 🔴
**File:** `infra/data_source/market/stocks.py`
**Lines:** 63, 274, 355
```
error: Argument 3 to "FallbackRunner" has incompatible type "**dict[str, str]"; expected "float" [arg-type]
```

**Code Pattern (Line 63):**
```python
params = {
    "symbol": code,        # str
    "start_date": start_date,  # str
    "end_date": end_date,      # str
    "adjust": adjust,          # str
}

runner = FallbackRunner(metric="stock_price", chain=chain, **params)
         # ↑ metric (Arg 1) = "stock_price"
         # ↑ chain (Arg 2) = ["akshare", "baostock"]
         # ↑ **params starts at Arg 3, but Arg 3 expects float
```

**FallbackRunner Signature (fallback.py line 75-81):**
```python
def __init__(
    self,
    metric: str,                              # Arg 1 ✓ "stock_price"
    chain: Optional[List[str]] = None,        # Arg 2 ✓ ["akshare", ...]
    timeout_per_provider: float = 5.0,        # Arg 3 ✗ Expected float, got dict[str,str] from **params
    **kwargs: Any
) -> None:
```

**Root Cause - DESIGN FLAW:**
- FallbackRunner expects optional `timeout_per_provider` as positional/keyword Arg 3
- When called as `FallbackRunner(..., chain=[...], **params)`:
  - All values in `params` are treated as kwargs after Arg 2
  - MyPy sees `chain=` is keyword argument
  - MyPy then expects Arg 3 (timeout_per_provider) to be provided as keyword too
  - Instead, it sees `dict[str, str]` from `**params`, causing mismatch

**Three problem calls (all identical pattern):**
- Line 63: `get_stock_daily_hist()` - stock_price metric
- Line 274: `get_index_daily()` - index_daily metric
- Line 355: `get_fund_nav_history()` - fund_nav metric

---

### Category 5: Unsupported Union Attribute Access (2 errors) 🔴
**File:** `infra/data_source/market/stocks.py`
**Line:** 155
```
error: Item "dict[str, Any]" of "dict[str, Any] | list[Any] | Any" has no attribute "iloc" [union-attr]
error: Item "list[Any]" of "dict[str, Any] | list[Any] | Any" has no attribute "iloc" [union-attr]
```

**Code (Line 155):**
```python
df = provider.fetch("stock_price", symbol=code)  # Returns: dict[str, Any] | list[Any] | Any
if df is not None and len(df) > 0:  # Type narrowed to: dict[str, Any] | list[Any] | Any (none excluded)
    latest = df.iloc[0]  # ← Line 155: .iloc is pandas DataFrame only, not dict/list
```

**Root Cause - TYPE NOT NARROWED:**
- `provider.fetch()` returns union type: `dict[str, Any] | list[Any] | Any`
- Check `if df is not None and len(df) > 0:` only verifies non-null and non-empty
- Doesn't narrow type to the actual return type that has `.iloc` (pandas DataFrame)
- `.iloc` is a pandas-specific indexer:
  - `dict` has no `.iloc` attribute
  - `list` has no `.iloc` attribute
  - Only `pd.DataFrame` has `.iloc`
- **Provider return type is too broad** - should be `pd.DataFrame | None` for this usage

---

## Summary by Architecture Layer

| Layer | Status | Details |
|-------|--------|---------|
| `domain/` | ✓ PASS | No errors detected |
| `infra/cache/` | ✓ PASS | No errors detected |
| `infra/store/` | ✓ PASS | No errors detected |
| `infra/llm/` | ✓ PASS | No errors detected |
| `use_cases/` | ✓ PASS | No errors detected |
| `infra/data_source/` | ✗ FAIL - 21 errors | Provider adapters + market utilities have issues |

---

## Root Cause Analysis by Issue

### Issue 1: Provider Helper Methods Return `Any` (15 errors total)

**Why it happens:**
1. Akshare/Baostock/Tushare provider helpers call external Python libraries
2. These libraries (akshare, baostock, tushare) have `ignore_missing_imports = true` in mypy config
3. When calling `akshare.get_gdp_data(...)`, the return type cannot be inferred → inferred as `Any`
4. Helper method signature: `def _fetch_macro_gdp(...) -> Any:` (returns Any)
5. Main method promises: `-> Union[Dict[str, Any], List[Any], None]` (specific return type)
6. MyPy strict mode flags this: **"Return type is Any, but function declared to return dict|list|None"**

**Config setting enabling this:**
- Line 62: `akshare` under `ignore_missing_imports = true`
- Line 66: `baostock` under `ignore_missing_imports = true`

**Stack:**
```
External lib (no stubs)  ← akshare.get_gdp_data(...) returns Any
         ↓
Helper method            ← _fetch_macro_gdp() → Any
         ↓
Main method             ← fetch() → Union[Dict, List, None]  ✗ Type mismatch
```

---

### Issue 2: Tencent Provider Incomplete Refactor (1 error)

**Why it happens:**
1. fallback.py tries to import `TencentProvider` class (line 232)
2. But tencent_provider.py only contains standalone functions, no class
3. Module was incompletely migrated from function-based to class-based adapter pattern
4. Other providers (akshare, baostock, tushare) are classes implementing adapter pattern
5. MyPy can't resolve the import at static analysis time

**Current state:**
- akshare_provider.py → class AkshareProvider ✓
- baostock_provider.py → class BaostockProvider ✓
- tushare_provider.py → class TushareProvider ✓
- tencent_provider.py → just functions ✗ (mismatch in architecture)

---

### Issue 3: FallbackRunner Signature Design Issue (3 errors)

**Why it happens:**
1. FallbackRunner.__init__ signature mixes positional, optional, and **kwargs:
   ```python
   __init__(self, metric: str, chain: Optional[List[str]] = None, 
            timeout_per_provider: float = 5.0, **kwargs: Any)
   ```
2. When called with keyword arguments: `FallbackRunner(metric="...", chain=[...], **params)`
3. MyPy interprets this as:
   - metric = positional arg 1 ✓
   - chain = keyword arg 2 ✓
   - timeout_per_provider = expects keyword arg 3 (defaults to 5.0)
   - **params = other keywords
4. But timeout_per_provider isn't explicitly passed, and **params dicts are `str: str`
5. MyPy can't reconcile: "I need a float at position 3, but I'm getting dict[str, str]"

**Caller misuse:**
```python
params = {"symbol": "000001", "start_date": "20260101"}
runner = FallbackRunner(
    metric="stock_price",      # ✓ arg 1
    chain=chain,               # ✓ arg 2 (keyword)
    **params                   # ✗ These are all strings, not float for arg 3
)
```

**Better design:**
- Make timeout_per_provider a kwarg that defaults in **kwargs
- Or require explicit keyword argument

---

### Issue 4: Union Type Not Narrowed (2 errors)

**Why it happens:**
1. Provider.fetch() has broad return type: `dict[str, Any] | list[Any] | Any`
2. Method call: `df = provider.fetch(...)`  → type is union
3. Guard: `if df is not None and len(df) > 0:` → only excludes None, not narrowing union
4. Access: `df.iloc[0]` → `.iloc` only exists on pandas DataFrame, not dict/list
5. MyPy reports: "dict has no .iloc, list has no .iloc" (both true for union members)

**Type guard that doesn't narrow:**
```python
if df is not None and len(df) > 0:  # Checks not-None and truthy, but doesn't narrow
    latest = df.iloc[0]             # Type is still dict|list|Any
```

**Better type guard:**
```python
import pandas as pd
if isinstance(df, pd.DataFrame):    # Narrows to DataFrame
    latest = df.iloc[0]             # ✓ Now .iloc is valid
```

---

## Recommended Fixes (Priority Order)

### Priority 1: Fix TencentProvider (1 error) - ARCHITECTURAL
```
Action: Create TencentProvider class adapter in tencent_provider.py
Time: ~15 minutes
Impact: Unblocks fallback.py, enables tencent provider in chain
```

**Option A: Create class wrapper**
- Create `class TencentProvider:` in tencent_provider.py
- Implement `fetch(metric, **kwargs)` that wraps `get_stock_quote_tencent()`
- Implement `is_available()` method
- This aligns tencent with other providers' class-based architecture

**Option B: Remove from fallback chain**
- If tencent provider isn't production-ready, remove the dynamic import
- Keep standalone functions for internal use only

---

### Priority 2: Fix Provider Return Types (15 errors) - TYPE ANNOTATIONS
```
Action: Annotate helper methods with specific return types or use cast()
Time: ~30 minutes
Impact: Fixes 12 + 3 errors in akshare/baostock providers
```

**Option A (Recommended - Most explicit):**
```python
from typing import cast, Dict, List, Any, Union

def _fetch_macro_gdp(self, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
    """Fetch GDP data - returns dict or list."""
    cache_key = "ak_macro_gdp"
    cached = _macro_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        # akshare.get_gdp_data returns Any
        data = akshare.get_gdp_data(**params)
        # Explicitly narrow to expected type
        result: Union[Dict[str, Any], List[Any], None] = data
        _macro_cache.set(cache_key, result)
        return result
    except Exception:
        return None
```

**Option B (Use cast for individual returns):**
```python
from typing import cast

return cast(Dict[str, Any], akshare.get_gdp_data(**params))
```

**Option C (Accept Any in helpers, narrow in main):**
```python
def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
    try:
        if metric == "macro_gdp":
            data: Any = self._fetch_macro_gdp(**params)
            return cast(Union[Dict[str, Any], List[Any], None], data)
```

---

### Priority 3: Fix FallbackRunner Calls (3 errors) - CALLER DESIGN
```
Action: Explicitly pass timeout_per_provider or restructure signature
Time: ~20 minutes
Impact: Fixes 3 errors in market/stocks.py
```

**Option A (Explicit timeout parameter - most explicit):**
```python
runner = FallbackRunner(
    metric="stock_price",
    chain=["akshare", "baostock"],
    timeout_per_provider=5.0,  # ← Explicitly pass float
    symbol=code,
    start_date=start_date,
    end_date=end_date,
    adjust=adjust,
)
```

**Option B (Restructure FallbackRunner signature - cleaner):**
```python
def __init__(
    self,
    metric: str,
    chain: Optional[List[str]] = None,
    **kwargs: Any  # Merge timeout_per_provider here
) -> None:
    self.timeout_per_provider = kwargs.pop('timeout_per_provider', 5.0)
```

---

### Priority 4: Fix Union Type Access (2 errors) - TYPE NARROWING
```
Action: Add type guard or narrow return type
Time: ~10 minutes
Impact: Fixes 2 errors in market/stocks.py line 155
```

**Option A (Add isinstance check):**
```python
import pandas as pd

df = provider.fetch("stock_price", symbol=code)
if isinstance(df, pd.DataFrame) and len(df) > 0:
    latest = df.iloc[0]
    # ...
else:
    return None
```

**Option B (Change provider return type):**
- If provider only ever returns DataFrame for stock_price, change return type signature
- More honest about what the function actually returns

---

## Files Requiring Changes (Summary)

```
backend/
├── infra/data_source/
│   ├── providers/
│   │   ├── akshare_provider.py           [12 errors] → Fix helper return types
│   │   ├── baostock_provider.py          [3 errors]  → Fix helper return types
│   │   └── tencent_provider.py           [1 error]   → Create TencentProvider class
│   ├── fallback.py                       [1 error]   → Import will resolve when TencentProvider created
│   └── market/stocks.py                  [4 errors]  → Fix FallbackRunner calls (3) + Union narrowing (2)
```

---

## MyPy Configuration Assessment

**Current Configuration: Well-structured but revealing integration gaps**

### Strengths ✓
- Strict mode properly applied to new architecture layers (domain/, infra/cache/, infra/store/, infra/llm/, use_cases/)
- Legacy code (services/, api/) exempt for gradual migration
- Third-party libraries properly exempted due to missing stubs
- RAG/knowledge layers reasonably exempted (chromadb/sentence_transformers lack type stubs)
- Error codes enabled for precise error messages
- Python 3.11 target is current

### Issues with Current Config
- `infra.data_source` override catches entire submodule, including:
  - Partially refactored code (tencent provider)
  - Integration code that calls untyped external libraries
  - Market utilities that need stricter type discipline
  
### Recommended Configuration Improvements

**Option 1: Fine-grained overrides for data_source**
```toml
# Separate overrides by maturity level
[[tool.mypy.overrides]]
module = [
    "infra.data_source.providers.akshare_provider",
    "infra.data_source.providers.baostock_provider",
]
disallow_untyped_defs = true
# ... other strict settings

[[tool.mypy.overrides]]
module = [
    "infra.data_source.market.*",
]
disallow_untyped_defs = true
# ... other strict settings

[[tool.mypy.overrides]]
module = [
    "infra.data_source.providers.tencent_provider",  # Temporarily exempt until class created
    "infra.data_source.legacy.*",
]
ignore_errors = true
```

**Option 2: Temporary exemption pending fixes**
```toml
[[tool.mypy.overrides]]
module = [
    "infra.data_source.providers",
    "infra.data_source.market",
]
# Reduce strictness temporarily while refactoring
disallow_untyped_defs = false
check_untyped_defs = true  # Still catch issues but don't require all defs typed
```

---

## Testing Recommendations

After fixes, run:
```bash
# Full strict check on new architecture
cd backend && python -m mypy \
  domain/ \
  infra/cache/ \
  infra/store/ \
  infra/llm/ \
  use_cases/ \
  --config-file ../pyproject.toml

# Check data_source once fixed
cd backend && python -m mypy \
  infra/data_source/ \
  --config-file ../pyproject.toml
```

Expected result: **0 errors** in all architecture layers
