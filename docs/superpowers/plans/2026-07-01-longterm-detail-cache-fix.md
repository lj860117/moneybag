# Longterm Detail Cache Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix MoneyBag longterm fund/stock detail popups so they show real content immediately instead of timing out or rendering empty placeholders.

**Architecture:** Address the problem in two layers. First, make `/api/fund/detail/{code}?userId=...` reuse shared detail cache when the user does not actually hold the fund, and extend cache warming to preheat longterm fund detail codes. Second, make the longterm stock popup fetch/merge missing stock basic + financial data instead of assuming the longterm ranking payload already contains the full stock-screen schema.

**Tech Stack:** FastAPI, Python cache warmer, vanilla JS modal components, pytest regression tests, Node syntax check.

---

### Task 1: Lock the bug in tests

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_longterm_fund_detail_reuses_shared_cache_when_user_not_holding(monkeypatch):
    ...


def test_longterm_stock_modal_fetches_stock_basic_and_financials():
    components = (BACKEND_DIR.parent / "pages" / "_components.js").read_text(encoding="utf-8")
    assert "API_BASE + '/stock-basic/'" in components
    assert "API_BASE + '/stock/financials/'" in components
    assert "showStockDetailModal = async function" in components
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -m pytest \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_regression_signal_and_cache.py" \
  -k "longterm_fund_detail_reuses_shared_cache_when_user_not_holding or longterm_stock_modal_fetches_stock_basic_and_financials" -q
```
Expected: FAIL because current backend duplicates user-scoped cache work and current stock modal is synchronous/static.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_regression_signal_and_cache.py
git commit -m "test: cover longterm detail cache regressions"
```

### Task 2: Fix longterm fund detail cache path

**Files:**
- Modify: `backend/api/fund_detail.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: Implement minimal backend fix**

```python
shared_cache_key = f"fund_detail_{code}"
user_cache_key = f"{shared_cache_key}_{userId}" if userId else shared_cache_key

if userId:
    cached = _get_cached(user_cache_key, allow_stale=True)
    if cached:
        return cached

    base_detail = _get_cached(shared_cache_key, allow_stale=True)
    if base_detail is not None:
        enriched = _enrich_detail_with_holding(dict(base_detail), code, userId)
        if enriched.get("holding_relation") == "🔵 已持仓":
            _set_cached(user_cache_key, enriched)
            return enriched
        return base_detail
```

- [ ] **Step 2: Extend implementation so cold user-scoped non-holding requests store/reuse shared cache**

```python
result = { ... }
if userId:
    enriched = _enrich_detail_with_holding(dict(result), code, userId)
    if enriched.get("holding_relation") == "🔵 已持仓":
        _set_cached(user_cache_key, enriched)
        return enriched

_set_cached(shared_cache_key, result)
return result
```

- [ ] **Step 3: Run targeted tests**

Run:
```bash
PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -m pytest \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_regression_signal_and_cache.py" \
  -k "longterm_fund_detail_reuses_shared_cache_when_user_not_holding or longterm_funds_fallback" -q
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/api/fund_detail.py backend/tests/test_regression_signal_and_cache.py
git commit -m "fix: reuse shared fund detail cache for longterm popups"
```

### Task 3: Preheat longterm fund detail caches

**Files:**
- Modify: `backend/scripts/cache_warmer.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: Add longterm fund detail warming after longterm ranking warm-up**

```python
for uid in ["LeiJiang", "BuLuoGeLi"]:
    try:
        r = _rq.get(f"http://127.0.0.1:8000/api/longterm/funds?userId={uid}", timeout=180)
        funds = (r.json().get("funds") or [])[:12] if r.ok else []
        _warm_fund_details([f.get("code") for f in funds if f.get("code")], f"长持基金详情 {uid}")
    except Exception:
        pass
```

- [ ] **Step 2: Add regression assertion**

```python
assert "/api/longterm/funds?userId={uid}" in warmer
assert "长持基金详情" in warmer
```

- [ ] **Step 3: Run targeted tests**

Run:
```bash
PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -m pytest \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_regression_signal_and_cache.py" \
  -k "cache_warmer or longterm_fund_detail_reuses_shared_cache_when_user_not_holding" -q
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/cache_warmer.py backend/tests/test_regression_signal_and_cache.py
git commit -m "fix: preheat longterm fund detail caches"
```

### Task 4: Make longterm stock popup fetch missing data

**Files:**
- Modify: `pages/_components.js`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: Convert stock detail modal to async and show loading shell first**

```javascript
window.showStockDetailModal = async function(stockData) {
  ...
  document.body.appendChild(o)
  const body = document.getElementById('stockDetailBody')
  body.innerHTML = loadingHtml
}
```

- [ ] **Step 2: Fetch stock basic + financial data and merge with longterm row**

```javascript
const [basicRes, finRes] = await Promise.allSettled([
  fetch(API_BASE + '/stock-basic/' + code, { signal: AbortSignal.timeout(15000) }),
  fetch(API_BASE + '/stock/financials/' + code, { signal: AbortSignal.timeout(15000) }),
])
const merged = {
  ...stockData,
  price: basic.price ?? stockData.price,
  industry: basic.industry ?? stockData.industry,
  roe: fin.roe ?? stockData.roe ?? stockData.avg_roe,
  gross_margin: fin.gross_margin ?? stockData.gross_margin ?? stockData.avg_gpm,
  debt_ratio: fin.debt_ratio ?? stockData.debt_ratio ?? stockData.avg_debt,
  revenue_growth: fin.revenue_growth ?? stockData.revenue_growth ?? stockData.avg_np_growth,
  score: stockData.score ?? stockData.longterm_score,
  aiComment: stockData.aiComment ?? stockData.note,
}
```

- [ ] **Step 3: Keep graceful fallback for fields that still do not exist**

```javascript
const scoreValue = merged.score ?? merged.longterm_score ?? '—'
const roeValue = merged.roe ?? merged.avg_roe ?? '—'
```

- [ ] **Step 4: Run syntax and targeted tests**

Run:
```bash
"/Users/leijiang/.workbuddy/binaries/node/versions/22.12.0/bin/node" --check "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/pages/_components.js" && \
PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -m pytest \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_regression_signal_and_cache.py" \
  -k "longterm_stock_modal_fetches_stock_basic_and_financials" -q
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pages/_components.js backend/tests/test_regression_signal_and_cache.py
git commit -m "fix: enrich longterm stock detail popup"
```

### Task 5: Verify, deploy, and regress on server

**Files:**
- Modify: `pages/_components.js`, `backend/api/fund_detail.py`, `backend/scripts/cache_warmer.py`, `backend/tests/test_regression_signal_and_cache.py`
- Modify if frontend assets change: `index.html`, `sw.js`, `backend/config.py`

- [ ] **Step 1: Run final local verification**

Run:
```bash
"/Users/leijiang/.workbuddy/binaries/node/versions/22.12.0/bin/node" --check "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/pages/_components.js"
PYTHONPATH="/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend" \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/.venv/bin/python" -m pytest \
  "/Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/tests/test_regression_signal_and_cache.py" \
  -k "longterm or cache_warmer or stock_modal" -q
```
Expected: PASS

- [ ] **Step 2: If frontend JS changed, bump all three versions**

```bash
# pages/_components.js changed → update:
# - sw.js CACHE_NAME
# - index.html asset query versions
# - backend/config.py APP_VERSION
```

- [ ] **Step 3: Deploy safely**

Use targeted deployment or rsync only the touched files to `/opt/moneybag/`, then restart:
```bash
sudo systemctl restart moneybag
sudo systemctl is-active moneybag
```

- [ ] **Step 4: Verify live behavior**

Run:
```bash
curl -s --max-time 20 "http://150.158.47.189:8000/api/health"
curl -s --max-time 40 "http://150.158.47.189:8000/api/longterm/funds?userId=LeiJiang"
curl -s --max-time 40 "http://150.158.47.189:8000/api/fund/detail/013466?userId=LeiJiang"
curl -s --max-time 20 "http://150.158.47.189:8000/api/stock-basic/600809"
curl -s --max-time 20 "http://150.158.47.189:8000/api/stock/financials/600809"
```
Expected: health OK, longterm detail no longer times out, stock detail enrichment endpoints return data.

- [ ] **Step 5: Commit**

```bash
git add pages/_components.js backend/api/fund_detail.py backend/scripts/cache_warmer.py backend/tests/test_regression_signal_and_cache.py index.html sw.js backend/config.py
git commit -m "fix: restore longterm detail popup data"
```
