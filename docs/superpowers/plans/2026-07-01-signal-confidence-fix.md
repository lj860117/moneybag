# Signal Confidence Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除钱袋子周度自检里 13维信号 `confidence < 20` 的告警，并确保线上正式代码与本地修复逻辑一致。

**Architecture:** 先按 systematic-debugging 定位根因，确认是线上 `services/signal.py` 仍停留在旧版 `confidence = min(abs(final_score), 100)` 逻辑，而本地仓库已包含更合理的一致性+强度置信度算法。随后补一条回归测试保护 HOLD/中性分数场景下的最低置信度，再把本地文件部署到服务器并重跑周度自检。

**Tech Stack:** Python, pytest, FastAPI backend, systemd, rsync, SSH

---

### Task 1: 复现并锁定根因

**Files:**
- Modify: `backend/services/signal.py`（仅核对，不改）
- Test: `backend/use_cases/self_audit.py`（仅核对，不改）

- [ ] **Step 1: 用线上正式解释器复现告警**

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 "cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python - <<'PY'
from services.signal import generate_daily_signal
sig = generate_daily_signal()
print(sig.get('overall'), sig.get('score'), sig.get('confidence'))
PY"
```

- [ ] **Step 2: 对比本地/线上 `signal.py` 的 confidence 实现**

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 "grep -n 'signal\["confidence"\]' /opt/moneybag/backend/services/signal.py"
grep -n 'confidence = round(consistency \* 60 + strength \* 40, 1)' /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/services/signal.py
```

- [ ] **Step 3: 记录单一假设**

```text
我认为根因是：服务器仍在运行旧版 signal.py，HOLD 场景直接使用 abs(final_score) 作为 confidence，导致 score≈17 时 confidence≈17；本地仓库已修复，但未部署到线上。
```

### Task 2: 补回归测试保护修复逻辑

**Files:**
- Modify: `backend/tests/test_regression_signal_and_cache.py`
- Test: `backend/tests/test_regression_signal_and_cache.py`

- [ ] **Step 1: 写回归测试，构造中性偏多但非极强信号**

```python
def test_generate_daily_signal_keeps_hold_confidence_above_floor(monkeypatch):
    import services.signal as signal_module

    monkeypatch.setattr(signal_module, "get_technical_indicators", lambda: {
        "rsi": 58,
        "macd": {"trend": "MACD金叉但仍在0轴下方，反弹信号（非趋势反转）"},
        "bollinger": {"position": "价格在中轨上方，偏强但注意回调"},
    })
    monkeypatch.setattr(signal_module, "get_valuation_percentile", lambda: {"percentile": 19.3, "current_pe": 19.61})
    monkeypatch.setattr(signal_module, "get_dividend_yield", lambda: {"available": True, "percentile": 20, "dividend_yield": 0.72})
    monkeypatch.setattr(signal_module, "get_treasury_yield", lambda: {"available": True, "yield_10y": 1.733, "equity_premium": "股市有吸引力"})
    monkeypatch.setattr(signal_module, "get_northbound_flow", lambda: {"available": True, "net_flow_5d": 0, "net_flow_today": 0})
    monkeypatch.setattr(signal_module, "get_margin_trading", lambda: {"available": True, "margin_change_5d": 1.03, "margin_balance": 9278.49})
    monkeypatch.setattr(signal_module, "get_shibor", lambda: {"available": True, "overnight": 1.36, "trend": "流动性平稳"})
    monkeypatch.setattr(signal_module, "get_fear_greed_index", lambda: {"score": 50})
    monkeypatch.setattr(signal_module, "get_news_sentiment_score", lambda: {"available": True, "score": 0, "level": "中性", "source": "test"})
    monkeypatch.setattr(signal_module, "get_macro_calendar", lambda: [{"name": "PMI", "value": "50.3"}, {"name": "M2", "value": "8.6%"}])
    monkeypatch.setattr(signal_module, "get_market_news", lambda: [])

    class FakeGeo:
        @staticmethod
        def get_geopolitical_risk_score():
            return {"available": True, "score": 0, "level": "low", "top_events": []}
```

- [ ] **Step 2: 先运行单测确认旧逻辑会失败**

Run: `pytest backend/tests/test_regression_signal_and_cache.py -k hold_confidence -q`
Expected: old server logic would produce confidence < 20; current fixed local code should pass after deployment sync.

- [ ] **Step 3: 如需最小实现，仅保持本地已存在的新公式，不再引入额外改动**

```python
confidence = round(consistency * 60 + strength * 40, 1)
confidence = max(confidence, 30.0)
```

- [ ] **Step 4: 运行回归测试**

Run: `pytest backend/tests/test_regression_signal_and_cache.py -q`
Expected: PASS

### Task 3: 部署并验证线上告警消除

**Files:**
- Modify: `/opt/moneybag/backend/services/signal.py`（通过部署覆盖）
- Test: `backend/use_cases/self_audit.py`

- [ ] **Step 1: 部署本地 `signal.py` 到服务器**

```bash
rsync -avz -e "ssh -i ~/.ssh/id_ed25519" \
  /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/services/signal.py \
  ubuntu@150.158.47.189:/opt/moneybag/backend/services/signal.py
```

- [ ] **Step 2: 重启服务**

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 "sudo systemctl restart moneybag && sudo systemctl is-active moneybag"
```

- [ ] **Step 3: 重跑周度自检相关验证**

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@150.158.47.189 "cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python - <<'PY'
from use_cases.self_audit import run_smoke_tests
print(next(r for r in run_smoke_tests() if r['name'] == '13维信号'))
PY"
```

- [ ] **Step 4: 健康检查**

Run: `curl -s --max-time 8 http://150.158.47.189:8000/api/health`
Expected: `{"status":"ok", ...}`

- [ ] **Step 5: 记录记忆**

```bash
# 追加到当前会话 workspace 的记忆日志
```
