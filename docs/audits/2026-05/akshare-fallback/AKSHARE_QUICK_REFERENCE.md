# MoneyBag AKShare Dependencies — Quick Reference
**2026-05-14** | Risk Assessment + Action Items

---

## 🎯 Key Findings

### Services with NO Fallback (Critical Risk)
| Service | Function | Issue | Impact |
|---------|----------|-------|--------|
| **sector_rotation** | `get_sector_ranking()` | 100% AKShare | Entire sector rotation analysis fails silently |
| **global_market** | `get_us_indices()` | 100% AKShare | US market data completely unavailable |
| **global_market** | `get_forex_data()` | 100% AKShare | USDCNY rate and forex analysis missing |
| **global_market** | `get_fed_rate()` | 100% AKShare | Fed policy analysis unavailable |
| **news_data** | All news functions | AKShare only | Returns fake "loading..." instead of empty |

### Services with Fallback (Good)
| Service | Function | Fallback Chain | Status |
|---------|----------|----------------|--------|
| **market_data** | `get_valuation_percentile()` | AKShare → Tushare → AKShare(alt) → CSIndex | ✅ Robust |
| **market_data** | `get_fund_nav()` | AKShare → Tushare | ✅ Good |
| **stock_price_provider** | `get_daily_df()` | Tushare → AKShare → mootdx | ✅ Good |

### Services with Partial Fallback (Medium Risk)
| Service | Function | Issue |
|---------|----------|-------|
| **market_data** | `get_fear_greed_index()` | Only gets沪深300 from AKShare, no Tushare fallback |
| **global_market** | `get_global_pe()` | US PE has no fallback, only CN PE does |

---

## 📊 Dependency Map

```
Frontend / Pipeline
    ↓
[Services Layer]
    ├─ market_data.py ✅✅❌
    ├─ sector_rotation.py ❌
    ├─ news_data.py ❌
    ├─ global_market.py ❌❌❌
    └─ stock_price_provider.py ✅
    ↓
[Infrastructure Layer]
    ├─ infra/data_source/market/stocks.py
    │  └─ AKShare → mootdx (stock prices OK)
    ├─ infra/data_source/macro/indicators.py
    │  └─ AKShare only (NO fallback) ❌
    └─ infra/data_source/alt/flows.py
       └─ AKShare only (NO fallback) ❌
    ↓
[Data Sources]
    ├─ AKShare (东财HTTP, 易封IP)
    ├─ Tushare (5000积分, 稳定)
    ├─ BaoStock (自由, 基础)
    ├─ mootdx (通达信TCP, 不封IP)
    └─ Tencent (实时, 基础)
```

---

## 🔴 Critical Issues

### Issue #1: Sector Data has NO Alternative Source
```
get_sector_ranking() 
  ├─ Try: AKShare stock_board_industry_summary_ths()
  └─ Fail: Returns {"available": False}  ← NO FALLBACK
```

**What happens:** Users see empty sector rotation analysis, think system is broken.

**Quick Fix (Week 1):**
```python
# Add Tushare fallback in infra/data_source/alt/flows.py
def get_industry_board_summary():
    try:
        return ak.stock_board_industry_summary_ths()
    except:
        # Fallback to Tushare if configured
        import tushare as ts
        pro = ts.pro_api()
        return pro.index_classify()  # Return reformatted data
```

---

### Issue #2: News Returns Fake "Loading" Data
```
get_market_news() 
  ├─ Try: AKShare stock_news_em("A股")
  └─ Fail: Returns [{"title": "市场资讯加载中...", "source": "系统"}]
           ↑ LIE! This isn't real data
```

**What happens:** UI shows "loading" but never updates; users think data is coming but it's not.

**Quick Fix (Week 1):**
```python
# Return empty list + error flag instead
if not news_list:
    return {"news": [], "error": "news_unavailable", "message": "新闻源暂时不可用"}
```

---

### Issue #3: Global Market Data Complete Dependency on AKShare
```
US Market Analysis:
  ├─ get_us_indices() → ak.us_index_daily()  (NO FALLBACK)
  ├─ get_forex_data() → ak.fx_spot_quote()   (NO FALLBACK)
  ├─ get_fed_rate() → ak.us_interest_rate()  (NO FALLBACK)
  └─ get_global_pe() → ak.global_market_pe() (ONLY CN has fallback)
```

**What happens:** When AKShare is down (凌晨/反爬), entire global analysis section fails.

**Medium Fix (Week 2-3):**
- Requires external APIs (yfinance for US, Fed.gov for rates, etc.)
- May need infrastructure refactor

---

### Issue #4: Fear & Greed Index Missing Tushare Fallback
```
get_fear_greed_index()
  ├─ Try: get_index_daily("sh000300")
  │      └─ ak.stock_zh_index_daily()
  └─ Fail: Returns default {"score": 50}  ← Only default, no fallback attempt
```

**What happens:** Sentiment analysis always shows neutral when AKShare fails (useless).

**Quick Fix (Week 1):**
```python
# In infra/data_source/market/stocks.py::get_index_daily()
def get_index_daily(symbol="sh000300", ...):
    try:
        return ak.stock_zh_index_daily(symbol)
    except:
        # Fallback to Tushare
        if symbol == "sh000300":
            pro = ts.pro_api()
            return pro.index_daily(ts_code="399300.SZ")
        # etc for other indices
```

---

## 📋 Action Plan

### WEEK 1 (High Priority)
- [ ] Add Tushare fallback to `get_industry_board_summary()` in `alt/flows.py`
- [ ] Fix news functions to return error flag instead of fake "loading" message
- [ ] Add Tushare fallback to `get_index_daily()` in `market/stocks.py` for fear & greed
- [ ] Document expected failure modes

### WEEK 2 (Medium Priority)
- [ ] Implement global market fallback chain (requires external APIs)
- [ ] Add BaoStock fallback for stock prices (redundancy)
- [ ] Improve logging to distinguish "no data" vs "source down"

### WEEK 3+ (Long Term)
- [ ] Consider Redis for caching failed responses (24hr grace period)
- [ ] Add circuit breaker pattern (track source health)
- [ ] Implement SLA monitoring per data source

---

## 🧪 Testing Checklist

When AKShare is down, these should NOT return errors:
- [ ] `market_data.get_valuation_percentile()` (should return Tushare data)
- [ ] `market_data.get_fund_nav()` (should return Tushare data)
- [ ] `stock_price_provider.get_daily_df()` (should return AKShare or mootdx)
- [ ] `sector_rotation.get_sector_ranking()` (after fix: should return Tushare data)
- [ ] `market_data.get_fear_greed_index()` (after fix: should return calculated from mootdx)

When AKShare is down, these SHOULD return graceful failures:
- [ ] `news_data.get_*_news()` → `{"news": [], "error": "unavailable"}`
- [ ] `global_market.get_us_indices()` → `{"available": False, "dji": None, ...}`
- [ ] `global_market.get_forex_data()` → `{"available": False, ...}`

---

## 🔗 Related Issues

1. **night_worker complaint** (stock_price_provider)
   - Error: `get_stock_daily_hist() got unexpected keyword 'symbol'`
   - Status: Likely documentation issue, not a real bug
   - Fix: Clarify that `get_stock_daily_hist(code=..., ...)` is correct usage

2. **AKShare reliability** (凌晨/反爬)
   - Currently happening 2-3 times/week
   - Sector rotation completely fails (no alternative)
   - News shows fake "loading..." data

---

## 📚 Reference

**Full Analysis:** `/AKSHARE_DEPENDENCY_ANALYSIS.md` (16KB, detailed per-function breakdown)

**Key Files to Modify:**
1. `infra/data_source/alt/flows.py` — Add Tushare fallback
2. `infra/data_source/market/stocks.py` — Add index Tushare fallback
3. `services/news_data.py` — Fix fake "loading" behavior
4. `services/global_market.py` — Add external API fallbacks (future)

---

## ❓ FAQ

**Q: Why not just use Tushare everywhere?**
- A: Tushare requires 5000积分 for some APIs (expensive), AKShare is free
- A: Some data (sector rotation) only available in AKShare

**Q: Why does news return "loading..." instead of error?**
- A: Original design tried to be "graceful" but ends up lying to users
- A: Should show "unavailable" flag instead

**Q: What's the difference between AKShare → mootdx?**
- A: AKShare uses HTTP (易被反爬封IP), mootdx uses TCP (不封IP)
- A: mootdx is通达信协议 (more robust) but different data format

**Q: Can we just cache the data?**
- A: Current cache is 5 min for prices, 30 min for sectors
- A: Even with cache, first failure of day is bad
- A: Better to have fallback + cache

---

Generated by: Claude Code  
Last Updated: 2026-05-14  
Next Review: After Week 1 fixes
