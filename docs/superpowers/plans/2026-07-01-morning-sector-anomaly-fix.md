# Morning Briefing Sector Anomaly Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复钱袋子晨报里行业热点涨幅异常映射问题，并验证当前“消息截断”未在现行代码上复现。

**Architecture:** 晨报的行业热点来自 `services/sector_rotation.py`。本次只修复列映射逻辑，确保 `涨跌幅` 不会被 `领涨股-涨跌幅` 覆盖；同时用最小回归测试锁死这个坑。对“截断”问题先保留证据，不在未复现前盲改。

**Tech Stack:** Python 3.11, pytest, FastAPI backend, AKShare/Tushare data adapters

---

### Task 1: 锁定列映射回归测试

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 写失败测试，证明旧逻辑会把 `change_pct` 指到 `领涨股-涨跌幅`**

```python
def test_sector_ranking_prefers_actual_change_pct_column(monkeypatch):
    import pandas as pd
    import services.sector_rotation as sector_rotation

    sample = pd.DataFrame([
        {"板块": "医疗器械", "涨跌幅": 2.11, "总成交额": 120.5, "净流入": 8.6, "上涨家数": 35, "下跌家数": 12, "领涨股": "某龙头", "领涨股-涨跌幅": 21.17},
        {"板块": "化学制药", "涨跌幅": 1.35, "总成交额": 98.2, "净流入": 6.4, "上涨家数": 20, "下跌家数": 18, "领涨股": "某个股", "领涨股-涨跌幅": 30.0},
    ])

    monkeypatch.setattr(sector_rotation, "_get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(sector_rotation, "_set_cached", lambda *args, **kwargs: None)

    class _FakePrimary:
        def get_sector_daily(self):
            return None

    monkeypatch.setattr(sector_rotation.TusharePrimary, "instance", classmethod(lambda cls: _FakePrimary()))
    monkeypatch.setattr(sector_rotation, "get_industry_board_summary", lambda: sample)

    result = sector_rotation.get_sector_ranking()

    assert result["top_gainers"][0]["name"] == "医疗器械"
    assert result["top_gainers"][0]["change_pct"] == 2.11
    assert result["top_gainers"][0]["leader_chg"] == 21.17
```

- [ ] **Step 2: 运行定向测试，确认当前旧实现会失败**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k sector_ranking_prefers_actual_change_pct_column -q`
Expected: FAIL，`change_pct` 被错误取成 `21.17`。

- [ ] **Step 3: 实现最小修复**

```python
for c in df.columns:
    cl = c.lower().strip()
    if "板块" in c or "行业" in c or cl == "板块":
        col_map["name"] = c
    elif c == "涨跌幅":
        col_map["change_pct"] = c
    elif "净流入" in c:
        col_map["net_inflow"] = c
    ...
    elif "领涨股-涨跌幅" in c:
        col_map["leader_chg"] = c
```

- [ ] **Step 4: 重跑定向测试，确认通过**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k sector_ranking_prefers_actual_change_pct_column -q`
Expected: PASS

### Task 2: 验证当前晨报未发生真实截断

**Files:**
- Modify: none
- Verify only: server night worker outputs

- [ ] **Step 1: 读取服务器 `products_YYYY-MM-DD.json` 与 `briefings_YYYY-MM-DD.json`**

Run:
```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 "cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python - <<'PY'
from pathlib import Path
import json
from datetime import date
from scripts import night_worker
for kind in ('products', 'briefings'):
    fn = night_worker.NIGHT_LOG_DIR / f'{kind}_{date.today()}.json'
    data = json.loads(fn.read_text(encoding='utf-8'))
    text = data.get('LeiJiang', '')
    print(kind, len(text), text[-400:])
PY"
```
Expected: 长度显著小于 3900，结尾保留完整免责声明。

- [ ] **Step 2: 如果仍无法复现截断，则不写代码，只在结果中说明证据**

Expected: 结论写清楚“当前未复现，不做盲修”。

### Task 3: 部署并复验线上行业热点

**Files:**
- Modify: `backend/services/sector_rotation.py`
- Modify: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 语法检查**

Run: `"/Users/leijiang/.workbuddy/binaries/python/versions/3.11.9/bin/python3" -m py_compile backend/services/sector_rotation.py backend/tests/test_regression_signal_and_cache.py`
Expected: no output

- [ ] **Step 2: 跑完整回归文件**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -q`
Expected: PASS

- [ ] **Step 3: 上传到服务器并跑定向/完整验证**

Run:
```bash
rsync -avz -e "ssh -i ~/.ssh/id_ed25519" backend/services/sector_rotation.py backend/tests/test_regression_signal_and_cache.py ubuntu@150.158.47.189:/opt/moneybag/backend/
```
然后在服务器把文件放回正确目录、修正属主、运行：
```bash
cd /opt/moneybag/backend
/opt/moneybag/venv/bin/pytest tests/test_regression_signal_and_cache.py -k sector_ranking_prefers_actual_change_pct_column -q
/opt/moneybag/venv/bin/python - <<'PY'
from services.sector_rotation import get_sector_ranking
print(get_sector_ranking().get('top_gainers', [])[:3])
PY
```
Expected: 医疗器械/电网设备等行业涨幅回到合理区间，不再是 20%/200% 级别错值。

- [ ] **Step 4: 若需要重启服务再验证健康检查**

Run: `curl -s --max-time 8 http://150.158.47.189:8000/api/health`
Expected: `status=ok`

- [ ] **Step 5: 更新工作记忆和 overview**

记录：
- 行业涨幅异常根因是服务器旧版 `sector_rotation.py` 列匹配过宽
- 当前“晨报截断”在当日产物中未复现，暂不改代码
