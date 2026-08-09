# 长持基金刷新崩溃修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复钱袋子资讯页“长持基金”接口 500，恢复手机端长持内容正常刷新，并确认接口会回写 per-user 硬盘缓存。

**Architecture:** 根因已定位为 `backend/services/longterm_screen.py` 在 fallback 分支里局部 `from datetime import datetime`，把模块级 `datetime` 变成函数局部变量；当函数走到结果组装时访问 `datetime.now()` 会触发 `UnboundLocalError`，导致 `/api/longterm/funds` 直接 500。修复方案是消除局部遮蔽，并补一个覆盖 fallback 路径的回归测试，最后验证本地接口、缓存写盘和线上接口。

**Tech Stack:** Python 3.11, pytest, FastAPI route helpers, MoneyBag cache files

---

### Task 1: 补回归测试

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 写失败测试**

```python
def test_screen_longterm_funds_fallback_keeps_module_datetime(tmp_path, monkeypatch):
    import services.longterm_screen as longterm_screen

    monkeypatch.setattr(longterm_screen, "DATA_DIR", tmp_path)
    monkeypatch.setattr(longterm_screen, "_CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(longterm_screen, "_FUND_CACHE_FILE", (tmp_path / "_cache" / "longterm_funds.json"))
    longterm_screen._CACHE_DIR.mkdir(parents=True, exist_ok=True)

    rank_file = tmp_path / "fund_rank_ts.json"
    rank_file.write_text(
        json.dumps({
            "ranks": {
                "all": [
                    {
                        "code": "000001",
                        "ts_code": "000001.OF",
                        "name": "示例成长混合",
                        "return_1y": 12.0,
                        "return_3y": 36.0,
                        "list_date": "20180101",
                    }
                ]
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(longterm_screen, "_cache_valid", lambda *_args, **_kwargs: False)

    fake_tushare = types.ModuleType("services.tushare_data")
    fake_tushare.is_configured = lambda: False
    fake_tushare._call_tushare = lambda *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "services.tushare_data", fake_tushare)

    fake_industry = types.ModuleType("services.industry_templates")
    fake_industry.get_fund_industry = lambda _name: {"tag": "混合", "desc": "测试行业描述"}
    monkeypatch.setitem(sys.modules, "services.industry_templates", fake_industry)

    fake_akshare = types.ModuleType("akshare")
    fake_akshare.fund_rating_all = lambda: None
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    result = longterm_screen.screen_longterm_funds(force=False)

    assert result["funds"]
    assert result["generated_at"]
    assert (tmp_path / "_cache" / "longterm_funds.json").exists()
```

- [ ] **Step 2: 跑单测确认先红**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k longterm_funds_fallback -q`
Expected: FAIL with `UnboundLocalError: cannot access local variable 'datetime'`

### Task 2: 修复 fallback 遮蔽

**Files:**
- Modify: `backend/services/longterm_screen.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 改最小实现**

```python
# before
from datetime import datetime
ld = datetime.strptime(str(list_date_str)[:8], "%Y%m%d")
fund_age_years = (datetime.now() - ld).days / 365

# after
ld = datetime.strptime(str(list_date_str)[:8], "%Y%m%d")
fund_age_years = (datetime.now() - ld).days / 365
```

- [ ] **Step 2: 如果还有同类局部导入，一并消掉同函数内的遮蔽**

Run: `python3 - <<'PY'
from pathlib import Path
fp = Path('backend/services/longterm_screen.py')
for i, line in enumerate(fp.read_text(encoding='utf-8').splitlines(), start=1):
    if 'from datetime import datetime' in line:
        print(i, line)
PY`
Expected: no function-local `from datetime import datetime` remains inside `screen_longterm_funds`

### Task 3: 验证本地接口与写盘

**Files:**
- Test: `backend/tests/test_regression_signal_and_cache.py`
- Verify: `backend/services/longterm_screen.py`, `backend/api/signals.py`

- [ ] **Step 1: 重跑单测确认转绿**

Run: `PYTHONPATH="$PWD/backend" .venv/bin/python -m pytest backend/tests/test_regression_signal_and_cache.py -k longterm_funds_fallback -q`
Expected: PASS

- [ ] **Step 2: 本地直接调用接口 helper**

Run: `"$PWD/.venv/bin/python" - <<'PY'
import sys
sys.path.insert(0, 'backend')
from api.signals import api_longterm_funds
res = api_longterm_funds(force=False, userId='LeiJiang')
print('funds=', len(res.get('funds', [])))
print('generated_at=', res.get('generated_at'))
PY`
Expected: prints non-zero `funds=` and a `generated_at` timestamp, no traceback

- [ ] **Step 3: 检查 per-user 缓存是否写盘**

Run: `python3 - <<'PY'
from pathlib import Path
fp = Path('data/_cache/longterm_funds_LeiJiang.json')
print(fp.exists(), fp)
PY`
Expected: `True data/_cache/longterm_funds_LeiJiang.json`

### Task 4: 部署并回归线上

**Files:**
- Modify/Deploy: `backend/services/longterm_screen.py`
- Verify: remote MoneyBag API

- [ ] **Step 1: 语法检查**

Run: `.venv/bin/python -m py_compile backend/services/longterm_screen.py`
Expected: no output

- [ ] **Step 2: 部署最小改动到服务器**

Run: `bash deploy.sh`
Expected: deploy script completes and restarts `moneybag`

- [ ] **Step 3: 线上验证接口**

Run: `curl -s --max-time 25 "http://150.158.47.189:8000/api/longterm/funds?userId=LeiJiang"`
Expected: JSON payload with `funds` array, not `Internal Server Error`

- [ ] **Step 4: 线上验证长持股票不回归**

Run: `curl -s --max-time 25 "http://150.158.47.189:8000/api/longterm/stocks?userId=LeiJiang"`
Expected: still returns JSON payload with `stocks`
