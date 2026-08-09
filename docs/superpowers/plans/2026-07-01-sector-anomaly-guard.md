# 晨报行业涨幅合理性兜底 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给晨报行业热点增加固定阈值的异常涨幅过滤，避免第三方源抽风时把离谱行业涨幅透传到晨报。

**Architecture:** 在 `services/sector_rotation.py` 源头过滤 `top_gainers/top_losers` 中异常的 `change_pct`，并返回是否触发兜底的元信息；消费方继续读现有字段，不需要知道异常清洗细节。若过滤后有效行业不足，则返回降级结果，让晨报展示层自然回退为安全文案。

**Tech Stack:** Python 3.11, pytest, FastAPI backend, MoneyBag nightly briefing pipeline

---

### Task 1: 为行业异常过滤写回归测试

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: Write the failing test**

```python
def test_sector_ranking_filters_out_anomalous_change_pct(monkeypatch):
    import pandas as pd
    import services.sector_rotation as sector_rotation

    sample = pd.DataFrame([
        {"板块": "医疗器械", "涨跌幅": 211.7, "总成交额": 120.5, "净流入": 8.6, "上涨家数": 35, "下跌家数": 12, "领涨股": "龙头A", "领涨股-涨跌幅": 21.17},
        {"板块": "半导体", "涨跌幅": 6.32, "总成交额": 6224.73, "净流入": 314.27, "上涨家数": 158, "下跌家数": 21, "领涨股": "格科微", "领涨股-涨跌幅": 20.02},
        {"板块": "光学光电子", "涨跌幅": 5.90, "总成交额": 1533.38, "净流入": 104.95, "上涨家数": 96, "下跌家数": 12, "领涨股": "联建光电", "领涨股-涨跌幅": 20.04},
        {"板块": "军工电子", "涨跌幅": 5.04, "总成交额": 448.81, "净流入": 40.46, "上涨家数": 61, "下跌家数": 1, "领涨股": "景嘉微", "领涨股-涨跌幅": 14.01},
    ])

    monkeypatch.setattr(sector_rotation, "_get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_set_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_fetch_sector_dataframe", lambda: (sample.copy(), "akshare"))

    result = sector_rotation.get_sector_ranking()

    assert result["available"] is True
    assert result["anomaly_guard_triggered"] is True
    assert result["filtered_anomaly_count"] == 1
    assert [item["name"] for item in result["top_gainers"][:3]] == ["半导体", "光学光电子", "军工电子"]
    assert all(abs(item["change_pct"]) <= 15 for item in result["top_gainers"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k filters_out_anomalous_change_pct -q`
Expected: FAIL，因为当前实现还不会过滤 `211.7%` 这种异常值，也不会返回 `anomaly_guard_triggered` / `filtered_anomaly_count`。

- [ ] **Step 3: Write minimal implementation**

```python
MAX_REASONABLE_SECTOR_CHANGE_PCT = 15.0


def _is_reasonable_sector_change(change_pct: object) -> bool:
    try:
        value = float(change_pct)
    except (TypeError, ValueError):
        return False
    return abs(value) <= MAX_REASONABLE_SECTOR_CHANGE_PCT
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k filters_out_anomalous_change_pct -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_regression_signal_and_cache.py backend/services/sector_rotation.py
git commit -m "fix: guard abnormal sector change data"
```

### Task 2: 为不足样本的降级结果写回归测试

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Modify: `backend/services/sector_rotation.py`

- [ ] **Step 1: Write the failing test**

```python
def test_sector_ranking_degrades_when_too_few_valid_sectors(monkeypatch):
    import pandas as pd
    import services.sector_rotation as sector_rotation

    sample = pd.DataFrame([
        {"板块": "医疗器械", "涨跌幅": 211.7, "总成交额": 120.5, "净流入": 8.6, "上涨家数": 35, "下跌家数": 12, "领涨股": "龙头A", "领涨股-涨跌幅": 21.17},
        {"板块": "化学制药", "涨跌幅": 30.0, "总成交额": 98.2, "净流入": 6.4, "上涨家数": 20, "下跌家数": 18, "领涨股": "龙头B", "领涨股-涨跌幅": 30.0},
        {"板块": "医疗服务", "涨跌幅": 20.0, "总成交额": 80.0, "净流入": 5.2, "上涨家数": 10, "下跌家数": 10, "领涨股": "龙头C", "领涨股-涨跌幅": 12.0},
        {"板块": "半导体", "涨跌幅": 6.32, "总成交额": 6224.73, "净流入": 314.27, "上涨家数": 158, "下跌家数": 21, "领涨股": "格科微", "领涨股-涨跌幅": 20.02},
    ])

    monkeypatch.setattr(sector_rotation, "_get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_set_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_fetch_sector_dataframe", lambda: (sample.copy(), "akshare"))

    result = sector_rotation.get_sector_ranking()

    assert result["available"] is False
    assert result["top_gainers"] == []
    assert result["error"] == "暂无明显热点板块（行业数据异常已过滤）"
    assert result["filtered_anomaly_count"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k too_few_valid_sectors -q`
Expected: FAIL，因为当前实现仍会返回剩余单个行业，不会降级成统一兜底结果。

- [ ] **Step 3: Write minimal implementation**

```python
MIN_VALID_SECTORS = 3

if len(top_gainers) < MIN_VALID_SECTORS:
    result = {
        "available": False,
        "source": source,
        "top_gainers": [],
        "top_losers": [],
        "breadth": {},
        "rotation_signal": "neutral",
        "error": "暂无明显热点板块（行业数据异常已过滤）",
        "anomaly_guard_triggered": filtered_anomaly_count > 0,
        "filtered_anomaly_count": filtered_anomaly_count,
    }
    _set_cached("sector_ranking", result)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k too_few_valid_sectors -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_regression_signal_and_cache.py backend/services/sector_rotation.py
git commit -m "fix: degrade sector summary on anomalous data"
```

### Task 3: 完整验证并部署

**Files:**
- Modify: `backend/services/sector_rotation.py`
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Verify: `/opt/moneybag/backend/services/sector_rotation.py`

- [ ] **Step 1: Run full local regression**

```bash
PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -q
```

Expected: PASS，包含旧的列映射回归和新的异常兜底回归。

- [ ] **Step 2: Syntax-check changed module**

```bash
/Users/leijiang/.workbuddy/binaries/python/versions/3.11.9/bin/python3 -m py_compile backend/services/sector_rotation.py backend/tests/test_regression_signal_and_cache.py
```

Expected: 无输出

- [ ] **Step 3: Deploy to server and run targeted verification**

```bash
rsync -avz -e "ssh -i ~/.ssh/id_ed25519" backend/services/sector_rotation.py backend/tests/test_regression_signal_and_cache.py ubuntu@150.158.47.189:/opt/moneybag/backend/
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 "cd /opt/moneybag/backend && sudo chown ubuntu:ubuntu services/sector_rotation.py tests/test_regression_signal_and_cache.py && sudo systemctl restart moneybag && /opt/moneybag/venv/bin/pytest tests/test_regression_signal_and_cache.py -q"
```

Expected: PASS

- [ ] **Step 4: Rebuild briefing artifact to verify guard output**

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 "cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python - <<'PY'
from services.sector_rotation import get_sector_ranking
res = get_sector_ranking()
print(res.get('anomaly_guard_triggered'), res.get('filtered_anomaly_count'))
print(res.get('top_gainers', [])[:3])
PY"
```

Expected: 正常行情下 `filtered_anomaly_count` 为 `0` 或很小；即使未来源数据抽风，也不会再把超阈值行业塞进 `top_gainers`。

- [ ] **Step 5: Commit**

```bash
git add backend/services/sector_rotation.py backend/tests/test_regression_signal_and_cache.py docs/superpowers/plans/2026-07-01-sector-anomaly-guard.md
git commit -m "fix: add sector anomaly guard"
```
