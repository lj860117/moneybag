# AI Hallucination Fix Deployment Checklist

**Commit**: 3f065e175a0d22e9e7996dadc7bebfc8063834a5
**Date**: 2026-05-15
**Status**: ✅ READY FOR DEPLOYMENT

## Pre-Deployment Verification ✅

- [x] All 3 files compile without syntax errors
- [x] Fixes committed to git with comprehensive message
- [x] No breaking changes to existing APIs
- [x] All changes are additive (backward compatible)

## Deployment Steps

### 1. Environment Verification (5 min)
```bash
# Verify Tushare token is configured
echo $TUSHARE_TOKEN

# Verify data cache directory exists
mkdir -p data/briefings
```

### 2. Code Deployment (2 min)
```bash
cd /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend
git pull origin
# (already at 3f065e1)
```

### 3. Service Restart (3 min)
```bash
# Restart backend services
docker restart moneybag-backend
# or
systemctl restart moneybag-services
# or 
python scripts/night_worker.py --test  # Test mode
```

### 4. Health Check (5 min)
```bash
# Check logs for no errors
tail -f logs/night_worker.log | grep -i "error\|exception"

# Verify morning report generation
curl http://localhost:8000/api/briefing/test

# Check data sources
curl http://localhost:8000/api/health/data_sources
```

## Post-Deployment Monitoring (24 hours)

### Observability Checklist

**Fix #1 Verification**: Look for "缺少央行操作数据" warnings in logs
```
grep "缺少央行操作数据\|【数据完整性声明】\|【缺失数据提示】" logs/night_worker.log
```
- **Expected**: LLM prompts now include data completeness declarations
- **Success Metric**: No MLF/OMO/export hallucinations in morning reports
- **Duration**: 24-48 hours of report generation cycles

**Fix #2 Verification**: Look for Tushare fallback logs
```
grep "Fallback to Tushare\|get_hsgt_hist\|get_north_net_flow" logs/data_source.log
```
- **Expected**: When AKShare fails, Tushare fallback activates
- **Success Metric**: Northbound capital data present even if AKShare down
- **Test**: Simulate AKShare failure to verify fallback works
- **Duration**: Watch for 1-2 market disruption cycles

**Fix #3 Verification**: Look for cache TTL enforcement
```
grep "使用缓存\|删除过期缓存\|缓存年龄\|cache_age" logs/steward.log
```
- **Expected**: Cache invalidated after 4 hours, fresh data fetched
- **Success Metric**: Different data between 10:00 and 12:00 calls
- **Test**: Monitor briefing API at 07:30, 10:00, 11:30, 14:00
- **Duration**: Watch for 2-3 full market days

### Key Metrics to Monitor

| Fix | Metric | Target | Alert If |
|-----|--------|--------|----------|
| #1 | MLF/OMO hallucination count | 0 per day | > 0 |
| #1 | "缺少央行数据" flagging | Present when policy mentioned | Never appears |
| #2 | Tushare fallback activation | 0-1 times/day | > 5 times/day |
| #2 | Northbound capital data gap | 0 minutes | > 30 minutes |
| #3 | Cache hit rate 07:30-11:30 | 80%+ | < 50% |
| #3 | Cache miss rate 11:30-15:00 | 90%+ | < 70% |

## Rollback Plan (if needed)

```bash
cd /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend
git revert 3f065e1
git push origin main
docker restart moneybag-backend
```

## Success Criteria

✅ **Fix #1**: User reports no more fabricated PBOC data in morning reports
✅ **Fix #2**: Northbound capital data survives AKShare failures (Tushare fallback works)
✅ **Fix #3**: Afternoon/evening users see fresh data (not stale morning cache)

## Support Contacts

- Data Source Issues: Check logs in `infra/data_source/alt/flows.py` for Tushare token/network issues
- LLM Prompt Issues: Review actual LLM requests in LLMGateway logs
- Cache Issues: Check file mtime in `data/briefings/` directory
