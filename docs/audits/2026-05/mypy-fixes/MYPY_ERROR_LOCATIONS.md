# MyPy Error Locations — Interactive Map

## Error Count by File

```
infra/data_source/providers/akshare_provider.py    ████████████ 12 errors (57%)
infra/data_source/providers/baostock_provider.py   ███ 3 errors (14%)
infra/data_source/market/stocks.py                 ███ 5 errors (24%)
infra/data_source/fallback.py                      █ 1 error (5%)
────────────────────────────────────────────────────────────────────
                                          TOTAL:  21 errors
```

---

## Error Group 1: akshare_provider.py (12 errors)

**Pattern:** `-> Any` return type conflicts with parent's `-> dict[str, Any] | list[Any] | None`

### Lines with Errors:
```python
85  │ return self._fetch_macro_gdp(**params)      # ERROR: no-any-return
87  │ return self._fetch_macro_cpi(**params)       # ERROR: no-any-return
89  │ return self._fetch_macro_pmi(**params)       # ERROR: no-any-return
91  │ return self._fetch_macro_shibor(**params)    # ERROR: no-any-return
93  │ return self._fetch_macro_lpr(**params)       # ERROR: no-any-return
95  │ return self._fetch_macro_m1_m2(**params)     # ERROR: no-any-return
97  │ return self._fetch_stock_news(**params)      # ERROR: no-any-return
99  │ return self._fetch_northbound_flow(**params) # ERROR: no-any-return
101 │ return self._fetch_margin_detail(**params)   # ERROR: no-any-return
103 │ return self._fetch_block_trade(**params)     # ERROR: no-any-return
105 │ return self._fetch_fund_name(**params)       # ERROR: no-any-return
107 │ return self._fetch_fund_rank(**params)       # ERROR: no-any-return
```

### Root Cause:
```python
# Line 68: Return type is declared as union
def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
    """..."""
    # But helper methods return Any
    
# Line 114: Helper returns bare Any
def _fetch_macro_gdp(self, **params: Any) -> Any:
    """..."""
    df = ak.macro_china_gdp()  # Actually returns DataFrame or None
    # But declared as just Any
    return df
```

### Fix Strategy:
```python
# BEFORE:
def _fetch_macro_gdp(self, **params: Any) -> Any:
    return df

# AFTER (Option 1 - Explicit):
def _fetch_macro_gdp(self, **params: Any) -> pd.DataFrame | None:
    return df

# AFTER (Option 2 - Match parent):
def _fetch_macro_gdp(self, **params: Any) -> dict[str, Any] | list[Any] | None:
    return df
```

---

## Error Group 2: baostock_provider.py (3 errors)

**Pattern:** Same as akshare_provider.py

### Lines with Errors:
```python
75  │ return self._fetch_stock_price(**params)     # ERROR: no-any-return
77  │ return self._fetch_index_daily(**params)     # ERROR: no-any-return
79  │ return self._fetch_stock_industry(**params)  # ERROR: no-any-return
```

### Root Cause:
```python
# Line 54: Return type declared as union
def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
    """..."""

# Line 85: Helper returns Any
def _fetch_stock_price(self, **params: Any) -> Any:
    """Fetch stock daily OHLCV history."""
```

### Fix Strategy:
Same as akshare_provider.py — change helper methods to explicit return types

---

## Error Group 3: fallback.py (1 error)

**Pattern:** Missing module attribute

### Line with Error:
```python
232 │ from infra.data_source.providers.tencent_provider import TencentProvider
    │                                                           ^^^^^^^^^^^^^^^^
    │                                                           ERROR: attr-defined
    └─→ module "infra.data_source.providers.tencent_provider" has no attribute "TencentProvider"
```

### Root Cause:
```python
# What tencent_provider.py actually exports:
def get_stock_quote_tencent(code: str) -> Optional[dict]:
    """Function-based API"""
    pass

# What fallback.py expects:
class TencentProvider:
    def fetch(self, metric: str, **params: Any) -> ...:
        pass
```

### Fix Strategy:
**Option A:** Create `TencentProvider` class wrapper
```python
# In tencent_provider.py
class TencentProvider:
    def fetch(self, metric: str, **params: Any) -> Optional[dict]:
        code = params.get("symbol", "")
        return get_stock_quote_tencent(code)
```

**Option B:** Update fallback.py to handle function-based provider
```python
elif provider_name == "tencent":
    from infra.data_source.providers.tencent_provider import get_stock_quote_tencent
    # Wrap function call differently, not as class instantiation
```

---

## Error Group 4: market/stocks.py (5 errors)

### Error 1-3: FallbackRunner kwargs type mismatch

#### Lines with Errors:
```python
63  │ runner = FallbackRunner(metric="stock_price", chain=chain, **params)
    │                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    │                                              ERROR: arg-type (× multiple)
    └─→ params is dict[str, str], but FallbackRunner expects float for timeout_per_provider

274 │ runner = FallbackRunner(metric="index_daily", chain=chain, **params)
    │                        ERROR: arg-type

355 │ runner = FallbackRunner(metric="fund_nav", chain=chain, **params)
    │                        ERROR: arg-type
```

#### Root Cause:
```python
# Lines 52-58: params are all strings
params = {
    "symbol": code,           # str
    "start_date": start_date, # str
    "end_date": end_date,     # str
    "adjust": adjust,         # str ← Type is dict[str, str]
}

# Line 75-80: FallbackRunner expects a float parameter
class FallbackRunner:
    def __init__(
        self,
        metric: str,
        chain: Optional[List[str]] = None,
        timeout_per_provider: float = 5.0,  # ← Expects float!
        **kwargs: Any
    ) -> None:
```

When unpacking `**params`, mypy sees a type conflict.

#### Fix Strategy:

**Option 1:** Explicit parameter passing (preferred)
```python
# BEFORE:
runner = FallbackRunner(metric="stock_price", chain=chain, **params)

# AFTER:
runner = FallbackRunner(
    metric="stock_price",
    chain=chain,
    symbol=code,
    start_date=start_date,
    end_date=end_date,
    adjust=adjust
)
```

**Option 2:** Restructure FallbackRunner signature
```python
# Make timeout_per_provider Optional or move to kwargs
def __init__(
    self,
    metric: str,
    chain: Optional[List[str]] = None,
    **kwargs: Any  # Include timeout_per_provider here
) -> None:
    self.timeout_per_provider = kwargs.pop("timeout_per_provider", 5.0)
```

---

### Error 4-5: Union type narrowing failure

#### Lines with Errors:
```python
153 │ df = provider.fetch("stock_price", symbol=code)
154 │ if df is not None and len(df) > 0:
155 │     latest = df.iloc[0]
    │     ^^^^^^^^^^
    │     ERROR: union-attr (× 2)
    ├─→ Item "dict[str, Any]" of union has no attribute "iloc"
    └─→ Item "list[Any]" of union has no attribute "iloc"
```

#### Root Cause:
```python
# provider.fetch() returns:
def fetch(self, metric: str, **params: Any) -> dict[str, Any] | list[Any] | Any:

# After:
df = provider.fetch(...)  # Type is: dict[str, Any] | list[Any] | Any

if df is not None and len(df) > 0:
    # After None check: dict[str, Any] | list[Any] | Any
    # Still union of dict and list!
    latest = df.iloc[0]  # ERROR: dict and list don't have .iloc
    # Only pd.DataFrame has .iloc
```

The `None` check doesn't narrow away `dict` and `list`, only removes `None` from union.

#### Fix Strategy:

**Option 1:** Add isinstance check (explicit narrowing)
```python
df = provider.fetch("stock_price", symbol=code)
if isinstance(df, pd.DataFrame):
    latest = df.iloc[0]
    # Now mypy knows df is DataFrame
```

**Option 2:** Cast explicitly
```python
from typing import cast
import pandas as pd

df = provider.fetch("stock_price", symbol=code)
if df is not None and len(df) > 0:
    df_frame = cast(pd.DataFrame, df)
    latest = df_frame.iloc[0]
```

**Option 3:** Fix provider return type to always return DataFrame
```python
# In TushareProvider
def fetch(self, metric: str, **params: Any) -> pd.DataFrame | None:
    """Always return DataFrame, never dict/list"""
    # ...
```

---

## Summary Table: All 21 Errors

| Error # | File | Line | Type | Severity | Fix Complexity |
|---------|------|------|------|----------|---|
| 1-12 | akshare_provider.py | 85-107 | no-any-return | HIGH | LOW |
| 13-15 | baostock_provider.py | 75-79 | no-any-return | HIGH | LOW |
| 16 | fallback.py | 232 | attr-defined | MEDIUM | MEDIUM |
| 17-19 | market/stocks.py | 63,274,355 | arg-type | MEDIUM | MEDIUM |
| 20-21 | market/stocks.py | 155 | union-attr | LOW | LOW |

---

## Recommended Fix Order

1. **First (Quick wins):** Fix helper methods in akshare & baostock (15 errors)
   - 5 minutes per file
   - Changes: 15 `-> Any` → `-> pd.DataFrame | None`

2. **Second:** Fix TencentProvider wrapper (1 error)
   - 10 minutes
   - Either create class wrapper or update fallback.py

3. **Third:** Fix FallbackRunner callers (3 errors)
   - 15 minutes
   - Either explicit params or refactor signature

4. **Last:** Add union type guards (2 errors)
   - 10 minutes
   - Add isinstance() checks before .iloc usage

**Total time estimate:** 40 minutes to fix all 21 errors
