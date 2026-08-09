# MoneyBag Project: Night Worker Scheduling & Data Pipeline Analysis

**Generated**: 2026-05-22  
**Target**: `/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/scripts/night_worker.py`

---

## 1. NIGHT_WORKER SCHEDULING OVERVIEW

### 1.1 Main Execution Schedule (Line 911-1021)

**Function**: `run_night_worker()`  
**Triggered by**: Crontab `0 1 * * *` (every day at 01:00 UTC)  
**Location**: `/opt/moneybag/backend/scripts/deploy_to_server.sh` (line 188)

```bash
# PRODUCTION CRON ENTRY (deploy_to_server.sh line 188)
0 1 * * * cd $REMOTE_PATH/backend && /opt/moneybag/venv/bin/python scripts/night_worker.py \
  >> /opt/moneybag/logs/night.log 2>&1
```

**Execution Timeline** (01:00-08:30):
```
01:00 - step_health_check()          ← Data source health verification
01:15 - step_monthly_snapshot()      ← Monthly net asset snapshot (1st only)
01:30 - step_data_warm()             ← Data warming (Tushare + AKShare)
        [Cache precomputed factors]

02:00 - step_r1_phase1()             ← Phase 1: Global market analysis
        [Save 13-dimensional daily signal cache]

02:30 - step_r1_phase2()             ← Phase 2: Per-user portfolio diagnosis

03:00 - step_r1_phase3()             ← Phase 3: Recommendations + decisions
        [Save recommendations + scenarios cache]

04:00 - step_generate_products()     ← Generate briefing products
05:00 - step_archive_reports()       ← Archive broker reports
06:00 - step_maintenance()           ← Clean up old logs/records
07:00 - step_overnight_check()       ← Global futures snapshot + events ⭐
07:30 - step_morning_briefing()      ← Generate morning briefing
08:00 - Save briefings to disk (briefings_{date}.json)
```

**Execution Type**: Linear, sequential execution (NOT parallelized)  
**Duration**: ~5-10 minutes typical (handles all users + AI analysis)

---

### 1.2 Push-Only Execution (Line 1024-1043)

**Function**: `push_morning()`  
**Triggered by**: Crontab `30 8 * * 1-5` (weekdays only at 08:30 UTC)  
**Location**: `/opt/moneybag/backend/scripts/deploy_to_server.sh` (line 189)

```bash
# BACKUP PUSH CRON (only weekdays, workaround for failures)
30 8 * * 1-5 cd $REMOTE_PATH/backend && /opt/moneybag/venv/bin/python scripts/night_worker.py \
  --push-only >> /opt/moneybag/logs/night.log 2>&1
```

**Behavior**:
- Reads pre-generated briefings from `NIGHT_LOG_DIR / f"briefings_{date.today()}.json"`
- Calls `step_push_briefing()` to send via wxwork API
- Acts as **fallback/safety mechanism** if main night_worker fails

**Access via CLI**: `python scripts/night_worker.py --step overnight`

---

## 2. STEP_OVERNIGHT_CHECK() DEEP DIVE

**Location**: `night_worker.py`, lines 726-828  
**Execution Time**: ~07:00 (7th step in pipeline)  
**Purpose**: Global futures snapshot + AI-powered impact analysis

### 2.1 Data Sources (Priority Order)

#### Source 1: Global Futures Snapshot (Primary)
```python
from infra.data_source.macro.indicators import get_global_futures_snapshot()
```

**Provider**: AKShare `futures_global_spot_em()` (via akshare library)  
**Brands Fetched**:
- A50期指 (A50 Index Futures, code: CN00Y)
- 小型标普 (ES Micro, code: ES00Y)
- 小型道指 (YM Micro, code: YM00Y)
- 小型纳指 (NQ Micro, code: NQ00Y)
- NYMEX原油 (WTI Crude, code: CL00Y)
- COMEX黄金 (Gold, code: GC00Y)

**Returns**: 
```python
{
    "a50": {"price": float, "change_pct": float, "prev_close": float},
    "sp500": {...},
    "dji": {...},
    "nasdaq": {...},
    "oil": {...},
    "gold": {...},
    "available": bool,
    "source": "akshare_futures_global"
}
```

**No Caching** - Live call every time  
**Fallback**: If futures unavailable, tries `get_us_indices()` for daily close data

#### Source 2: US Indices Closing Data (Fallback)
```python
from services.global_market import get_us_indices()
```

Only called if futures snapshot fails (line 770).

#### Source 3: FX Data (Tertiary)
```python
from services.global_market import get_forex_data()
```

**Fetches**: USD/CNY exchange rate

#### Source 4: HSI (Hong Kong Index)
```python
from infra.data_source.macro.indicators import get_hsi_latest()
```

**Provider**: AKShare `stock_hk_index_daily_sina(symbol="HSI")`  
**Uses**: Last 2 days of daily data to calculate % change

---

### 2.2 get_global_futures_snapshot() Implementation

**File**: `/backend/infra/data_source/macro/indicators.py`, lines 580-673

**Key Points**:
```python
def get_global_futures_snapshot() -> dict:
    # NO CACHING - calls akshare directly every time
    # Data source: AKShare futures_global_spot_em()
    
    # Column matching is flexible:
    # - "代码" | "code" (for symbol)
    # - "最新价" | "latest" (for price)
    # - "涨跌幅" (for % change)
    # - "昨结" | "昨收" (for previous close)
    
    # Gold has special fuzzy matching: "COMEX黄金" | "纽约金" | "黄金当月"
    
    # Availability threshold: requires ≥2 symbols with data
    result["available"] = found_count >= 2
```

**Caching Policy**: 
- ❌ **NO TTL** - Not cached at the data_source level
- ❌ **NO Redis/Disk cache** - Fresh call every execution
- ⚠️ **AKShare may have internal rate limiting** (30 min? Not documented)

**Trading Session Check**:
- ❌ **NO EXPLICIT CHECK** - Just raw market data
- Uses `futures` (not daily candles) → reflects pre-market/post-market moves
- A50 reflects Hong Kong overnight trading
- WTI/Gold reflect NYMEX overnight trading
- SP500/DJI/NASDAQ reflect Chicago CME overnight futures (not NYSE closes)

---

## 3. CACHING BEHAVIOR ACROSS NIGHT_WORKER

### 3.1 Precomputed Cache System

**File**: `/backend/services/precomputed_cache.py`  
**Mechanism**: Disk-based JSON files in `DATA_DIR/precomputed/`

**TTL Configuration**:
```python
_PRECOMPUTED_TTL = {
    "recommendations": 14400,    # 4 hours
    "decisions": 14400,          # 4 hours
    "daily_signal": 7200,        # 2 hours ← Used in morning briefing
    "sector_rotation": 7200,     # 2 hours
    "broker_consensus": 14400,   # 4 hours
    "scenarios": 28800,          # 8 hours
    "factors": 7200,             # 2 hours (P0.4a: was 1h)
    "macro": 14400,              # 4 hours
    "fear_greed": 7200,          # 2 hours (P0.4a: was 1h)
    "valuation": 7200,           # 2 hours
}
```

**Non-trading day extension**: Weekend/holidays auto-extend TTL to 72 hours

**Saved by night_worker.py**:
```python
# Line 929-954 (after data warming)
save_precomputed("factors", {...})
save_precomputed("fear_greed", get_fear_greed_index())
save_precomputed("valuation", get_valuation_percentile())
save_precomputed("sector_rotation", ...)
save_precomputed("broker_consensus", ...)

# Line 962-967 (after Phase 1)
save_precomputed("daily_signal", generate_daily_signal())

# Line 978-995 (after Phase 3)
save_precomputed("recommendations", ...)
save_precomputed("scenarios", ...)
```

### 3.2 In-Memory Caches During night_worker

- **Signal Scout**: 30 min TTL (signal_scout.py)
- **Macro V8**: 24h TTL for monthly data, 1h for daily (macro_v8.py)
- **Factor IC**: 24h TTL (factor_ic.py)

**Note**: These caches are session-specific (reset when process ends)

---

## 4. FUND_RANK_TS.JSON PIPELINE

### 4.1 Generation Script

**File**: `/backend/scripts/fund_rank_build.py`  
**Output**: `backend/data/fund_rank_ts.json`

**Execution Flow**:
```python
def build_rank():
    # [1/4] Fetch all fund basics (E + O types)
    basics = get_fund_basic_all()
    
    # [2/4] Fetch latest NAV (find_latest_trade_date → 10 days lookback)
    latest_td, latest_navs = find_latest_trade_date()
    
    # [3/4] Fetch 1-year-ago NAV
    td_1y, navs_1y = find_nav_date_before(365, latest_td)
    
    # [4/4] Fetch 3-year-ago NAV
    td_3y, navs_3y = find_nav_date_before(365*3, latest_td)
    
    # Local calculation: return_1y, return_3y, composite score
    # Score = return_1y * 0.6 + return_3y * 0.4
    
    # Output: {
    #   "generated_at": ISO timestamp,
    #   "trade_date": "20260419",
    #   "date_1y_ago": "20250419",
    #   "date_3y_ago": "20230419",
    #   "total_funds": 17000+,
    #   "ranks": {
    #     "all": [top 1000],
    #     "stock": [top 500 股票型],
    #     "hybrid": [top 500 混合型],
    #     "bond": [top 500 债券型],
    #     "index": [top 500 指数型],
    #     "qdii": [top 500 QDII],
    #     "etf": [top 200 ETF]
    #   }
    # }
```

**Data Provider**: Tushare only (4 API calls)
- `get_fund_basic_all()` - Fund metadata
- `get_fund_nav_by_date()` - 3 calls for different dates

**Usage in night_worker.py**:
```python
# Line 447-477: _get_fund_recommendations()
# Reads fund_rank_ts.json for fund recommendations
rank_file = Path("/opt/moneybag/backend/data/fund_rank_ts.json")
# Filters: 5% < return_1y < 100%
# Returns: top_n funds sorted by score
```

### 4.2 Scheduling

**❌ NOT IN CRONTAB** - Currently manual trigger only!

**Usage**:
```bash
# Local build
python backend/scripts/fund_rank_build.py

# Upload to production
python backend/scripts/fund_rank_build.py --upload
# (SCP to ubuntu@150.158.47.189:/opt/moneybag/backend/data/fund_rank_ts.json)
```

**Recommendation**: Should be scheduled as:
```bash
# Daily (after market close, ~16:30 UTC+8 = 08:30 UTC)
30 8 * * * cd $REMOTE_PATH/backend && \
  /opt/moneybag/venv/bin/python scripts/fund_rank_build.py >> /opt/moneybag/logs/fund_rank.log 2>&1
```

**Last Modified Check**:
```bash
stat /opt/moneybag/backend/data/fund_rank_ts.json
```

---

## 5. HOW TO ADJUST FUTURES DATA TIMING

### Option A: Change step_overnight_check() Timing

Currently at **07:00** (1 hour before push).

**Modify**: `night_worker.py` line 1009
```python
# Current: runs as step 7 at ~07:00
overnight = step_overnight_check()

# To run at specific time, move this call:
# - Earlier: Move after step_data_warm (01:45) - but data may not be ready
# - Later: After morning_briefing (08:00) - but too late for morning push
# - Separate cron: Add independent cron job at specific time
```

### Option B: Add Separate Cron for Futures Only

```bash
# Fetch futures at 07:30 (30 min before push)
30 7 * * * cd /opt/moneybag/backend && \
  /opt/moneybag/venv/bin/python scripts/night_worker.py --step overnight \
  > /opt/moneybag/logs/futures.log 2>&1
```

### Option C: Add Pre-market Data Cache

Modify `get_global_futures_snapshot()` to add TTL:

```python
# Add to indicators.py (line 609)
_futures_cache = MemoryCache(default_ttl=1800)  # 30 min

def get_global_futures_snapshot() -> dict:
    cache_key = "global_futures_snapshot"
    cached = _futures_cache.get(cache_key)
    if cached:
        return cached
    
    # ... existing code ...
    
    _futures_cache.set(cache_key, result)
    return result
```

### Option D: Fetch Before Market Open (Asia)

Modify cron to run earlier:
```bash
# 22:00 UTC = 06:00 Shanghai time (pre-market)
0 22 * * * cd /opt/moneybag/backend && \
  /opt/moneybag/venv/bin/python scripts/night_worker.py --step overnight \
  > /opt/moneybag/logs/futures_evening.log 2>&1
```

---

## 6. TRADING SESSION CORRECTNESS

### Current Implementation

**No explicit trading session validation** - just checks if data is available.

### Recommended Additions

```python
def _validate_trading_session(data: dict) -> bool:
    """Verify futures data is from correct trading session
    
    A50:      Hong Kong 16:00 (UTC+8) - 08:00 next day
    ES/YM/NQ: CME 16:00 prev day (UTC-6) - 15:15 next day
    CL/GC:    NYMEX 22:00 prev day (UTC-5) - 21:00 next day
    """
    
    # Check timestamp field if available in data
    # Verify A50 is from current day (HK trading)
    # Verify CME futures are pre-market for next day
    # Skip if stale (>24 hours old)
```

---

## 7. RUNNING JUST step_overnight_check()

```bash
# From command line
cd /opt/moneybag/backend
python scripts/night_worker.py --step overnight

# Or in cron for separate execution
30 7 * * * cd /opt/moneybag/backend && \
  python scripts/night_worker.py --step overnight >> /opt/moneybag/logs/futures.log 2>&1
```

**Available steps** (line 1055-1066):
- `health`
- `warm`
- `phase1`
- `phase2`
- `phase3`
- `reports`
- `maintain`
- `overnight` ⭐

---

## 8. KEY FINDINGS SUMMARY

| Item | Value | Status |
|------|-------|--------|
| **main.run_night_worker()** | `0 1 * * *` (01:00 UTC daily) | ✅ Configured |
| **step_overnight_check()** | Line 726; runs ~07:00 in pipeline | ✅ Implemented |
| **futures data source** | AKShare `futures_global_spot_em()` | ✅ Live, no cache |
| **futures caching** | ❌ NONE (fresh call each time) | ⚠️ Consider adding |
| **fund_rank_ts.json generator** | `fund_rank_build.py` | ⏸️ Manual trigger only |
| **fund_rank schedule** | ❌ NOT in crontab | ⚠️ **NEEDS SETUP** |
| **precomputed cache TTL** | 2-14400s (depends on type) | ✅ Configured |
| **trading session validation** | ❌ Not implemented | 💡 Suggested |

---

## 9. RECOMMENDED ACTIONS

1. **Add fund_rank_build.py to crontab** (daily, post-market)
   ```bash
   30 8 * * * python /opt/moneybag/backend/scripts/fund_rank_build.py
   ```

2. **Add futures caching** (30 min TTL) to avoid unnecessary AKShare calls

3. **Add trading session validation** to ensure data freshness

4. **Monitor night_worker logs** for execution duration and failures
   ```bash
   tail -f /opt/moneybag/logs/night.log
   ```

5. **Run futures fetch earlier** if current 07:00 time misses Asia pre-market data
   - Consider 22:00 UTC (06:00 Shanghai) for pre-market snapshot

---

**End of Report**
