# MoneyBag `/api/api/` Double-Path Bug - Complete Analysis

## Executive Summary

**Status**: 6 bug instances found in 2 frontend files
- **pages/stocks.js**: 5 instances (lines 52, 69, 70, 82, 87)
- **pages/chart.js**: 1 instance (line 57)

The root cause: Frontend code appends `/api/` after `API_BASE` which already contains `/api`, creating double-path URLs like `/api/api/behavior/...` instead of `/api/behavior/...`

---

## ROOT CAUSE ANALYSIS

### Frontend Configuration (app.js, lines 60-64)
```javascript
const API_BASE = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1' || h.startsWith('192.168.')) 
    return 'http://localhost:8000/api';
  return '/api'; // Production: already includes /api prefix!
})();
```

**Key fact**: `API_BASE = '/api'` (production) or `'http://localhost:8000/api'` (dev)

### Backend Architecture (main.py)
The backend recently refactored (M1 W2) to move all routes into `api/*.py` files. Each file defines routes with full `/api/...` paths:

```python
# api/behavior.py
router = APIRouter(tags=["行为风控"])
@router.get("/api/behavior/guard-status")      # Full path includes /api/
@router.post("/api/behavior/guard-toggle")
@router.get("/api/behavior/active-interventions")
@router.post("/api/behavior/override/{index}")

# api/chart.py
@router.get("/api/chart/{fund_code}")          # Full path includes /api/
```

In `main.py`, routers are included WITHOUT a prefix:
```python
app.include_router(behavior_router)  # No prefix parameter
app.include_router(chart_router)
```

This means the full paths defined in decorators become the actual routes.

---

## DETAILED BUG REPORT

### FILE: pages/stocks.js

#### BUG #1 - Line 52 (loadBehaviorGuardBar)
```javascript
const r=await fetch(API_BASE+'/api/behavior/guard-status?'+getProfileParam(),{
    signal:AbortSignal.timeout(5000)
});
```
| Aspect | Value |
|--------|-------|
| Current URL | `/api/api/behavior/guard-status` ❌ |
| Correct URL | `/api/behavior/guard-status` ✅ |
| Fix | Change to: `API_BASE+'/behavior/guard-status?'...` |

#### BUG #2 - Line 69 (_loadGuardPanel - first fetch in Promise.all)
```javascript
fetch(API_BASE+'/api/behavior/guard-status?'+getProfileParam(),{
    signal:AbortSignal.timeout(5000)
}),
```
| Aspect | Value |
|--------|-------|
| Current URL | `/api/api/behavior/guard-status` ❌ |
| Correct URL | `/api/behavior/guard-status` ✅ |
| Fix | Change to: `API_BASE+'/behavior/guard-status?'...` |

#### BUG #3 - Line 70 (_loadGuardPanel - second fetch in Promise.all)
```javascript
fetch(API_BASE+'/api/behavior/active-interventions?'+getProfileParam(),{
    signal:AbortSignal.timeout(5000)
})]);
```
| Aspect | Value |
|--------|-------|
| Current URL | `/api/api/behavior/active-interventions` ❌ |
| Correct URL | `/api/behavior/active-interventions` ✅ |
| Fix | Change to: `API_BASE+'/behavior/active-interventions?'...` |

#### BUG #4 - Line 82 (_toggleGuard)
```javascript
try{await fetch(API_BASE+'/api/behavior/guard-toggle?'+getProfileParam(),{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({enabled,reason:'用户手动切换'})
});
```
| Aspect | Value |
|--------|-------|
| Current URL | `/api/api/behavior/guard-toggle` ❌ |
| Correct URL | `/api/behavior/guard-toggle` ✅ |
| Fix | Change to: `API_BASE+'/behavior/guard-toggle?'...` |

#### BUG #5 - Line 87 (_overrideIntervention)
```javascript
try{await fetch(API_BASE+'/api/behavior/override/'+idx+'?'+getProfileParam(),{
    method:'POST'
});
```
| Aspect | Value |
|--------|-------|
| Current URL | `/api/api/behavior/override/{idx}` ❌ |
| Correct URL | `/api/behavior/override/{idx}` ✅ |
| Fix | Change to: `API_BASE+'/behavior/override/'...` |

---

### FILE: pages/chart.js

#### BUG #6 - Line 57 (_loadChartData)
```javascript
const url = `${API_BASE}/api/chart/${_chartFundCode}?period=${_chartPeriod}&${getProfileParam()}`;
```
| Aspect | Value |
|--------|-------|
| Current URL | `/api/api/chart/{fund_code}` ❌ |
| Correct URL | `/api/chart/{fund_code}` ✅ |
| Fix | Change to: `` `${API_BASE}/chart/${_chartFundCode}?...` `` |

---

## BACKEND ROUTE MAPPINGS

### behavior.py Routes (all correctly defined in backend)
| Decorator | Function | Line | HTTP Method | Expected Frontend Path |
|-----------|----------|------|------------|------------------------|
| `@router.get("/api/behavior/guard-status")` | `guard_status` | 20 | GET | `/api/behavior/guard-status` |
| `@router.post("/api/behavior/guard-toggle")` | `guard_toggle` | 33 | POST | `/api/behavior/guard-toggle` |
| `@router.get("/api/behavior/active-interventions")` | `active_interventions` | 44 | GET | `/api/behavior/active-interventions` |
| `@router.post("/api/behavior/override/{index}")` | `override` | 54 | POST | `/api/behavior/override/{index}` |

### chart.py Routes (correctly defined in backend)
| Decorator | Function | Line | HTTP Method | Expected Frontend Path |
|-----------|----------|------|------------|------------------------|
| `@router.get("/api/chart/{fund_code}")` | `get_chart_data` | 17 | GET | `/api/chart/{fund_code}` |

---

## FIX PATTERN

### Pattern 1: Concatenation (used in stocks.js)
```javascript
// WRONG
fetch(API_BASE + '/api/behavior/guard-status?...')

// CORRECT
fetch(API_BASE + '/behavior/guard-status?...')
```

### Pattern 2: Template Literals (used in chart.js)
```javascript
// WRONG
const url = `${API_BASE}/api/chart/${code}?...`;

// CORRECT
const url = `${API_BASE}/chart/${code}?...`;
```

---

## COMPLETE FIX CHECKLIST

### pages/stocks.js
- [ ] Line 52: Remove `/api` from path
- [ ] Line 69: Remove `/api` from path
- [ ] Line 70: Remove `/api` from path
- [ ] Line 82: Remove `/api` from path
- [ ] Line 87: Remove `/api` from path

### pages/chart.js
- [ ] Line 57: Remove `/api` from path

---

## VALIDATION STEPS

After applying fixes, verify these endpoints work:

1. **Guard Status** (behavior.py:20)
   ```
   GET /api/behavior/guard-status
   Expected: { "enabled": bool, "active_count": int, "status_icon": str, "tip": str }
   ```

2. **Active Interventions** (behavior.py:44)
   ```
   GET /api/behavior/active-interventions
   Expected: { "total": int, "interventions": [...] }
   ```

3. **Guard Toggle** (behavior.py:33)
   ```
   POST /api/behavior/guard-toggle
   Body: { "enabled": bool, "reason": str }
   Expected: { "ok": true, "enabled": bool, "message": str }
   ```

4. **Override Intervention** (behavior.py:54)
   ```
   POST /api/behavior/override/{index}
   Expected: { "ok": true, "message": str }
   ```

5. **Chart Data** (chart.py:17)
   ```
   GET /api/chart/{fund_code}?period=1y
   Expected: { "fund_code": str, "kline_data": [...], "volume_data": [...], "indicators": {...} }
   ```

---

## IMPACT ASSESSMENT

### Affected Features
1. **Behavior Guard System** (stocks.js) - 4 endpoints broken
   - Guard status display on stocks page
   - Guard panel modal
   - Guard toggle functionality
   - Intervention override functionality

2. **Mini Chart Feature** (chart.js) - 1 endpoint broken
   - Fund K-line chart display
   - Chart data loading

### User Impact
- ❌ Cannot view behavior guard status
- ❌ Cannot toggle guard on/off
- ❌ Cannot override interventions
- ❌ Cannot view fund charts

---

## ROOT CAUSE TIMELINE

1. **Original State**: Backend routes were under `/api/*.py` files with different structure
2. **M1 W2 Refactor**: Backend consolidated routes with full `/api/...` paths in decorators
3. **Frontend Code**: Written before or without accounting for new backend path structure
4. **Result**: Frontend appends `/api/` thinking backend routes don't have it (but they do)

---

## RECOMMENDATIONS

### Immediate
1. Fix all 6 instances by removing the duplicate `/api/` prefix
2. Test all endpoints in browser DevTools Network tab
3. Verify both stocks page and chart modal work

### Medium-term
1. **Code review**: Check other API calls in other pages to ensure no similar pattern
2. **Frontend API layer**: Consider creating a helper function to prevent this:
   ```javascript
   function apiUrl(path) {
     // Ensures no duplicate /api prefix
     const cleanPath = path.startsWith('/') ? path : '/' + path;
     return API_BASE + cleanPath;
   }
   ```

### Long-term
1. **Documentation**: Add backend API route structure to developer docs
2. **Frontend tooling**: Add linting rule to catch `API_BASE + '/api/'` pattern
3. **Backend consistency**: Consider having routers use relative paths without `/api/`, 
   and add prefix when including in main.py:
   ```python
   app.include_router(behavior_router, prefix="/api")
   ```
   This would make backend routes like `@router.get("/behavior/guard-status")`

---

## OTHER FILES CHECKED

Confirmed NO double-path bugs in these files:
- ✅ pages/alloc.js
- ✅ pages/analysis.js
- ✅ pages/assets.js
- ✅ pages/chat.js
- ✅ pages/history.js
- ✅ pages/insight.js
- ✅ pages/landing.js
- ✅ pages/ledger.js
- ✅ pages/portfolio.js
- ✅ pages/quiz.js

