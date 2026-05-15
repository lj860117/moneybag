# Technical Reference: AI Hallucination Fixes

## Architecture Overview

```
User Question (Morning Briefing)
    ↓
Steward.briefing()
    ├─ Check Cache (4-hour TTL check) ← FIX #3
    ├─ Get Market Regime
    ├─ Execute "fast" Pipeline
    │   ├─ get_north_net_flow() ← FIX #2 (Tushare fallback)
    │   ├─ get_hsgt_hist() ← FIX #2 (Tushare fallback)
    │   ├─ Other data sources...
    │   └─ Compile data_text
    ├─ Call LLM with prompt ← FIX #1 (data-completeness declaration)
    └─ Cache result + Return
```

## Fix #1: Data-Completeness Declaration in LLM Prompt

**File**: `backend/scripts/night_worker.py` (lines ~256-262)
**Root Cause**: LLM receives partial data and infers/fabricates missing PBOC operations (MLF, OMO, PBOC)
**Solution**: Explicit declaration of what data IS available and what IS NOT

### Implementation Details

```python
# BEFORE:
prompt = f"""基于以下数据写一段 200 字以内的市场研判
{data_text}
"""

# AFTER:
prompt = f"""你是 A 股宏观策略分析师。

【数据完整性声明】本分析基于以下实时数据：
- 恐贪指数、估值百分位、北向资金、银行间利率、融资余额、地缘风险、行业热点、机构共识、新闻头条
- 【缺失数据提示】本次快照不包含：MLF操作、OMO规模、PBOC逆回购、出口数据、新增贷款等央行精准操作数据
- 重要：请ONLY基于上述实时数据进行分析，对央行政策判断必须标注"缺少央行操作数据"的警示

请基于上述数据写一段 200 字以内的市场研判。

{data_text}

要求: 
1. 1句话结论（基于上述数据）
2. 3个要点（如涉及央行政策需标注数据缺失）
3. 风险提示

语言: 用普通人能看懂的大白话，不要英文术语和专业缩写
"""
```

### Why This Works

1. **Explicit Boundary Setting**: The `【缺失数据提示】` section tells the LLM exactly what's NOT available
2. **Prevents Inference**: LLM cannot make up plausible-sounding PBOC data without explicitly acknowledging the gap
3. **Mandatory Disclaimer**: The "如涉及央行政策需标注数据缺失" requirement forces transparency
4. **Prompt Engineering Best Practice**: Named sections (【】) are highly visible and attention-grabbing for LLMs

### Monitoring

```bash
# Check for successful data completeness flagging
grep -c "缺少央行操作数据" logs/night_worker.log  # Should be > 0 when policies mentioned
grep -c "MLF\|OMO\|逆回购" logs/night_worker.log  # Should be < 1 (very rare, all flagged)

# Examples of good behavior:
# ✅ "建议谨慎，因为缺少央行操作数据支持判断"
# ✅ "MLF操作等央行精准操作数据缺失，暂不做政策判断"
# ❌ "央行今日流动性宽松" (without data to back it)
```

## Fix #2: Tushare Fallback for Northbound Capital

**File**: `backend/infra/data_source/alt/flows.py` (lines 19-63, 185-257)
**Root Cause**: AKShare API failures cause data gaps → LLM makes up numbers to fill gaps
**Solution**: Two-tier degradation chain (Primary: AKShare → Fallback: Tushare)

### Implementation Details

#### Function 1: `get_hsgt_hist(symbol="北向资金")`

```python
def get_hsgt_hist(symbol: str = "北向资金") -> Any:
    # TIER 1: Primary source (AKShare)
    try:
        import akshare as ak
        result = ak.stock_hsgt_hist_em(symbol=symbol)
        if result is not None and len(result) > 0:
            return result  # Success
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_hsgt_hist({symbol}) - AKShare failed: {e}")
    
    # TIER 2: Fallback source (Tushare) - for 北向资金 only
    if symbol == "北向资金":
        try:
            import os
            ts_token = os.environ.get("TUSHARE_TOKEN", "")
            if ts_token:
                import tushare as ts
                ts.set_token(ts_token)
                pro = ts.pro_api()
                result = pro.hsgt_detail(start_date="20230101")
                if result is not None and len(result) > 0:
                    import pandas as pd
                    # CRITICAL: Transform unit from 1M to 亿
                    transformed = pd.DataFrame({
                        '日期': result['trade_date'],
                        '北向资金': result['north_money'] / 100,
                    })
                    print(f"[DATA_SOURCE/ALT] get_hsgt_hist: Fallback to Tushare success ({len(transformed)} rows)")
                    return transformed
        except Exception as e:
            print(f"[DATA_SOURCE/ALT] get_hsgt_hist (Tushare fallback failed): {e}")
    
    return None  # Both sources exhausted
```

#### Function 2: `get_north_net_flow()`

```python
def get_north_net_flow() -> Any:
    # TIER 1: Primary source (AKShare)
    try:
        import akshare as ak
        result = ak.stock_hsgt_north_net_flow_in_em()
        if result is not None and len(result) > 0:
            return result  # Success
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_north_net_flow (AKShare failed): {e}")
    
    # TIER 2: Fallback source (Tushare)
    try:
        import os
        ts_token = os.environ.get("TUSHARE_TOKEN", "")
        if ts_token:
            import tushare as ts
            ts.set_token(ts_token)
            pro = ts.pro_api()
            result = pro.hsgt_detail(start_date="20230101")
            if result is not None and len(result) > 0:
                import pandas as pd
                # CRITICAL: Transform columns and units
                transformed = pd.DataFrame({
                    '日期': result['trade_date'],
                    '北向资金(亿)': result['north_money'] / 100,
                    '沪股通(亿)': result['sh_money'] / 100,
                    '深股通(亿)': result['sz_money'] / 100,
                })
                print(f"[DATA_SOURCE/ALT] get_north_net_flow: Fallback to Tushare success ({len(transformed)} rows)")
                return transformed
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_north_net_flow (Tushare fallback failed): {e}")
    
    return None  # Both sources exhausted
```

### Critical Implementation Notes

1. **Unit Conversion**: Tushare data is in units of 1M (100万), needs division by 100 to get 亿 (100M)
   ```
   Tushare: 北向资金 = 500 (1M units) → AKShare format: 5 (亿)
   Formula: north_money / 100 = 500 / 100 = 5 亿
   ```

2. **Column Name Alignment**: Different sources use different column names
   ```
   AKShare: '日期', '北向资金', '沪股通', '深股通'
   Tushare: 'trade_date', 'north_money', 'sh_money', 'sz_money'
   → Must transform to AKShare format for upstream consistency
   ```

3. **Selective Fallback**: For `get_hsgt_hist()`, fallback only when symbol=="北向资金"
   - Other symbols (沪股通, 深股通, 南向资金) may not have Tushare equivalents
   - Prevents false positives

### Monitoring

```bash
# Check Tushare fallback activation frequency
grep "Fallback to Tushare success" logs/data_source.log | wc -l  # Should be 0-2/day normally
grep "Fallback to Tushare success" logs/data_source.log | tail -5

# Alert if fallback > 5 times/day (indicates primary source reliability issue)
grep -c "Fallback to Tushare success" logs/data_source.log | awk '$1 > 5 {print "⚠️ HIGH FALLBACK RATE"}'

# Verify transformation correctness
tail -10 logs/data_source.log | grep "north_money\|北向资金"
```

## Fix #3: Cache TTL Reduction from 24h to 4h

**File**: `backend/services/steward.py` (lines 153-215)
**Root Cause**: Morning cache (07:30) served all day → afternoon users see stale northbound/margin data
**Solution**: Implement mtime-based TTL check, invalidate cache after 4 hours

### Implementation Details

```python
def briefing(self, user_id: str) -> dict:
    # 1. Generate cache path
    today = datetime.now().strftime("%Y%m%d")
    cache_fp = _BRIEF_DIR / f"{user_id}_{today}.json"
    CACHE_TTL_HOURS = 4  # ← CRITICAL: 4-hour window, not 24-hour
    
    # 2. Check if cache exists
    if cache_fp.exists():
        try:
            cached = json.loads(cache_fp.read_text(encoding="utf-8"))
            cache_date = _extract_cache_date(cache_fp.stem)
            
            # 3. Verify today's date AND check TTL
            if cache_date == today:  # Same day check
                try:
                    # Get file modification time
                    file_mtime = cache_fp.stat().st_mtime
                    cache_age_seconds = time.time() - file_mtime
                    cache_age_hours = cache_age_seconds / 3600
                    
                    # 4. Two-condition check: date match AND age < 4 hours
                    if cache_age_hours < CACHE_TTL_HOURS:
                        # ✅ Cache is fresh, use it
                        cached["from_cache"] = True
                        cached["cache_age_minutes"] = round(cache_age_seconds / 60)
                        print(f"[STEWARD] ✅ 使用缓存 (生成于 {cache_age_hours:.1f}h前)")
                        return cached
                    else:
                        # ❌ Cache expired, delete and regenerate
                        cache_fp.unlink()
                        print(f"[STEWARD] 删除过期缓存 (已{cache_age_hours:.1f}h): {cache_fp.name}")
                except Exception as e:
                    print(f"[STEWARD] 检查缓存年龄失败: {e}")
            else:
                # ❌ Different day, delete and regenerate
                cache_fp.unlink()
                print(f"[STEWARD] 删除过期缓存（日期不符）: {cache_fp.name}")
        except Exception as e:
            print(f"[STEWARD] 读晨报缓存失败: {e}")
    
    # 5. No valid cache, regenerate
    start = time.time()
    ctx = DecisionContext(user_id=user_id, question="每日简报")
    
    # ... (regime classification, pipeline execution) ...
    
    # 6. Cache the result
    briefing = { ... }
    try:
        _BRIEF_DIR.mkdir(parents=True, exist_ok=True)
        cache_fp.write_text(json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[STEWARD] 写晨报缓存失败: {e}")
    
    return briefing
```

### Cache Timeline Example

```
07:30  → night_worker generates & caches briefing (mtime = 07:30:00)
        ✅ Cache age = 0h → Use cache

09:00  → User calls API
        ✅ Cache age = 1.5h → Use cache (< 4h)

11:30  → User calls API  
        ✅ Cache age = 4h (boundary)
        → Check: 4h < 4h? NO → Delete cache
        → Regenerate with fresh data (market might have moved)

14:00  → User calls API
        ✅ Cache age = fresh (from 11:30 regeneration)
        → Use new cache (< 4h old)

23:59  → Cache expires automatically next day (date != today)
```

### Monitoring

```bash
# Monitor cache lifecycle
grep "使用缓存\|删除过期缓存" logs/steward.log | tail -20

# Expected pattern:
# 07:31 ✅ 写晨报缓存成功
# 09:00 ✅ 使用缓存 (生成于 1.5h前)
# 11:30 删除过期缓存 (已4.0h)
# 11:31 ✅ 写晨报缓存成功

# Alert if cache deleted/regenerated < 2 hours (likely timeout issue)
grep "删除过期缓存" logs/steward.log | awk -F'已' '{print $2}' | awk -F'h' '{if ($1 < 2) print "⚠️ PREMATURE CACHE DELETION: " $0}'
```

## Data Flow Diagram

```
Morning Report Generation (07:30)
    ├─ Fetch northbound capital
    │   └─ get_north_net_flow()
    │       ├─ Try: AKShare stock_hsgt_north_net_flow_in_em()
    │       └─ Fallback: Tushare hsgt_detail() [with unit conversion]
    ├─ Fetch other macro data
    ├─ Compile data_text with all available metrics
    ├─ Call LLM with prompt (includes 【数据完整性声明】)
    ├─ LLM generates analysis
    ├─ Cache result (mtime = 07:30)
    └─ Return to user

Afternoon Briefing Request (14:00)
    ├─ Check cache (mtime = 07:30)
    │   └─ age = 6.5 hours > 4 hours TTL
    │   └─ Delete cache
    ├─ Re-fetch northbound capital (new data point)
    │   └─ May be different from morning
    ├─ Call LLM with fresh data
    ├─ Cache result (mtime = 14:00)
    └─ Return updated analysis to user
```

## Common Issues and Solutions

### Issue 1: "Fallback to Tushare failed"
**Symptoms**: Both AKShare AND Tushare fail
**Debug**:
```bash
echo $TUSHARE_TOKEN  # Verify token exists
python -c "import tushare as ts; print(ts.__version__)"  # Verify library
```
**Solution**: 
- Ensure TUSHARE_TOKEN env var is set
- Check Tushare API quota limits
- Add retry logic with exponential backoff

### Issue 2: "Cache age check failed"
**Symptoms**: Cache TTL check throws exception
**Debug**:
```bash
ls -la data/briefings/  # Check file permissions
stat data/briefings/user_id_20260515.json  # Check mtime
```
**Solution**:
- Ensure briefings directory has write permissions
- Check disk space (no space left on device)
- Verify filesystem supports mtime (not some weird mount)

### Issue 3: Unit mismatch in northbound data
**Symptoms**: Data shows "5000000" instead of "5"
**Debug**:
```python
# Check raw Tushare output
import tushare as ts
ts.set_token(os.environ["TUSHARE_TOKEN"])
pro = ts.pro_api()
df = pro.hsgt_detail()
print(df['north_money'].head())  # Should be in 1M units
```
**Solution**:
- Verify unit conversion formula: `/ 100` converts 1M to 亿
- Check AKShare column names haven't changed
- Add type hints to catch conversion errors early

## Testing Checklist

- [ ] Deploy Fix #1: LLM generates "缺少央行操作数据" when appropriate
- [ ] Deploy Fix #2: Northbound data available even if AKShare down
- [ ] Deploy Fix #3: Cache expires after 4h, fresh data fetched
- [ ] Monitor logs for 24h: no errors in fallback chains
- [ ] Monitor metrics: cache hit rate 07:30-11:30 is 80%+, 11:30-15:00 is 90%+
- [ ] End-to-end test: Run full morning briefing, verify all data sources responsive

