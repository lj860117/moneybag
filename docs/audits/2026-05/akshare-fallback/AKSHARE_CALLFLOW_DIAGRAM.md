# MoneyBag AKShare Call Flow Diagram
## Detailed Service → Infrastructure → Data Source Mapping

---

## 1. Sector Rotation (CRITICAL ❌)

```
sector_rotation.py
  └─ get_sector_ranking()
     ├─ cache check
     └─ get_industry_board_summary()
        └─ infra/data_source/alt/flows.py
           └─ get_industry_board_summary()
              └─ try:
                 └─ ak.stock_board_industry_summary_ths()  ← AKSHARE ONLY
                 └─ except: print log, return None
              └─ on failure: {"available": False, "error": "行业数据不足"}

RISK: 🔴 CRITICAL
  - Single source, zero fallback
  - Returns error, not data
  - Service used by Pipeline enrich()
```

**Proposed Fix:**
```
get_industry_board_summary()
  ├─ Try: ak.stock_board_industry_summary_ths()
  ├─ Fail → Try: Tushare pro.index_classify()
  ├─ Fail → Try: BaoStock get_industry_board()
  └─ Fail → Return empty DataFrame with note
```

---

## 2. Market Data - Fear & Greed Index (MEDIUM ❌)

```
market_data.py
  └─ get_fear_greed_index()
     ├─ cache check
     └─ try:
        ├─ get_index_daily("sh000300")
        │  └─ infra/data_source/market/stocks.py
        │     └─ get_index_daily(symbol)
        │        └─ try:
        │           └─ ak.stock_zh_index_daily(symbol)  ← AKSHARE ONLY
        │           └─ except: print log, return None
        ├─ calculate 3-dimension score from data
        ├─ except: return {"score": 50, "level": "中性"}  ← ONLY DEFAULT
        └─ return full result

RISK: 🟡 MEDIUM
  - No Tushare fallback for index daily
  - Returns neutral default when AKShare fails
  - Better: should try Tushare index_daily()
```

**Proposed Fix:**
```
infra/data_source/market/stocks.py::get_index_daily()
  ├─ Try: ak.stock_zh_index_daily(symbol)
  ├─ Fail + symbol=="sh000300" → Try: 
  │   Tushare pro.index_daily(ts_code="399300.SZ")
  ├─ Fail → Try: mootdx daily("000300")
  └─ Fail → Return None
```

---

## 3. Market Data - Valuation Percentile (GOOD ✓)

```
market_data.py
  └─ get_valuation_percentile()
     ├─ Attempt 1: get_index_pe("沪深300")
     │  └─ AKShare stock_index_pe_lg()
     │     └─ Use "滚动市盈率中位数" column
     │     └─ On success: return percentile ✓
     │     └─ On fail: continue
     │
     ├─ Attempt 2: is_configured() && Tushare
     │  └─ pro.index_dailybasic("399300.SZ", fields="pe_ttm")
     │     └─ On success: return percentile ✓ (with ⚠️ weighted PE note)
     │     └─ On fail: continue
     │
     ├─ Attempt 3: get_index_pe("沪深300") again
     │  └─ AKShare stock_index_pe_lg()
     │     └─ Use "滚动市盈率" column (fallback)
     │     └─ On success: return percentile ✓
     │     └─ On fail: continue
     │
     └─ Attempt 4: get_index_valuation_csindex("000300")
        └─ AKShare stock_zh_index_value_csindex()
           └─ On success: return percentile ✓ (limited data)
           └─ On fail: return default {"percentile": 50}

RISK: ✅ GOOD
  - 4-tier fallback chain
  - Tushare included
  - Graceful default
  - But: inefficient (calls get_index_pe twice)
```

---

## 4. Market Data - Fund NAV (GOOD ✓)

```
market_data.py
  └─ get_fund_nav(code)
     ├─ cache check
     ├─ Attempt 1: get_fund_nav_history(code)
     │  └─ infra/data_source/market/stocks.py
     │     └─ try: ak.get_fund_nav()
     │     └─ except: return None
     │  └─ extract latest + prev NAV
     │  └─ On success: return nav dict ✓
     │  └─ On fail: continue
     │
     └─ Attempt 2: is_configured() && ts_nav(code, days=5)
        └─ Tushare fund NAV (5000 points)
           └─ extract latest + prev NAV
           └─ On success: return nav dict ✓ (log "Tushare 降级成功")
           └─ On fail: return {"nav": "N/A"}

RISK: ✅ GOOD
  - Tushare fallback present
  - Good logging
```

---

## 5. News Data (CRITICAL ❌)

```
news_data.py
  ├─ get_fund_news(code)
  │  ├─ keyword_map[code] → keyword
  │  ├─ get_stock_news(keyword)  [via infra layer]
  │  └─ if empty: return [{"title": "市场动态获取中...", "source": "系统"}]  ❌ FAKE DATA
  │
  ├─ get_market_news(limit=30)
  │  ├─ cache check
  │  ├─ Try "A股": get_stock_news("A股")
  │  ├─ Try "财经": get_stock_news("财经")
  │  └─ if all empty: return [{"title": "市场资讯加载中...", "source": "系统"}]  ❌ FAKE DATA
  │
  ├─ get_policy_news(limit=20)
  │  ├─ cache check
  │  ├─ Try "财经" + keyword filter
  │  ├─ Try "A股" + keyword filter
  │  └─ if all empty: return [{"title": "政策资讯加载中...", "source": "系统"}]  ❌ FAKE DATA
  │
  └─ get_stock_news_by_code(code)
     └─ get_stock_news(symbol=code)
        └─ infra/data_source/macro/indicators.py
           └─ get_stock_news()
              └─ try: ak.stock_news_em(symbol)  ← AKSHARE ONLY
              └─ except: return None
           └─ if None: return []

[Infrastructure Layer]
  infra/data_source/macro/indicators.py::get_stock_news()
    └─ try: ak.stock_news_em(symbol)
    └─ except: print log, return None
    └─ NO TUSHARE FALLBACK

RISK: 🔴 CRITICAL
  - Returns fake "loading..." instead of empty/error
  - UI shows data but it's not real
  - Users deceived into thinking data is coming
  - Multiple functions all affected
```

**Proposed Fix:**
```python
def get_fund_news(code, limit=3):
    try:
        return real_news_list  # from AKShare or Tushare
    except:
        # Don't return fake data!
        return {
            "news": [],
            "error": "news_unavailable",
            "message": "新闻源暂时不可用，请稍后重试"
        }
```

---

## 6. Global Market - US Indices (CRITICAL ❌)

```
global_market.py
  └─ get_us_indices()
     ├─ cache check
     └─ for each symbol in [".DJI", ".INX", ".IXIC"]:
        └─ get_us_index(symbol)
           └─ infra/data_source/macro/indicators.py
              └─ get_us_index(symbol)
                 └─ try: ak.us_index_daily(symbol)  ← AKSHARE ONLY
                 └─ except: print log, return None
           └─ extract close + change_pct
           └─ on fail: result[key] = None
     └─ return {
        "dji": None,
        "spx": None,
        "ixic": None,
        "available": False
     }

RISK: 🔴 CRITICAL
  - No fallback, all None on failure
  - Entire US market analysis unavailable
  - Used by get_global_snapshot()
```

---

## 7. Global Market - Forex (CRITICAL ❌)

```
global_market.py
  └─ get_forex_data()
     ├─ cache check
     └─ get_fx_spot_quote()
        └─ infra/data_source/macro/indicators.py
           └─ get_fx_spot_quote()
              └─ try: ak.fx_spot_quote()  ← AKSHARE ONLY
              └─ except: print log, return None
        └─ find "美元" + "人民币" pair
        └─ on fail: return {
           "usdcny": None,
           "dxy_proxy": None,
           "available": False
        }

RISK: 🔴 CRITICAL
  - No fallback
  - USDCNY rate unavailable
  - Forex analysis missing
```

---

## 8. Global Market - Fed Rate (CRITICAL ❌)

```
global_market.py
  └─ get_fed_rate()
     ├─ cache check
     └─ get_usa_interest_rate()
        └─ infra/data_source/macro/indicators.py
           └─ get_usa_interest_rate()
              └─ try: ak.us_interest_rate_hist()  ← AKSHARE ONLY
              └─ except: print log, return None
        └─ on fail: return {
           "current_rate": None,
           "trend": "hold",
           "available": False
        }

RISK: 🔴 CRITICAL
  - No fallback
  - Fed policy analysis impossible
  - Global impact analysis has no rate data
```

---

## 9. Global Market - PE Comparison (MEDIUM 🟡)

```
global_market.py
  └─ get_global_pe()
     ├─ US PE:
     │  └─ get_global_market_pe("美国")
     │     └─ try: ak.global_market_pe()  ← AKSHARE ONLY
     │     └─ except: result["us_pe"] = None
     │
     ├─ CN PE:
     │  └─ get_global_market_pe("中国")
     │     └─ try: ak.global_market_pe()  ← AKSHARE ONLY
     │     └─ except: result["cn_pe"] = None
     │
     ├─ Sanity check: if us_pe == cn_pe
     │  └─ Fallback to get_valuation_percentile()
     │     └─ Uses 沪深300 PE from market_data.py
     │     └─ Get's own robust fallback chain
     │     └─ Returns cn_pe correctly
     │  └─ Mark us_pe as None + notice
     │
     └─ return {
        "us_pe": None (if failed),
        "cn_pe": 23.15 (from fallback),
        "available": bool
     }

RISK: 🟡 MEDIUM
  - US PE has NO fallback (returns None)
  - CN PE has fallback (calls get_valuation_percentile)
  - Inconsistent coverage
```

---

## 10. Stock Price Provider (GOOD ✓)

```
stock_price_provider.py
  └─ get_daily_df(code, days, adjust)
     ├─ Clean code (remove sh/sz prefix)
     ├─ cache check
     └─ Attempt 1: is_configured() && _from_tushare()
        └─ get_daily_price(code, days)  ← Tushare pro_bar/daily
           └─ On success: return normalized DF ✓
           └─ On fail: continue
     │
     └─ Attempt 2: _from_akshare()
        └─ get_stock_daily_hist(code, ..., adjust)
           └─ infra/data_source/market/stocks.py
              └─ get_stock_daily_hist()
                 ├─ Attempt 1: ak.stock_zh_a_hist()  ← AKSHARE
                 │  └─ On success: return df ✓
                 │  └─ On fail: continue
                 │
                 └─ Attempt 2: get_daily_hist_mootdx()
                    └─ mootdx (通达信 TCP)  ← RESILIENT
                       └─ On success: return df ✓
                       └─ On fail: return None
           └─ On success: return normalized DF ✓
           └─ On fail: return empty DF

RISK: ✅ GOOD
  - 3-tier chain: Tushare → AKShare → mootdx
  - Very resilient
  - Good fallback design
```

---

## Summary Table: Fallback Depth

| Service | Function | Fallback Depth | Status |
|---------|----------|---|--------|
| sector_rotation | get_sector_ranking | 0/1 | ❌ CRITICAL |
| market_data | get_fear_greed_index | 0/3 | 🟡 MEDIUM |
| market_data | get_valuation_percentile | 3/4 | ✅ GOOD |
| market_data | get_fund_nav | 1/2 | ✅ GOOD |
| news_data | get_*_news | 0/1 | ❌ CRITICAL |
| global_market | get_us_indices | 0/1 | ❌ CRITICAL |
| global_market | get_forex_data | 0/1 | ❌ CRITICAL |
| global_market | get_fed_rate | 0/1 | ❌ CRITICAL |
| global_market | get_global_pe | 1/2 (CN only) | 🟡 MEDIUM |
| stock_price_provider | get_daily_df | 2/3 | ✅ GOOD |

---

## Key Observations

1. **Infrastructure Layer Pattern:**
   - `market/stocks.py` has some fallback (mootdx)
   - `macro/indicators.py` has NO fallback (AKShare only)
   - `alt/flows.py` has NO fallback (AKShare only)

2. **Service Layer Issues:**
   - Services DON'T implement fallback themselves
   - They rely 100% on infrastructure layer
   - When infrastructure has no fallback, service fails

3. **Failure Modes:**
   - Most return `None` or empty result
   - News layer returns fake "loading..." (BAD!)
   - Some return sensible defaults

4. **Which Need Urgent Fix:**
   - Sector rotation (used by Pipeline)
   - News functions (misleading users)
   - Global market indices (used by LLM analysis)

---

