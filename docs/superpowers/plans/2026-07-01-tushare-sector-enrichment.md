# Tushare 行业资金流与家数补强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 MoneyBag 的 Tushare 行业主链补上行业资金流、上涨家数/下跌家数能力，尽量减少对 AKShare 的依赖。

**Architecture:** 继续保留 `sw_daily` 作为行业指数主链，再通过 `index_member_all + moneyflow_dc` 聚合行业成分股，补齐 `净流入/上涨家数/下跌家数`；若 `moneyflow_dc` 不可用，则降级为 `daily + moneyflow` 的组合聚合。最终统一由 `services.tushare_data.get_sw_sector_daily()` 输出兼容 `sector_rotation` 的字段。

**Tech Stack:** Python 3.11, Tushare `_call_tushare`, pytest

---

### Task 1: 梳理并固化 Tushare 行业补强入口

**Files:**
- Modify: `backend/services/tushare_data.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 写失败测试，要求行业主链包含补强字段**

```python
def test_get_sw_sector_daily_enriches_flow_and_breadth_from_constituents(monkeypatch):
    ...
    rows = tushare_data.get_sw_sector_daily(trade_date="20260701", level="L1")
    assert rows[0]["净流入"] == 120.0
    assert rows[0]["上涨家数"] == 2
    assert rows[0]["下跌家数"] == 1
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k enriches_flow_and_breadth -q`
Expected: FAIL，提示 `净流入` / `上涨家数` / `下跌家数` 缺失。

- [ ] **Step 3: 实现最小补强逻辑**

```python
def _get_sw_sector_constituents(level: str = "L1") -> dict:
    ...

def _get_stock_snapshot_for_sector_enrichment(trade_date: str) -> dict:
    ...

def _enrich_sw_sector_rows(rows: list, level: str = "L1") -> list:
    ...
```

- [ ] **Step 4: 重跑测试确认 GREEN**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k enriches_flow_and_breadth -q`
Expected: PASS

### Task 2: 保证降级链也能吃到补强后的统一字段

**Files:**
- Modify: `backend/services/tushare_fallback.py`
- Modify: `backend/services/sector_rotation.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 写失败测试，要求 sector_rotation 直接使用补强字段**

```python
def test_sector_ranking_prefers_enriched_tushare_counts_and_flow(monkeypatch):
    ...
    result = sector_rotation.get_sector_ranking()
    assert result["top_inflow"][0]["net_inflow"] == 120.0
    assert result["market_breadth"]["up"] == 5
    assert result["market_breadth"]["down"] == 2
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k enriched_tushare_counts_and_flow -q`
Expected: FAIL，提示 `top_inflow` 为空或 breadth 不正确。

- [ ] **Step 3: 保持 `tushare_fallback` / `sector_rotation` 复用统一 helper，不新增分叉入口**

```python
result = get_sw_sector_daily(trade_date=trade_date, level="L1")
```

- [ ] **Step 4: 重跑定向测试确认 GREEN**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k "enriched_tushare_counts_and_flow or tushare_primary_sector_daily" -q`
Expected: PASS

### Task 3: 语法校验、完整回归、部署与线上验证

**Files:**
- Modify: `backend/services/tushare_data.py`
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Modify: `backend/services/tushare_fallback.py`（如有必要）
- Modify: `backend/services/sector_rotation.py`（如有必要）

- [ ] **Step 1: 跑定向回归**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k "sector or tushare" -q`
Expected: PASS

- [ ] **Step 2: 跑完整回归文件**

Run: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -q`
Expected: PASS

- [ ] **Step 3: 语法检查**

Run: `python3 -m py_compile backend/services/tushare_data.py backend/services/tushare_fallback.py backend/services/sector_rotation.py backend/tests/test_regression_signal_and_cache.py`
Expected: no output

- [ ] **Step 4: 上传并在服务器验证**

Run:
```bash
rsync -avz -e "ssh -i ~/.ssh/id_ed25519" \
  backend/services/tushare_data.py \
  backend/services/tushare_fallback.py \
  backend/services/sector_rotation.py \
  backend/tests/test_regression_signal_and_cache.py \
  ubuntu@150.158.47.189:/opt/moneybag/backend/
```
Expected: files uploaded

- [ ] **Step 5: 服务器分发、重启、验证**

Run:
```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 'set -e && \
  mv -f /opt/moneybag/backend/tushare_data.py /opt/moneybag/backend/services/tushare_data.py && \
  mv -f /opt/moneybag/backend/tushare_fallback.py /opt/moneybag/backend/services/tushare_fallback.py && \
  mv -f /opt/moneybag/backend/sector_rotation.py /opt/moneybag/backend/services/sector_rotation.py && \
  mv -f /opt/moneybag/backend/test_regression_signal_and_cache.py /opt/moneybag/backend/tests/test_regression_signal_and_cache.py && \
  sudo chown ubuntu:ubuntu /opt/moneybag/backend/services/tushare_data.py /opt/moneybag/backend/services/tushare_fallback.py /opt/moneybag/backend/services/sector_rotation.py /opt/moneybag/backend/tests/test_regression_signal_and_cache.py && \
  cd /opt/moneybag/backend && \
  /opt/moneybag/venv/bin/python -m pytest tests/test_regression_signal_and_cache.py -k "sector or tushare" -q && \
  sudo systemctl restart moneybag && \
  sleep 3 && sudo systemctl is-active moneybag'
```
Expected: tests pass, service active
