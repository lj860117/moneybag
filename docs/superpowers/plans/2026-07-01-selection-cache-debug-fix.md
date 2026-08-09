# 选股 K线 / 选基详情 / 持仓诊断缓存链路修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复选股 K 线不可见、选基详情渲染失败、以及选基持仓诊断等待过久三个问题，并让它们真正命中已存在的预热/硬盘缓存链路。

**Architecture:** 前端改为走正确的数据入口并避免绕过预热缓存；后端为 K 线与用户态基金详情补齐文件缓存/预热；同时用回归测试锁住“股票不要走基金净值接口”“持仓详情渲染不再引用未定义变量”“持仓诊断优先命中预热扫描缓存”这三条不变量。

**Tech Stack:** 原生 JS、FastAPI、pytest、MoneyBag cache_warmer、文件缓存。

---

### Task 1: 锁定 K 线错误入口与缓存缺口

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Modify: `pages/insight-stock.js`
- Modify: `pages/analysis.js`
- Modify: `backend/api/fund_detail.py`
- Modify: `backend/scripts/cache_warmer.py`

- [ ] **Step 1: 写失败回归测试，证明股票 K 线不能再走基金净值接口**

```python
def test_stock_pick_kline_uses_chart_endpoint_not_fund_nav_history():
    page = Path("pages/insight-stock.js").read_text(encoding="utf-8")
    assert "_showFundKlineModal(" not in page
    assert "showFundChart(" in page
```

- [ ] **Step 2: 运行测试，确认先红**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k stock_pick_kline_uses_chart_endpoint_not_fund_nav_history -q`
Expected: FAIL，当前页面仍引用 `_showFundKlineModal`。

- [ ] **Step 3: 最小改动修正股票入口**

```javascript
<button onclick="showFundChart('${cleanCode}')">📈 K线</button>
```

把 `pages/insight-stock.js` 和 `pages/analysis.js` 里用于股票的 K 线按钮统一改到 `showFundChart()`。

- [ ] **Step 4: 给 `/api/fund/nav-history/{code}` 补文件缓存与预热可复用能力**

```python
cache_key = f"nav_hist_{code}_{days}"
cached = _get_cached(cache_key, allow_stale=True)
if cached:
    return {**cached, "cached": True}
```

不要只留进程内 `_NAV_HISTORY_CACHE`；要复用已有 detail 文件缓存目录，保证重启后仍能命中。

- [ ] **Step 5: 在预热脚本里补 K 线预热**

```python
_rq.get(f"http://127.0.0.1:8000/api/chart/{code}?period=1y&userId={uid}", timeout=30)
_rq.get(f"http://127.0.0.1:8000/api/fund/nav-history/{code}?days=90", timeout=20)
```

对持仓基金与选基 TOP 基金补预热；股票榜单只预热 `/api/chart/{code}`。

- [ ] **Step 6: 重跑测试确认转绿**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k stock_pick_kline_uses_chart_endpoint_not_fund_nav_history -q`
Expected: PASS

### Task 2: 修复基金详情渲染失败与用户态详情预热缺口

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Modify: `pages/_components.js`
- Modify: `backend/scripts/cache_warmer.py`

- [ ] **Step 1: 写失败回归测试，锁住前端不再引用未定义的 `isMyHolding`**

```python
def test_fund_detail_component_declares_is_my_holding_guard():
    page = Path("pages/_components.js").read_text(encoding="utf-8")
    assert "const isMyHolding = !!d.holding_relation" in page
```

- [ ] **Step 2: 运行测试，确认先红**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k fund_detail_component_declares_is_my_holding_guard -q`
Expected: FAIL，当前没有声明该变量。

- [ ] **Step 3: 前端补显式守卫，避免渲染期 ReferenceError**

```javascript
const isMyHolding = !!d.holding_relation;
```

放在 `showFundDetailModal()` 取到 `d` 后、首次使用之前。

- [ ] **Step 4: 预热脚本补 userId 版 `fund/detail`**

```python
_rq.get(f"http://127.0.0.1:8000/api/fund/detail/{code}?userId={uid}", timeout=30)
```

不要只预热无 `userId` 的通用详情，否则前端真正请求的 `fund_detail_{code}_{uid}` 仍然冷启动。

- [ ] **Step 5: 重跑测试确认转绿**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k fund_detail_component_declares_is_my_holding_guard -q`
Expected: PASS

### Task 3: 让持仓诊断真正命中预热缓存

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Modify: `pages/insight-fund.js`
- Optional Modify: `backend/api/holdings.py`

- [ ] **Step 1: 写失败回归测试，锁住前端优先读取预热扫描缓存而不是逐只实时请求**

```python
def test_my_holdings_diag_prefers_scan_cache_endpoint():
    page = Path("pages/insight-fund.js").read_text(encoding="utf-8")
    assert "/fund-holdings/scan?" in page
```

- [ ] **Step 2: 运行测试，确认先红**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k my_holdings_diag_prefers_scan_cache_endpoint -q`
Expected: FAIL，当前代码是 `Promise.all(codes.map(fetch realtime))`。

- [ ] **Step 3: 最小实现，把逐只 realtime 改成扫描缓存 + enrich 并行**

```javascript
Promise.all([
  fetch(API_BASE + '/fund-holdings/scan?' + getProfileParam()).then(r => r.ok ? r.json() : { holdings: [] }),
  fetch(API_BASE + '/fund-holdings/enrich?userId=' + getProfileId()).then(r => r.ok ? r.json() : { funds: [] }),
])
```

再用 `scanRes.holdings` 构建 `navMap/riskMap`，不要逐只调用 `/fund-holdings/realtime/{code}`。

- [ ] **Step 4: 如有必要给扫描接口补 `from_cache` 透传和字段兼容**

```python
cached["from_cache"] = True
return cached
```

确保前端能直接知道是否命中预热缓存。

- [ ] **Step 5: 重跑测试确认转绿**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k my_holdings_diag_prefers_scan_cache_endpoint -q`
Expected: PASS

### Task 4: 完整验证、部署、线上复验

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Modify: `pages/insight-stock.js`
- Modify: `pages/analysis.js`
- Modify: `pages/_components.js`
- Modify: `pages/insight-fund.js`
- Modify: `backend/api/fund_detail.py`
- Modify: `backend/scripts/cache_warmer.py`

- [ ] **Step 1: 跑定向回归**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k "kline or fund_detail_component_declares_is_my_holding_guard or my_holdings_diag_prefers_scan_cache_endpoint" -q`
Expected: 全部 PASS

- [ ] **Step 2: 跑完整回归文件**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -q`
Expected: 全部 PASS

- [ ] **Step 3: 语法检查**

Run: `"/Users/leijiang/.workbuddy/binaries/python/versions/3.11.9/bin/python3" -m py_compile backend/api/fund_detail.py backend/scripts/cache_warmer.py`
Expected: 无输出

Run: `"/Users/leijiang/.workbuddy/binaries/node/versions/22.12.0/bin/node" --check pages/_components.js && "/Users/leijiang/.workbuddy/binaries/node/versions/22.12.0/bin/node" --check pages/insight-fund.js && "/Users/leijiang/.workbuddy/binaries/node/versions/22.12.0/bin/node" --check pages/insight-stock.js && "/Users/leijiang/.workbuddy/binaries/node/versions/22.12.0/bin/node" --check pages/analysis.js`
Expected: 无输出

- [ ] **Step 4: 若有前端改动，bump PWA 版本**

```bash
perl -0pi -e "s/moneybag-v\d+-cache/moneybag-v991-cache/g" sw.js
```

- [ ] **Step 5: 部署并线上验证**

Run: `rsync` 上传修改文件到 `ubuntu@150.158.47.189:/opt/moneybag/backend/` 与前端根目录，然后在服务器端 `mv` 到正确位置、`sudo chown ubuntu:ubuntu ...`、`sudo systemctl restart moneybag`。

- [ ] **Step 6: 线上复验**

Run:
```bash
curl -s --max-time 8 http://150.158.47.189:8000/api/health
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 'cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python - <<'"'"'PY'"'"'
from api.fund_detail import fund_nav_history
print(fund_nav_history("016501", 90).get("ok"))
PY'
```

再人工复核三件事：
1. 选股页 K 线能打开
2. 选基详情不再显示“基金详情渲染失败”
3. 持仓诊断首屏明显更快，且命中缓存时能秒出
