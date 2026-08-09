# Tushare Sector Entry Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复钱袋子行业轮动里 Tushare 主链行业数据入口不一致问题，让主链和最终降级都统一走项目封装的 Tushare 行业接口。

**Architecture:** 当前 `services/sector_rotation.py` 主链调用 `TusharePrimary.get_sector_daily()`，但该实现实际上返回 4 个宽基指数；最终降级 `_tushare_sw_daily_fallback()` 又直接绕过项目封装手调 `tushare`。本次把行业数据入口统一下沉到 `services/tushare_data.py`，由一个 helper 负责 `index_classify + sw_daily` 组合查询，再让 `tushare_fallback.py` 与 `sector_rotation.py` 共同复用。

**Tech Stack:** Python 3.11, pytest, FastAPI backend, Tushare `_call_tushare` wrapper, MoneyBag regression suite

---

### Task 1: 先写失败测试锁定主链入口问题

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 新增失败测试，证明 `TusharePrimary.get_sector_daily()` 目前不是行业主链格式**

```python
def test_tushare_primary_sector_daily_uses_unified_sw_industry_chain(monkeypatch):
    import types
    import services.tushare_fallback as tushare_fallback
    import services.tushare_data as tushare_data

    classifications = [
        {"index_code": f"8010{i:02d}.SI", "industry_name": f"行业{i}"}
        for i in range(12)
    ]
    sample_rows = [
        {
            "ts_code": item["index_code"],
            "trade_date": "20260701",
            "name": item["industry_name"],
            "pct_change": round(6.0 - idx * 0.2, 2),
            "amount": 1000 + idx * 10,
        }
        for idx, item in enumerate(classifications)
    ]

    monkeypatch.setattr(tushare_data, "is_configured", lambda: True)
    monkeypatch.setattr(tushare_data, "get_index_classify", lambda level="L1": classifications)
    monkeypatch.setattr(tushare_data, "_call_tushare", lambda api_name, params, fields="": sample_rows)

    obj = tushare_fallback.TusharePrimary.__new__(tushare_fallback.TusharePrimary)
    obj._pro = types.SimpleNamespace(index_daily=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call index_daily")))

    result = obj.get_sector_daily(trade_date="20260701")

    assert len(result) >= 10
    assert result[0]["板块"] == "行业0"
    assert "涨跌幅" in result[0]
    assert result[0]["source"] == "tushare"
```

- [ ] **Step 2: 跑定向测试，确认旧实现失败**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k tushare_primary_sector_daily_uses_unified_sw_industry_chain -q`
Expected: FAIL，当前实现仍走 `_pro.index_daily()` 宽基指数路径，返回数量/字段都不符合行业主链预期。

### Task 2: 统一行业 Tushare 入口

**Files:**
- Modify: `backend/services/tushare_data.py`
- Modify: `backend/services/tushare_fallback.py`
- Modify: `backend/services/sector_rotation.py`

- [ ] **Step 1: 在 `tushare_data.py` 增加统一 helper，封装 `index_classify + sw_daily`**

```python
def get_sw_sector_daily(trade_date: str = "", level: str = "L1") -> list:
    ...
```

要求：
- 先 `is_configured()`，再调用 `_call_tushare()`
- 通过 `get_index_classify(level)` 拿申万行业列表
- 用 `_call_tushare("sw_daily", ...)` 查最近可用交易日数据
- 输出统一成 `{"板块", "涨跌幅", "总成交额", "代码", "trade_date", "source"}` 格式

- [ ] **Step 2: 让 `TusharePrimary.get_sector_daily()` 直接复用统一 helper**

```python
from services.tushare_data import get_sw_sector_daily

def get_sector_daily(self, trade_date: str = "") -> Optional[List[Dict]]:
    rows = get_sw_sector_daily(trade_date=trade_date, level="L1")
    return rows or None
```

- [ ] **Step 3: 让 `sector_rotation.py` 的最终 Tushare 降级也复用同一个 helper**

```python
from services.tushare_data import get_sw_sector_daily

def _tushare_sw_daily_fallback():
    rows = get_sw_sector_daily(level="L1")
    if not rows:
        return None
    return pd.DataFrame(rows)
```

### Task 3: 回归验证并确认行业轮动主链切回 Tushare

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Modify: `backend/services/tushare_data.py`
- Modify: `backend/services/tushare_fallback.py`
- Modify: `backend/services/sector_rotation.py`

- [ ] **Step 1: 跑定向回归，确认新测试通过**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k "tushare_primary_sector_daily_uses_unified_sw_industry_chain or sector_ranking" -q`
Expected: PASS

- [ ] **Step 2: 跑完整回归文件**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -q`
Expected: PASS

- [ ] **Step 3: 跑语法检查**

Run: `"/Users/leijiang/.workbuddy/binaries/python/versions/3.11.9/bin/python3" -m py_compile backend/services/tushare_data.py backend/services/tushare_fallback.py backend/services/sector_rotation.py backend/tests/test_regression_signal_and_cache.py`
Expected: no output

- [ ] **Step 4: 输出结果结论并补工作记忆/overview**

记录：
- 行业 Tushare 主链之前其实不是行业链，而是 4 个宽基指数 + 直连 `tushare` 的分叉入口
- 现在统一为 `services.tushare_data.get_sw_sector_daily()` 单一入口
- `sector_rotation` 主链优先使用统一 Tushare 行业数据，AKShare 仅在 Tushare 无数据时降级
