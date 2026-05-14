# MoneyBag Data Source Fallback/Degradation Architecture - Comprehensive Analysis

**Date**: 2026-05-14  
**Status**: Deep exploration of 5-bucket data source taxonomy + fallback patterns  
**Analyst**: Claude (Code)

---

## Executive Summary

The MoneyBag project has a **designed but incomplete** multi-source fallback architecture. The system is organized into 5 data buckets, but the `fallback.py` is just a stub. **Only 1 data type (market K-lines) has a working 2-level fallback chain**, while 4 other buckets have **critical single-point-of-failure dependencies on AKShare**.

### Quick Status
| Bucket | Primary | Fallback 1 | Fallback 2 | Status |
|--------|---------|-----------|-----------|--------|
| **market** | AKShare | mootdx | - | ✅ **WORKING** (2 levels) |
| **macro** | AKShare | Tushare | - | ⚠️ Partial (1 function) |
| **fundamental** | AKShare | - | - | ❌ **SINGLE POINT OF FAILURE** |
| **alt** | AKShare | - | - | ❌ **SINGLE POINT OF FAILURE** |
| **synthetic** | N/A | N/A | N/A | - |

---

## 1. PROVIDER INTERFACE & PROTOCOL

### Provider Class Structure
All 5 providers follow the same **structural subtyping pattern** (not enforced, but consistent):

```python
# Common interface (duck-typed, not enforced)
class DataSourceProvider:
    def __init__(self): ...
    def fetch(metric: str, **params) -> Dict|List|None: ...
    def is_available() -> bool: ...
    @property
    def provider_name() -> str: ...
```

### Current Providers

#### 1. **AkshareProvider** (providers/akshare_provider.py)
- **Status**: STUB (method bodies return None)
- **Supported Metrics**: 14 metrics
  - Macro: `macro_gdp`, `macro_cpi`, `macro_pmi`, `macro_shibor`, `macro_lpr`, `macro_m1_m2`
  - Alt: `stock_news`, `northbound_flow`, `margin_detail`, `block_trade`
  - Fundamental: `fund_name`, `fund_rank`
- **Why It's Primary**:
  - Free, no API key required
  - Widest macro data coverage
  - Sole source for alt data (northbound flows, margin)
- **Risk**: Scraper-based, breaks on upstream website changes

#### 2. **TushareProvider** (providers/tushare_provider.py)
- **Status**: STUB (method bodies return None)
- **Supported Metrics**: 7 metrics
  - Market: `stock_price`, `index_daily`, `fund_nav`
  - Fundamental: `income_statement`, `balance_sheet`, `valuation`, `dividend`
- **Requirement**: `TUSHARE_TOKEN` environment variable
- **Cost**: 5000 积分 (credit points) — stable, structured, fast
- **Real Implementation**: YES! ✅ See `/services/tushare_data.py` (fully implemented wrapper)

#### 3. **BaostockProvider** (providers/baostock_provider.py)
- **Status**: STUB (method bodies return None)
- **Supported Metrics**: 3 metrics
  - Market: `stock_price`, `index_daily`
  - Fundamental: `stock_industry`
- **Why It's Tertiary**:
  - Free, no API key
  - Most stable historical K-line data
  - Baostock requires login/logout session management
- **Issue**: Adapter is completely unimplemented, but the underlying baostock library is solid

#### 4. **TencentProvider** (providers/tencent_provider.py)
- **Status**: FULLY IMPLEMENTED** ✅
- **Provides**: Real-time quotes (PE/PB/market cap)
- **URL**: `http://qt.gtimg.cn/q={sh/sz}{code}`
- **Encoding**: GBK (legacy Chinese)
- **Cost**: Free, no key, HTTP GET
- **Purpose**: Fallback PE/PB when Tushare PE unavailable
- **Cache**: 300 seconds (5 minutes)

#### 5. **MootdxProvider** (providers/mootdx_provider.py)
- **Status**: FULLY IMPLEMENTED** ✅
- **Provides**:
  - K-line data (daily bars, 90-day default)
  - Financial data (EPS, ROE, net profit, revenue, gross margin)
- **Protocol**: TCP binary (通达信 protocol, not HTTP)
- **Advantages**: 
  - Doesn't go through Eastmoney (no IP blocking)
  - Not rate-limited
- **Cache**: 3600s for K-lines, 86400s for financials
- **Purpose**: Already in use as 2nd fallback for K-lines

---

## 2. FIVE-BUCKET DATA SOURCE TAXONOMY

Reference: `docs/design/12-framework-refactor.md` (§6)

### Bucket 1: **MARKET** (market/stocks.py)
**Data**: Stock prices, K-lines, indices, fund NAV, futures, ETF

#### Functions & Their Fallback Status
| Function | Data Type | Primary | Fallback 1 | Fallback 2 | Status |
|----------|-----------|---------|-----------|-----------|--------|
| `get_stock_daily_hist()` | K-line | AKShare | mootdx | - | ✅ **2-level** |
| `get_stock_realtime_quotes_em()` | Real-time | AKShare | - | - | ❌ None |
| `get_stock_realtime_quotes()` | Real-time (legacy) | AKShare | - | - | ❌ None |
| `get_stock_spot_xq()` | Real-time (xueqiu) | AKShare | - | - | ❌ None |
| `get_stock_daily_legacy()` | K-line (legacy) | AKShare | - | - | ❌ None |
| `get_stock_code_name_list()` | Metadata | AKShare | - | - | ❌ None |
| `get_index_daily()` | Index daily | AKShare | - | - | ❌ None |
| `get_index_pe()` | Index PE | AKShare | - | - | ❌ None |
| `get_index_valuation_csindex()` | Index valuation | AKShare | - | - | ❌ None |
| `get_fund_nav_history()` | Fund NAV | AKShare | - | - | ❌ None |
| `get_fund_name_list()` | Fund metadata | AKShare | - | - | ❌ None |
| `get_fund_estimated_nav()` | Fund est. NAV | AKShare | - | - | ❌ None |
| `get_fund_rank()` | Fund ranking | AKShare | - | - | ❌ None |
| `get_etf_fund_daily()` | ETF daily | AKShare | - | - | ❌ None |
| `get_futures_main()` | Futures | AKShare | - | - | ❌ None |
| `get_futures_foreign_hist()` | Foreign futures | AKShare | - | - | ❌ None |
| `get_restricted_release_summary()` | Alt data | AKShare | - | - | ❌ None |

**CRITICAL FINDING**: Only 1 function has true 2-level fallback:
```python
# get_stock_daily_hist (line 23-80 in market/stocks.py)
# 降级 1: AKShare（东财 HTTP）
# 降级 2: mootdx（通达信 TCP）
```

### Bucket 2: **FUNDAMENTAL** (fundamental/financials.py)
**Data**: Financial indicators, valuations, fund holdings
**All functions call AKShare directly — NO fallback**

| Function | Data Type | Primary | Status |
|----------|-----------|---------|--------|
| `get_financial_indicators()` | Financials | AKShare | ❌ No fallback |
| `get_stock_lg_indicator()` | Valuation | AKShare | ❌ No fallback |
| `get_fund_portfolio_holdings()` | Fund holdings | AKShare | ❌ No fallback |

### Bucket 3: **ALT** (alt/flows.py)
**Data**: Northbound flows, margin, interbank rates, fund flows
**All functions call AKShare directly — NO fallback**

| Function | Data Type | Primary | Status |
|----------|-----------|---------|--------|
| `get_hsgt_hist()` | Northbound flow | AKShare | ❌ No fallback |
| `get_hsgt_hold_stock()` | Northbound holdings | AKShare | ❌ No fallback |
| `get_margin_sse()` | Margin (SSE only) | AKShare | ❌ No fallback |
| `get_bond_zh_us_rate()` | Bond yield | AKShare | ❌ No fallback |
| `get_interbank_rate()` | SHIBOR (interbank) | AKShare | ❌ No fallback |
| `get_individual_fund_flow_rank()` | Fund flow ranking | AKShare | ❌ No fallback |

### Bucket 4: **MACRO** (macro/indicators.py)
**Data**: Chinese & global macroeconomic indicators
**Partial fallback (only M2 money supply has Tushare fallback)**

| Function | Data Type | Primary | Fallback | Status |
|----------|-----------|---------|----------|--------|
| `get_china_money_supply()` | M0/M1/M2 | AKShare | Tushare `cn_m` | ⚠️ **1 only** |
| `get_china_social_financing()` | Social financing | AKShare | - | ❌ None |
| `get_china_lpr()` | LPR rates | AKShare | - | ❌ None |
| All others | - | AKShare | - | ❌ None |

### Bucket 5: **SYNTHETIC** (synthetic/__init__.py)
**Status**: Appears to be empty or minimal (not examined in detail)

---

## 3. ACTUAL FALLBACK IMPLEMENTATIONS IN CODEBASE

### ✅ Working Fallbacks (Confirmed in Code)

#### A. **market/stocks.py::get_stock_daily_hist()** (2-level)
```python
# 降级 1: AKShare（东财 HTTP）
try:
    df = ak.stock_zh_a_hist(**kwargs)  # Works when up
except:
    
# 降级 2: mootdx（通达信 TCP）
try:
    from infra.data_source.providers.mootdx_provider import get_daily_hist_mootdx
    df = get_daily_hist_mootdx(code=code, days=days)
```
**Coverage**: This is THE ONLY function with true multi-level fallback.

#### B. **services/market_data.py::get_fund_nav()** (2-level)
```python
# 降级 1: AKShare
try:
    df = _get_fund_nav_hist(code=code)  # Calls market/stocks.get_fund_nav_history()
    
# 降级 2: Tushare (2026-04-19 新增)
try:
    from services.tushare_data import is_configured, get_fund_nav as ts_nav
    if is_configured():
        ts = ts_nav(code, days=5)  # 5000 积分基金净值接口
```
**Coverage**: Fund NAV only.

#### C. **services/market_data.py::get_valuation_percentile()** (3-level)
```python
# 方案1: 乐咕"滚动市盈率中位数"（优先）
try:
    df = get_index_pe(symbol="沪深300")  # AKShare

# 方案2降级: Tushare 加权PE
try:
    pro = ts_api.pro_api()
    df = pro.index_dailybasic(ts_code="399300.SZ")  # 标准 Tushare

# 降级: 乐咕 stock_index_pe_lg（口径可能偏差）
try:
    df = get_index_pe(symbol="沪深300")  # AKShare again

# 降级: 中证官方 stock_zh_index_value_csindex
try:
    df = get_index_valuation_csindex(symbol="000300")  # AKShare
```
**Issue**: 3 methods, but 2 of them are AKShare (same source).

#### D. **services/factor_data.py** (Multi-level across functions)
Well-implemented fallback pattern for:
- `get_northbound_flow()`: Tushare → AKShare
- `get_margin_trading()`: Tushare → AKShare
- `get_shibor()`: Tushare → AKShare

Example (get_northbound_flow):
```python
# ── 方案 A（主）：Tushare moneyflow_hsgt ──
try:
    from services.tushare_data import is_configured, get_northbound_flow as ts_north
    if is_configured():
        ts_data = ts_north(days=30)

# ── 方案 B（降级）：AKShare stock_hsgt_hist_em ──
try:
    from infra.data_source.alt.flows import get_hsgt_hist
```

#### E. **services/tushare_data.py** (Fully implemented)
This service provides a **stable wrapper** for Tushare API:
- `get_daily_price()` — Stock daily bars
- `get_northbound_flow()` — Tushare moneyflow_hsgt (replaces AKShare)
- `get_shibor_rate()` — Tushare shibor (replaces AKShare rate_interbank)
- `get_margin_data()` — Tushare margin (full market coverage vs AKShare SSE only)
- `get_fund_nav()` — Tushare fund_nav (5000 积分)
- `get_financials()` — Tushare fina_indicator

#### F. **services/stock_price_provider.py** (New unified provider)
2026-04-19 addition for unified stock price data:
```python
def get_daily_df(code: str, days: int = 120):
    # 主源: Tushare pro_bar
    if is_configured():
        df = _from_tushare(code, days)
    
    # 降级: AKShare
    df = _from_akshare(code, days)
```

---

## 4. SINGLE POINTS OF FAILURE

### 🔴 Critical: No Fallback (Data Loss Risk)

#### MARKET BUCKET (9/16 functions)
1. `get_stock_realtime_quotes_em()` - AKShare only ❌
2. `get_stock_realtime_quotes()` - AKShare only ❌
3. `get_stock_spot_xq()` - AKShare only ❌
4. `get_stock_daily_legacy()` - AKShare only ❌
5. `get_stock_code_name_list()` - AKShare only ❌
6. `get_index_daily()` - AKShare only ❌
7. `get_index_pe()` - AKShare only ❌
8. `get_index_valuation_csindex()` - AKShare only ❌
9. `get_fund_nav_history()` - AKShare only ❌
10. `get_fund_name_list()` - AKShare only ❌
11. `get_fund_estimated_nav()` - AKShare only ❌
12. `get_fund_rank()` - AKShare only ❌
13. `get_etf_fund_daily()` - AKShare only ❌
14. `get_futures_main()` - AKShare only ❌
15. `get_futures_foreign_hist()` - AKShare only ❌

#### FUNDAMENTAL BUCKET (All 3 functions)
1. `get_financial_indicators()` - AKShare only ❌
2. `get_stock_lg_indicator()` - AKShare only ❌
3. `get_fund_portfolio_holdings()` - AKShare only ❌

#### ALT BUCKET (All 6 functions)
1. `get_hsgt_hist()` - AKShare only ❌ (data断层已知 2024-08)
2. `get_hsgt_hold_stock()` - AKShare only ❌
3. `get_margin_sse()` - AKShare only ❌ (Shanghai only, 60% market coverage)
4. `get_bond_zh_us_rate()` - AKShare only ❌
5. `get_interbank_rate()` - AKShare only ❌
6. `get_individual_fund_flow_rank()` - AKShare only ❌

#### MACRO BUCKET (Majority of functions)
Only `get_china_money_supply()` has Tushare fallback. All others AKShare-only.

### What Calls These Unsafe Functions?

**Services that depend on single-point-of-failure functions:**

1. **services/backtest_engine.py** - Calls `get_stock_daily_hist()` (OK - has fallback) but also legacy paths
2. **services/factor_data.py** - Calls `get_stock_lg_indicator()` (❌ no fallback)
3. **services/market_data.py** - Calls `get_index_pe()`, `get_index_daily()`, `get_fund_nav_history()` (❌ all no fallback)
4. **services/news_data.py** - Unknown, likely uses alt bucket
5. **services/broker_research.py** - Uses market bucket
6. **services/genetic_factor.py** - Uses market bucket

---

## 5. DATA TYPE MAPPING BY PROVIDER

### AKShare (Primary — Most Providers)
**Coverage**: Market, Fundamental, Alt, Macro
**Strengths**: 
- Widest data variety
- Free
- Real-time data scraping

**Weaknesses**:
- Scraper-based (fragile)
- Rate-limited, IP blocking
- Maintenance overhead on upstream changes
- 2024-08-16 northbound flow data断层 (known issue)
- Can be unstable during off-market hours

### Tushare (Paid — 5000 积分)
**Coverage**: Market (stocks, indices, funds), Fundamental (financials), Macro (limited)
**Real Implementations**:
- `get_daily_price()` - Stock daily bars ✅
- `get_northbound_flow()` - HSGT flows ✅
- `get_shibor_rate()` - SHIBOR rates ✅
- `get_margin_data()` - Margin data (full market) ✅
- `get_fund_nav()` - Fund NAV ✅
- `get_financials()` - Financial indicators ✅
- `get_valuation()` - Single stock valuation ✅
- `get_valuation_batch()` - Bulk valuation ✅

### Baostock (Free — Tertiary)
**Status**: Provider stub, but underlying library is battle-tested
**Coverage**: Stock prices, indices, industry data
**Strengths**:
- Free
- Session-based (stable connection)
- Good historical data
- Python wrapper mature
- No IP blocking

**Missing**: Implementation in BaostockProvider class

### Mootdx (Free — TCP-based)
**Status**: Fully implemented ✅
**Coverage**: K-lines, financial data
**Uses**: Already in get_stock_daily_hist fallback chain
**Strengths**:
- TCP protocol (不走东财，不封 IP)
- Free
- Fast
- Good quality data
- 24-hour access (not just trading hours)

### Tencent (Free — HTTP real-time)
**Status**: Fully implemented ✅
**Coverage**: Real-time quotes, PE, PB, market cap
**Uses**: Fallback for PE/PB when Tushare unavailable
**Cost**: Free, GBK encoding, 5min cache

---

## 6. CURRENT FALLBACK PATTERNS IN CODE

### Pattern 1: Linear Chain (Preferred)
```python
# Try source A
try:
    result = source_a()
    if result and len(result) > 0:
        return result
except:
    pass

# Fallback to source B
try:
    result = source_b()
    if result and len(result) > 0:
        return result
except:
    pass

# Fallback to source C
try:
    result = source_c()
except:
    pass

return None
```
**Used in**: `market/stocks.py::get_stock_daily_hist()`, `factor_data.py` functions

### Pattern 2: Conditional Check
```python
# Tushare only if configured
from services.tushare_data import is_configured
if is_configured():
    try:
        result = tushare_api()
        if result.get("available"):
            return result
    except:
        pass

# Fall back to AKShare
try:
    result = akshare_api()
except:
    pass
```
**Used in**: `factor_data.py`, `market_data.py`

### Pattern 3: No Fallback (Anti-pattern)
```python
# Direct call, no exception handling
import akshare as ak
return ak.some_function()
```
**Used in**: 14/16 market functions, all fundamental functions, all alt functions

---

## 7. IMPLEMENTATION STATUS OF PROVIDERS

### Summary Table
| Provider | Adapter Status | Real Implementation | Can Use? |
|----------|---|---|---|
| **AkshareProvider** | ❌ Stub | ✅ Yes (akshare lib) | Via direct imports |
| **TushareProvider** | ❌ Stub | ✅ Yes (via tushare_data.py) | Via services/tushare_data.py |
| **BaostockProvider** | ❌ Stub | ✅ Yes (baostock lib) | Need to implement adapter |
| **TencentProvider** | ✅ Full | ✅ Yes | `providers.tencent_provider.get_stock_quote_tencent()` |
| **MootdxProvider** | ✅ Full | ✅ Yes | `providers.mootdx_provider.get_daily_hist_mootdx()` |

### BaostockProvider: Is it Truly Empty?

**baostock_provider.py** is an adapter **stub**, but:
1. The underlying `baostock` library is fully functional
2. Login/logout is handled in `_login()`, `_logged_in` flag
3. Only the `fetch()` method is stubbed (returns None)

**Code sketch of what needs implementing**:
```python
def fetch(self, metric: str, **params):
    if metric == "stock_price":
        bs = self._get_bs()
        rs = bs.query_history_k_data_plus(
            code=params["symbol"],
            fields="date,open,high,low,close,volume",
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
        )
        return [row for row in rs.data]
```

---

## 8. DESIGN INTENT (from fallback.py header)

```python
"""
Planned implementation: M1 W4 (requires provider adapters).

Degradation order per category:
  - market:      Tushare > baostock > AKShare
  - fundamental: Tushare > AKShare
  - macro:       AKShare > Tushare
  - alt:         AKShare (sole source)
"""
```

### Analysis of Intent vs Reality

**Planned**:
- Market: 3-level (Tushare → baostock → AKShare)
- Fundamental: 2-level (Tushare → AKShare)
- Macro: 2-level (AKShare → Tushare)
- Alt: 1-level (AKShare only)

**Actual**:
- Market: 1.5-level (AKShare → mootdx) — Tushare & baostock not integrated
- Fundamental: 0-level (AKShare only) — Tushare fallback exists in code but not wired
- Macro: 0.2-level (AKShare only, except M2) — Mostly missing
- Alt: 0-level (AKShare only) — No fallback

**Explanation for Discrepancy**:
The design document was written with intent, but actual implementation uses:
1. **Direct service integration** (services/tushare_data.py) instead of provider adapters
2. **Ad-hoc fallback patterns** in each function instead of unified framework
3. **Partial implementations** (some functions have fallbacks, others don't)

---

## 9. PROVIDER COVERAGE MATRIX

Which providers supply which data types?

```
                        MARKET  FUNDAMENTAL  MACRO   ALT     
AKShare                 ✅      ✅           ✅      ✅(SOLE)
Tushare                 ✅      ✅           ⚠️(M2)  ❌      
Baostock                ✅      ❌           ❌      ❌      
Mootdx                  ✅      ⚠️(Finance)  ❌      ❌      
Tencent                 ⚠️(RT)  ❌           ❌      ❌      
```

### Critical Gaps

1. **No source for Alt data except AKShare**
   - Northbound flows (data gap known 2024-08-16)
   - Margin data (only Tushare has full coverage, not AKShare)
   - Interbank rates / SHIBOR (Tushare better, not wired)

2. **Macro data heavily skewed to AKShare**
   - LPR rates: AKShare only
   - Social financing: AKShare only
   - M2 money supply: Has Tushare fallback (1 function)

3. **Fundamental data missing Baostock**
   - BaostockProvider designed but not implemented
   - Could provide sector data, PE/PB analysis

---

## 10. ROOT CAUSE ANALYSIS

### Why is Fallback Incomplete?

1. **Architecture Mismatch**: 
   - Designed: Provider adapter pattern (domain/protocols/DataSourceProtocol)
   - Actual: Service layer pattern (services/tushare_data.py)
   - Result: Providers are stubs, actual fallback logic is ad-hoc in services

2. **Partial Migration**:
   - `services/tushare_data.py` was created to provide Tushare wrapper
   - But it wasn't integrated into the 5-bucket data_source layer
   - So fallback logic appears both in data_source (old) and services (new)

3. **Priority Mismatch**:
   - Design says: "Tushare > baostock > AKShare" for market
   - Reality: "AKShare > mootdx" (because mootdx was available first)
   - Reason: Mootdx was already used for fallback, Tushare wrapper added later

4. **Time Constraint**:
   - BaostockProvider designed but not implemented
   - Alt data sources missing (no alternative sources identified)
   - No unified fallback framework implemented

---

## 11. WHAT WORKS TODAY

### ✅ Production-Ready Fallbacks

1. **Stock K-lines** (`market/stocks.py::get_stock_daily_hist`)
   - Primary: AKShare stock_zh_a_hist
   - Fallback: mootdx (通达信 TCP)
   - Status: Tested, used in backtest_engine.py

2. **Factor Data** (`services/factor_data.py`)
   - Northbound: Tushare → AKShare
   - SHIBOR: Tushare → AKShare
   - Margin: Tushare → AKShare
   - Treasury yields: AKShare only
   - Status: Multiple functions with working fallback

3. **Fund NAV** (`services/market_data.py::get_fund_nav`)
   - Primary: AKShare
   - Fallback: Tushare (5000 积分)
   - Status: 2026-04-19 addition, tested

4. **Valuation Percentile** (`services/market_data.py::get_valuation_percentile`)
   - Multiple sources but some redundant (AKShare used twice)
   - Status: Complex but functional

### ⚠️ Partial Fallbacks

1. **Macro Indicators** (`macro/indicators.py::get_china_money_supply`)
   - Only M2 has Tushare fallback
   - Others are AKShare-only

---

## 12. RECOMMENDATIONS FOR IMPLEMENTATION

### Priority 1: Alt Data Bucket (Critical)
**Problem**: Northbound flows data断层 since 2024-08-16
**Solution**: 
- Wire Tushare moneyflow_hsgt into `alt/flows.py`
- Already implemented in `services/tushare_data.py::get_northbound_flow()`
- Margin data: Tushare provides full market (沪+深+北), AKShare only Shanghai (60%)

### Priority 2: Fundamental Bucket
**Problem**: No Tushare fallback despite implementation existing
**Solution**:
- Update `fundamental/financials.py` to call `services/tushare_data.py` first
- Pattern: Check `is_configured()`, try Tushare, fallback AKShare
- Implement `get_financial_indicators()` → `tushare_data.get_financials()`

### Priority 3: Macro Bucket
**Problem**: Only 1 function has fallback
**Solution**:
- Extend macro indicators with Tushare fallbacks
- LPR rates, social financing available in Tushare
- Create wrapper functions in `services/tushare_data.py`

### Priority 4: Market Bucket Optimization
**Problem**: Index data and fund data have no fallback
**Solution**:
- Index PE: Add Tushare index_dailybasic as fallback
- Fund data: Tushare has fund_nav, fund_basic APIs
- Real-time quotes: Consider Tencent, mootdx, or other real-time sources

### Priority 5: Implement BaostockProvider
**Problem**: Provider stub, underlying library solid
**Solution**:
- Implement `BaostockProvider.fetch()` per metric type
- Use for tertiary fallback on stock prices
- Handle login/logout correctly

---

## 13. IMPLEMENTATION CHECKLIST

### Phase 1: Immediate (Alt Data Crisis)
- [ ] Move `get_northbound_flow()` from `factor_data.py` to `alt/flows.py`
- [ ] Implement Tushare path in `alt/flows.py::get_northbound_flow()`
- [ ] Implement Tushare path in `alt/flows.py::get_margin_sse()`
- [ ] Test northbound flow data availability (2024-08 gap resolution)

### Phase 2: Fundamental Data
- [ ] Add Tushare fallback to `fundamental/financials.py::get_financial_indicators()`
- [ ] Add Tushare fallback to `fundamental/financials.py::get_stock_lg_indicator()`
- [ ] Test financial data fallback with Tushare token disabled

### Phase 3: Macro Data
- [ ] Implement remaining macro indicators in `services/tushare_data.py`
- [ ] Add fallback paths to all macro functions in `macro/indicators.py`
- [ ] Test M2, LPR, social financing with AKShare down

### Phase 4: Provider Adapters
- [ ] Complete `BaostockProvider.fetch()` implementation
- [ ] Integrate into market/stocks.py fallback chain
- [ ] Test 3-level fallback (Tushare → baostock → mootdx)

### Phase 5: Unified Framework
- [ ] Create `fallback.py` with actual implementation (not stub)
- [ ] Define priority chains per data category
- [ ] Create metric dispatcher logic
- [ ] Add comprehensive testing

---

## APPENDIX: CALL GRAPH

### Services that depend on market/stocks.py functions:

**get_stock_daily_hist** (HAS FALLBACK ✅):
- services/backtest_engine.py
- services/stock_price_provider.py (unified entry point)

**get_fund_nav_history** (NO FALLBACK ❌):
- services/market_data.py::get_fund_nav() — but wraps with Tushare fallback

**get_index_daily** (NO FALLBACK ❌):
- services/market_data.py::get_fear_greed_index()

**get_index_pe** (NO FALLBACK ❌):
- services/market_data.py::get_valuation_percentile()
- services/factor_data.py::get_dividend_yield()

**get_stock_lg_indicator** (NO FALLBACK ❌):
- services/factor_data.py::get_dividend_yield()
- services/factor_data.py::get_stock_financials()

### Services that depend on factor_data.py:
- All pipeline runners
- Dashboard/reporting endpoints
- Holdings analysis

---

**End of Analysis**
