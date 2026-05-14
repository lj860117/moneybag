# MoneyBag AKShare Single-Point Dependency Analysis
## Date: 2026-05-14

---

## Executive Summary

The MoneyBag backend has **multiple critical services with single-point AKShare dependencies** (no fallback chains):

### High-Risk Services (⚠️ BLOCKING)
1. **`sector_rotation.py`** — 100% dependent on AKShare (同花顺行业板块 API)
   - `get_sector_ranking()` → NO FALLBACK
   - Returns empty dict on failure
   
2. **`market_data.py`** — Mixed risk:
   - `get_fear_greed_index()` — Has fallback (to fallback calculation)
   - `get_valuation_percentile()` — HAS fallback (4-tier chain: AKShare → Tushare → AKShare alt → CSIndex)
   - `get_fund_nav()` — HAS fallback (to Tushare)

3. **`news_data.py`** — AKShare-only, graceful degradation:
   - `get_fund_news()`, `get_market_news()`, `get_policy_news()` — NO real fallback
   - Returns hardcoded "loading..." message on failure
   - `get_stock_news()` wrapper in `infra/data_source/macro/indicators.py` only calls AKShare

4. **`global_market.py`** — Pure AKShare dependency:
   - `get_us_indices()` → `get_us_index()` (AKShare only)
   - `get_forex_data()` → `get_fx_spot_quote()` (AKShare only)
   - `get_fed_rate()` → `get_usa_interest_rate()` (AKShare only)
   - `get_global_pe()` → `get_global_market_pe()` (AKShare only, with intra-service fallback)

5. **`stock_price_provider.py`** — Better but not complete:
   - Has Tushare → AKShare chain ✓
   - But infrastructure layer still has issues (see below)

---

## Detailed Analysis by Service

### 1. sector_rotation.py — ⚠️ CRITICAL

**Function:** `get_sector_ranking()`

**Current Flow:**
```
get_sector_ranking()
  └─ get_industry_board_summary()
     └─ infra/data_source/alt/flows.py
        └─ ak.stock_board_industry_summary_ths()  ← AKSHARE ONLY
```

**Failure Behavior:**
- Returns: `{"available": False, "error": "行业数据不足", "source": "ths"}`
- NO fallback to other industry data sources
- Pipeline still marks it as `available: False` with `score: 0.5` (default)

**Single Point:** Yes (100%)
- Tushare has `industry_detail()` but not being used
- BaoStock has industry data but not being used
- Mootdx has no industry API

**Missing Fallback Chain:**
1. AKShare 同花顺 (current)
2. Tushare `pro.index_classify()` + historical (not tried)
3. Mock data or skip (not tried)

---

### 2. market_data.py — Mixed Risk

#### 2.1 get_fear_greed_index()

**Function:** `get_fear_greed_index()`

**Current Flow:**
```
get_fear_greed_index()
  └─ get_index_daily("sh000300")
     └─ infra/data_source/market/stocks.py
        └─ ak.stock_zh_index_daily()  ← AKSHARE
```

**Failure Behavior:**
- Returns default: `{"score": 50, "level": "中性", "dimensions": {}}`
- NO actual fallback to Tushare/BaoStock/mootdx for the 沪深300 daily data
- Uses catch-all `except Exception: pass` without recovery

**Missing Fallback:**
```python
# Should try:
1. AKShare stock_zh_index_daily("sh000300")
2. Tushare index_daily(ts_code="399300.SZ")  # 沪深300
3. mootdx daily("000300")
```

---

#### 2.2 get_valuation_percentile()

**Function:** `get_valuation_percentile()`

**Current Flow (4-tier FALLBACK CHAIN):**
```
Attempt 1: get_index_pe("沪深300")
   └─ AKShare stock_index_pe_lg()
      └─ Uses "滚动市盈率中位数" column
      └─ On SUCCESS: return percentile
      └─ On FAIL: try Attempt 2

Attempt 2: Tushare pro.index_dailybasic("399300.SZ")
   └─ On SUCCESS: return percentile with ⚠️ WARNING (weighted PE, not equal-weight)
   └─ On FAIL: try Attempt 3

Attempt 3: get_index_pe("沪深300") again
   └─ Uses "滚动市盈率" column (fallback column)
   └─ On SUCCESS: return percentile
   └─ On FAIL: try Attempt 4

Attempt 4: get_index_valuation_csindex("000300")
   └─ CSIndex official data
   └─ On SUCCESS: return with note "数据不足"
   └─ On FAIL: return default {"percentile": 50}
```

**Good News:**
✓ Has 4-tier fallback chain
✓ Tushare is configured and used
✓ Falls back gracefully to default

**Bad News:**
- Attempts 1 & 3 both call `get_index_pe()` (inefficient)
- CSIndex data is minimal (not suitable as primary)
- Weighted PE (Tushare) != equal-weight PE (理杏仁/ETF.run)

---

#### 2.3 get_fund_nav()

**Function:** `get_fund_nav(code)`

**Current Flow (2-tier FALLBACK):**
```
Attempt 1: get_fund_nav_history(code)
   └─ AKShare get_fund_nav()
   └─ On SUCCESS: extract latest & prev NAV
   └─ On FAIL: try Attempt 2

Attempt 2: is_configured() && ts_nav(code, days=5)
   └─ Tushare fund NAV (需要 5000 积分)
   └─ On SUCCESS: extract NAV + calculate change
   └─ On FAIL: return {"nav": "N/A"}
```

**Good News:**
✓ Has Tushare fallback
✓ Prints informative logs ("Tushare 降级成功")

**Bad News:**
- Tushare requires high-point API (5000积分)
- Returns "N/A" on total failure (not empty list or error)

---

### 3. news_data.py — ⚠️ CRITICAL (Silent Failures)

**Functions:** `get_fund_news()`, `get_market_news()`, `get_policy_news()`, `get_stock_news_by_code()`

**Current Flow:**
```
get_fund_news(code)
  └─ keyword_map[code] → "基金" or specific keyword
  └─ get_stock_news(keyword)
     └─ infra/data_source/macro/indicators.py
        └─ ak.stock_news_em(symbol=keyword)  ← AKSHARE ONLY
        └─ EXCEPT: returns empty list []
  └─ On empty: returns [{"title": "市场动态获取中...", "source": "系统"}]
```

**Failure Behavior:**
- Returns fake/placeholder news ("获取中..." = "loading...")
- Silent failure: consumers don't know data is unavailable
- Misleading UI: shows "loading" but never updates

**Single Points:**
1. `get_stock_news()` — NO fallback
   - `stock_news_em()` only, no Tushare alternative
   
2. `get_market_news()` — NO fallback
   - Tries "A股" then "财经" keyword on same API
   - If both fail: returns placeholder

3. `get_policy_news()` — NO fallback
   - Same as above: only AKShare with keyword filtering

4. `get_stock_news_by_code()` — NO fallback
   - Direct `_get_stock_news()` call, returns `[]` on failure

5. `get_fund_news()` — Special case
   - Only calls AKShare for keyword (hardcoded mapping)
   - Gold code special path also AKShare-only

**Missing Fallback:**
```python
# Tushare has:
# - pro.news() but with different format
# - pro.major_news() for headline news
# BaoStock has no news API
# Mootdx has no news API
```

---

### 4. global_market.py — ⚠️ CRITICAL (All Pure AKShare)

#### 4.1 get_us_indices()

**Function:** `get_us_indices()`

**Current Flow:**
```
for key, symbol in [("dji", ".DJI"), ("spx", ".INX"), ("ixic", ".IXIC")]:
  └─ get_us_index(symbol)
     └─ infra/data_source/macro/indicators.py
        └─ ak.us_index_daily()  ← AKSHARE ONLY
        └─ EXCEPT: prints error, continues to next
```

**Failure Behavior:**
- Returns: `{"dji": None, "spx": None, "ixic": None, "available": False}`
- NO fallback

**Failure Impact:**
- `get_global_snapshot()` skips US indices section
- `analyze_global_impact_on_a_shares()` has incomplete data

---

#### 4.2 get_forex_data()

**Function:** `get_forex_data()`

**Current Flow:**
```
get_forex_data()
  └─ get_fx_spot_quote()
     └─ ak.fx_spot_quote()  ← AKSHARE ONLY
     └─ EXCEPT: returns None, prints error
```

**Failure Behavior:**
- Returns: `{"usdcny": None, "dxy_proxy": None, "available": False}`
- NO fallback

**Missing:** No Tushare forex API being used

---

#### 4.3 get_fed_rate()

**Function:** `get_fed_rate()`

**Current Flow:**
```
get_fed_rate()
  └─ get_usa_interest_rate()
     └─ ak.us_interest_rate_hist()  ← AKSHARE ONLY
     └─ EXCEPT: returns None, prints error
```

**Failure Behavior:**
- Returns: `{"current_rate": None, "trend": "hold", "available": False}`
- NO fallback

---

#### 4.4 get_global_pe()

**Function:** `get_global_pe()`

**Current Flow (Has intra-service fallback):**
```
US PE:
  └─ get_global_market_pe("美国")
     └─ ak.global_market_pe()  ← AKSHARE ONLY

CN PE:
  └─ get_global_market_pe("中国")
     └─ ak.global_market_pe()  ← AKSHARE ONLY

If US_PE == CN_PE (sanity check fails):
  └─ Fallback to get_valuation_percentile()
     └─ Uses沪深300 PE from market_data.py
     └─ Returns: {"cn_pe": 23.15, "us_pe": None, "notice": "..."}
```

**Good News:**
✓ Has sanity check for data quality
✓ Calls alternative `get_valuation_percentile()` on failure

**Bad News:**
- US PE has no fallback
- Only CN PE has secondary source (沪深300)

---

### 5. stock_price_provider.py — Better Design

**Function:** `get_daily_df(code, days, adjust)`

**Current Flow (2-tier FALLBACK):**
```
Check Cache:
  └─ If found: return copy

Attempt 1: is_configured() && _from_tushare(code, days)
   └─ get_daily_price(code, days)  ← Tushare pro_bar/daily
   └─ On SUCCESS: return normalized DataFrame
   └─ On FAIL: continue to Attempt 2

Attempt 2: _from_akshare(code, days, adjust)
   └─ get_stock_daily_hist(code, ..., adjust)
     └─ infra/data_source/market/stocks.py
        └─ ak.stock_zh_a_hist()  ← AKSHARE
        └─ On FAIL: try mootdx (see below)
   └─ On SUCCESS: return DataFrame
   └─ On FAIL: return empty DataFrame
```

**Good News:**
✓ Tushare is primary (more stable)
✓ AKShare is fallback
✓ Has decent caching

**But:** The infrastructure layer (`infra/data_source/market/stocks.py`) has its own fallback to mootdx, which is opaque to this layer.

---

## Infrastructure Layer Analysis

### infra/data_source/market/stocks.py

**Function:** `get_stock_daily_hist(code, ...)`

**Current Flow (2-tier FALLBACK):**
```
Attempt 1: ak.stock_zh_a_hist(symbol=code, ...)
   └─ AKSHARE (东财 HTTP) 
   └─ On SUCCESS: return df
   └─ On FAIL: continue to Attempt 2

Attempt 2: get_daily_hist_mootdx(code, days)
   └─ mootdx (通达信 TCP, 不封 IP)
   └─ On SUCCESS: print "[已降级至 mootdx]"
   └─ On FAIL: return None
```

**Good News:**
✓ Has mootdx fallback (smart choice for resilience)
✓ Uses TCP instead of HTTP (avoids IP bans)

**Problem:** The issue mentioned by night_worker!
```
get_stock_daily_hist() got an unexpected keyword argument 'symbol'
```

Looking at line 65 in stock_price_provider.py:
```python
df = get_stock_daily_hist(symbol=code, period="daily", adjust=adjust)
```

But in infrastructure layer (line 50):
```python
kwargs: Dict[str, Any] = {"symbol": code, "period": period, "adjust": adjust}
df = ak.stock_zh_a_hist(**kwargs)
```

**Root Cause:** AKShare parameter is `symbol`, but the infrastructure function signature doesn't list it in the docstring. This is likely a documentation/API mismatch issue, not a real problem, but confusing.

---

### infra/data_source/macro/indicators.py

**Functions:** `get_stock_news()`, `get_us_index()`, `get_fx_spot_quote()`, `get_usa_interest_rate()`, etc.

**Common Pattern:**
```python
def get_XXX(...) -> Any:
    """Get ... (akshare YYY_ZZZ).
    
    Returns:
        DataFrame or None on failure.
    """
    try:
        import akshare as ak
        return ak.yyy_zzz(...)
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_XXX failed: {e}")
        return None
```

**All are AKShare-only with NO fallback.**

**These are used by:**
- `market_data.py` — `get_fear_greed_index()` uses `get_index_daily()`
- `global_market.py` — uses `get_us_index()`, `get_fx_spot_quote()`, `get_usa_interest_rate()`, `get_global_market_pe()`
- `news_data.py` — uses `get_stock_news()`

---

### infra/data_source/alt/flows.py

**Functions:** `get_industry_board_summary()`, `get_hsgt_hist()`, `get_margin_sse()`, etc.

**All are AKShare-only with NO fallback.**

These are used by:
- `sector_rotation.py` — uses `get_industry_board_summary()`
- (other services use others)

---

## Problem Summary

### By Risk Level

**🔴 CRITICAL (No fallback, returns error/empty/placeholder):**
1. `sector_rotation.py::get_sector_ranking()` — ❌ Tushare not tried
2. `global_market.py::get_us_indices()` — ❌ No other source
3. `global_market.py::get_forex_data()` — ❌ No other source
4. `global_market.py::get_fed_rate()` — ❌ No other source
5. `news_data.py::get_*_news()` — ❌ Returns fake "loading..." message

**🟡 MEDIUM (Has some fallback, but gaps):**
1. `market_data.py::get_fear_greed_index()` — ❌ No Tushare fallback for index data
2. `global_market.py::get_global_pe()` — ⚠️ US PE has no fallback
3. Infrastructure layer — Only has catch-all without trying alternates

**🟢 GOOD (Has working fallback):**
1. `market_data.py::get_valuation_percentile()` — ✓ 4-tier chain
2. `market_data.py::get_fund_nav()` — ✓ Tushare fallback
3. `stock_price_provider.py::get_daily_df()` — ✓ Tushare → AKShare chain

---

## Direct AKShare Calls (Bypassing Infrastructure)

### In market_data.py
**Line 50, 106, 202, 280, 310:**
```python
from infra.data_source.market.stocks import get_fund_nav_history as _get_fund_nav_hist
from infra.data_source.market.stocks import get_index_daily
from infra.data_source.market.stocks import get_index_pe, get_index_valuation_csindex
```

These all go through the infrastructure layer (good practice).

### In sector_rotation.py
**Line 74:**
```python
from infra.data_source.alt.flows import get_industry_board_summary
```

This also goes through infrastructure (good practice).

### In news_data.py
**Lines 47, 141, 191, 368:**
```python
from infra.data_source.macro.indicators import get_stock_news
from infra.data_source.macro.indicators import get_stock_news as _get_stock_news
```

These go through infrastructure (but infrastructure has no fallback).

### In global_market.py
**Lines 67, 116, 163, 252:**
```python
from infra.data_source.macro.indicators import get_us_index
from infra.data_source.macro.indicators import get_fx_spot_quote
from infra.data_source.macro.indicators import get_usa_interest_rate
from infra.data_source.macro.indicators import get_global_market_pe
```

All go through infrastructure (which only calls AKShare).

---

## Recommended Fallback Chains

### 1. Sector Rotation (HIGHEST PRIORITY)
```python
# Current: AKShare only
# Proposed:
1. AKShare stock_board_industry_summary_ths()  (current)
2. Tushare pro.index_classify() + daily data
3. BaoStock industry concept data (if available)
4. Mootdx sector classification
5. Return mock/cached data if all fail
```

### 2. Global Market Indices
```python
# US Indices
1. AKShare us_index_daily(".DJI", ".INX", ".IXIC")
2. Tushare ? (check if they have US index data)
3. Yahoo Finance via yfinance (external dependency)
4. Mock using previous day's data

# Forex (USDCNY)
1. AKShare fx_spot_quote()
2. Tushare ? (check forex API)
3. Bank of China API (external)
4. Use cached rate if available

# Fed Rate
1. AKShare us_interest_rate_hist()
2. Federal Reserve official API (external)
3. Tushare ? (unlikely to have US rates)
4. Use previous month's rate
```

### 3. News Data
```python
# Stock News
1. AKShare stock_news_em(symbol)
2. Tushare pro.major_news() (different format)
3. Return empty list (don't lie with "loading...")

# Market News
Same as above
```

### 4. Fear & Greed Index
```python
# Index Daily Data (for HS300)
1. AKShare stock_zh_index_daily("sh000300")
2. Tushare pro.index_daily(ts_code="399300.SZ")
3. mootdx daily("000300")
4. Return default neutral score
```

---

## Current Status Table

| Service | Function | Primary | Fallback1 | Fallback2 | Fallback3 | Rating |
|---------|----------|---------|-----------|-----------|-----------|--------|
| market_data | get_fund_nav | AKShare | Tushare | — | — | ✓ Good |
| market_data | get_fear_greed_index | AKShare | None | — | — | ❌ Bad |
| market_data | get_valuation_percentile | AKShare | Tushare | AKShare(alt) | CSIndex | ✓ Good |
| sector_rotation | get_sector_ranking | AKShare | None | — | — | ❌ Critical |
| news_data | get_fund_news | AKShare | None | — | — | ❌ Bad |
| news_data | get_market_news | AKShare | None | — | — | ❌ Bad |
| news_data | get_policy_news | AKShare | None | — | — | ❌ Bad |
| global_market | get_us_indices | AKShare | None | — | — | ❌ Critical |
| global_market | get_forex_data | AKShare | None | — | — | ❌ Critical |
| global_market | get_fed_rate | AKShare | None | — | — | ❌ Critical |
| global_market | get_global_pe | AKShare | Valuation%(CN) | — | — | 🟡 Medium |
| stock_price_provider | get_daily_df | Tushare | AKShare | mootdx | — | ✓ Good |

---

## Next Steps

1. **Short-term (Week 1):** Add Tushare fallback to `get_fear_greed_index()`
2. **Medium-term (Week 2):** Implement sector rotation fallback chain
3. **Long-term (Week 3+):** Global market data fallback chains (requires external APIs)

