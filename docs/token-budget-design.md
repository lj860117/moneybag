# MoneyBag Token 预算控制 & API Key 运维方案

> 📅 2026-04-16 | 配套文档：`MoneyBag-全景设计文档.md` 附录 E.2 / E.3
> 🎯 原则：**不为省钱而省钱**，月度 ¥30 以内完全可接受

---

## 一、DeepSeek 当前定价（2026-04 官方）

| 计费项 | 价格（USD / 百万 token） | 折合人民币 |
|--------|-------------------------|-----------|
| 输入 token（**缓存命中**） | $0.028 | ¥0.20 |
| 输入 token（缓存未命中） | $0.28 | ¥2.03 |
| 输出 token | $0.42 | ¥3.04 |

> **deepseek-chat（V3）和 deepseek-reasoner（R1）定价完全相同**，区别只是 R1 开启思考模式，默认输出更长（32K vs 4K）。

### 关键省钱机制
- **缓存命中**：相同 system prompt + 相似上下文重复调用时，输入成本降到 **1/10**
- 钱袋子凌晨任务的 system prompt 固定 → 天然享受缓存优惠

---

## 二、实际 Token 消耗评估

### 2.1 凌晨任务（每天固定）

| 任务 | 引擎 | 输入 token | 输出 token | 缓存命中率 | 日成本 |
|------|------|-----------|-----------|-----------|--------|
| ① 宏观环境研判 | R1 | ~8K | ~4K | 60% | ¥0.026 |
| ② 地缘政治影响 | R1 | ~10K | ~6K | 50% | ¥0.035 |
| ③ 行业轮动分析 | R1 | ~8K | ~4K | 60% | ¥0.026 |
| ④ 持仓诊断-厉害了哥 | R1 | ~12K | ~8K | 40% | ¥0.054 |
| ⑤ 持仓诊断-部落格里 | R1 | ~12K | ~8K | 40% | ¥0.054 |
| ⑥ 盈利预测解读 | V3 | ~6K | ~2K | 70% | ¥0.009 |
| ⑦ 估值合理性 | V3 | ~6K | ~2K | 70% | ¥0.009 |
| ⑧ 买入候选筛选 | R1 | ~10K | ~6K | 40% | ¥0.042 |
| ⑨ 卖出/减仓检查 | V3 | ~4K | ~1K | 70% | ¥0.005 |
| ⑩ 三情景分析 | R1 | ~10K | ~8K | 30% | ¥0.053 |
| 投资简报生成 | V3 | ~8K | ~3K | 60% | ¥0.015 |
| 外盘+事件检查 | V3 | ~4K | ~2K | 50% | ¥0.010 |
| 早安简报×2 | V3 | ~6K | ~2K | 80% | ¥0.008 |
| **凌晨合计** | | **~104K** | **~56K** | | **¥0.35/天** |

### 2.2 盘中任务（交易日）

| 任务 | 引擎 | 输入 | 输出 | 日成本 |
|------|------|------|------|--------|
| 开盘异动 | V3 | ~4K | ~1K | ¥0.006 |
| 持仓异动扫描 | V3 | ~6K | ~2K | ¥0.010 |
| 午间新闻评估 | V3 | ~4K | ~2K | ¥0.008 |
| 尾盘预判 | V3 | ~4K | ~1K | ¥0.006 |
| 收盘复盘 | V3 | ~6K | ~2K | ¥0.010 |
| **盘中合计** | | **~24K** | **~8K** | **¥0.04/天** |

### 2.3 用户交互（按需）

| 场景 | 引擎 | 估算次数/天 | 平均 token/次 | 日成本 |
|------|------|------------|--------------|--------|
| 日常聊天问答 | V3 | ~8 次 | ~3K入+1K出 | ¥0.05 |
| 深度分析（"帮我看下茅台"） | R1 | ~2 次 | ~8K入+5K出 | ¥0.06 |
| 企微消息回复 | V3 | ~3 次 | ~2K入+1K出 | ¥0.01 |
| **交互合计** | | **~13 次** | | **¥0.12/天** |

### 2.4 周度/月度特殊任务

| 任务 | 频率 | 引擎 | 成本/次 |
|------|------|------|---------|
| V8 复盘-准确率验证 | 每周日 | R1 | ¥0.08 |
| V8 复盘-归因分析 | 每月1日 | R1 | ¥0.15 |
| V9 模拟盘结算 | 每天 | 无LLM | ¥0 |
| 遗传因子进化 | 每周六 | 无LLM | ¥0 |
| **特殊任务月均** | | | **¥0.47/月** |

---

## 三、月度成本汇总

| 项目 | 日成本 | 月成本（30天） | 占比 |
|------|--------|---------------|------|
| 凌晨 R1 分析 | ¥0.29 | ¥8.70 | 51% |
| 凌晨 V3 任务 | ¥0.06 | ¥1.80 | 11% |
| 盘中 V3 任务 | ¥0.04 | ¥0.88 | 5% |
| 用户交互 R1 | ¥0.06 | ¥1.80 | 11% |
| 用户交互 V3 | ¥0.06 | ¥1.80 | 11% |
| 周度/月度复盘 | — | ¥0.47 | 3% |
| **缓冲余量（+30%）** | — | ¥4.63 | — |
| **总计** | **¥0.51** | **≈ ¥20/月** | |

> 🟢 **结论**：正常使用月成本 ~¥15-20，加上异常重试和高频交互日的波动，不会超过 ¥25。远在 ¥30 预算以内。

---

## 四、推荐配置

```python
# config.py — Token 预算配置

# ═══════════════════════════════════════════
#  Token 预算（宽松设置，不为省钱而省钱）
# ═══════════════════════════════════════════

TOKEN_BUDGET = {
    # --- 日度预算 ---
    "daily_input_tokens":   500_000,    # 50万 input/天（正常用量 ~13万，4倍余量）
    "daily_output_tokens":  200_000,    # 20万 output/天（正常用量 ~6万，3倍余量）
    
    # --- 月度预算 ---
    "monthly_input_tokens":  10_000_000, # 1000万 input/月
    "monthly_output_tokens":  5_000_000, # 500万 output/月
    
    # --- 金额预算（兜底，最重要的一道线）---
    "daily_budget_rmb":    3.0,         # ¥3/天（正常 ¥0.5，6倍余量）
    "monthly_budget_rmb":  30.0,        # ¥30/月（硬上限）
    
    # --- 告警阈值 ---
    "alert_threshold":     0.7,         # 70% 时推企微预警
    "critical_threshold":  0.9,         # 90% 时降级为规则引擎
    
    # --- 超限策略 ---
    "on_daily_exceed":   "degrade",     # 日度超限 → 降级规则引擎（第二天自动恢复）
    "on_monthly_exceed": "degrade",     # 月度超限 → 降级规则引擎（下月自动恢复）
    # 可选值: "degrade"（推荐）/ "warn_only"（只告警不限制）/ "hard_stop"（完全停止 LLM）
    
    # --- 单次调用上限（防止单次失控）---
    "max_input_per_call":   50_000,     # 单次最大 5万 input token
    "max_output_per_call":  30_000,     # 单次最大 3万 output token（R1 深度推理需要）
}

# DeepSeek 定价（2026-04，用于成本计算）
DEEPSEEK_PRICING = {
    "input_cache_hit":    0.20,   # ¥/百万token（缓存命中）
    "input_cache_miss":   2.03,   # ¥/百万token（缓存未命中）
    "output":             3.04,   # ¥/百万token
}
```

### 为什么这样设

| 配置项 | 值 | 理由 |
|--------|-----|------|
| 日度 ¥3 | 正常的 6 倍 | 允许你某天密集分析（比如大盘暴跌连续追问 R1），不会被限制 |
| 月度 ¥30 | 正常的 1.5-2 倍 | 你说了 ¥30 以内接受，这个就是硬兜底 |
| 70% 预警 | ≈ 日度 ¥2.1 / 月度 ¥21 | 给你一个"今天用得比较多哦"的提醒，不影响使用 |
| 90% 降级 | ≈ 日度 ¥2.7 / 月度 ¥27 | 到这个线才降级，留了 30% 缓冲让你正常用 |
| 单次 5万 | R1 深度分析上限 | 防止某个 prompt 意外塞了 10 万 token 的上下文 |

---

## 五、实现方案

### 5.1 后端：`services/llm_gateway.py` 增强

```python
# === Token 用量记录 ===

import json, os, time
from datetime import datetime, date
from config import TOKEN_BUDGET, DEEPSEEK_PRICING

# 用量文件路径
def _usage_path(d: date = None) -> str:
    d = d or date.today()
    os.makedirs("data/llm_usage", exist_ok=True)
    return f"data/llm_usage/{d.isoformat()}.json"

def _read_usage(d: date = None) -> dict:
    path = _usage_path(d)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"input_tokens": 0, "output_tokens": 0, "cost_rmb": 0.0, 
            "calls": 0, "calls_detail": []}

def _save_usage(usage: dict, d: date = None):
    """原子写（复用 Phase 0 的 atomic_write_json）"""
    from persistence import atomic_write_json
    atomic_write_json(_usage_path(d), usage)


def record_token_usage(model: str, input_tokens: int, output_tokens: int, 
                        cache_hit: bool = False):
    """每次 LLM 调用后记录 Token 用量"""
    usage = _read_usage()
    
    # 计算本次成本
    input_rate = DEEPSEEK_PRICING["input_cache_hit"] if cache_hit \
                 else DEEPSEEK_PRICING["input_cache_miss"]
    cost = (input_tokens * input_rate + output_tokens * DEEPSEEK_PRICING["output"]) / 1_000_000
    
    usage["input_tokens"] += input_tokens
    usage["output_tokens"] += output_tokens
    usage["cost_rmb"] = round(usage["cost_rmb"] + cost, 4)
    usage["calls"] += 1
    usage["calls_detail"].append({
        "time": datetime.now().isoformat(),
        "model": model,
        "input": input_tokens,
        "output": output_tokens,
        "cache_hit": cache_hit,
        "cost": round(cost, 4),
    })
    
    _save_usage(usage)
    
    # 检查是否触发告警
    _check_alerts(usage)
    
    return cost


def check_budget() -> dict:
    """调用 LLM 前检查预算，返回 {"allowed": True/False, "reason": "..."}"""
    today = _read_usage()
    monthly = _get_monthly_total()
    budget = TOKEN_BUDGET
    
    # 月度硬上限
    if monthly["cost_rmb"] >= budget["monthly_budget_rmb"] * budget["critical_threshold"]:
        return {"allowed": False, "reason": f"月度预算已用 ¥{monthly['cost_rmb']:.1f}/¥{budget['monthly_budget_rmb']}（{monthly['cost_rmb']/budget['monthly_budget_rmb']*100:.0f}%）"}
    
    # 日度上限
    if today["cost_rmb"] >= budget["daily_budget_rmb"] * budget["critical_threshold"]:
        return {"allowed": False, "reason": f"今日预算已用 ¥{today['cost_rmb']:.2f}/¥{budget['daily_budget_rmb']}（{today['cost_rmb']/budget['daily_budget_rmb']*100:.0f}%）"}
    
    return {"allowed": True, "reason": "ok", 
            "daily_used": today["cost_rmb"], "monthly_used": monthly["cost_rmb"]}


def _get_monthly_total() -> dict:
    """汇总本月所有日度用量"""
    today = date.today()
    total = {"input_tokens": 0, "output_tokens": 0, "cost_rmb": 0.0, "calls": 0}
    for day in range(1, today.day + 1):
        d = today.replace(day=day)
        usage = _read_usage(d)
        for k in total:
            total[k] += usage.get(k, 0)
    total["cost_rmb"] = round(total["cost_rmb"], 2)
    return total


def _check_alerts(usage: dict):
    """检查是否需要告警"""
    budget = TOKEN_BUDGET
    pct = usage["cost_rmb"] / budget["daily_budget_rmb"]
    
    if pct >= budget["critical_threshold"]:
        _send_alert("🔴", f"今日 Token 已用 ¥{usage['cost_rmb']:.2f}（{pct*100:.0f}%），AI 分析将降级为规则引擎")
    elif pct >= budget["alert_threshold"]:
        _send_alert("🟡", f"今日 Token 已用 ¥{usage['cost_rmb']:.2f}（{pct*100:.0f}%），接近上限")


def _send_alert(level: str, msg: str):
    """推送企微告警（只推给厉害了哥，不推老婆）"""
    try:
        from services.wxwork_push import send_text_to_user
        send_text_to_user("LeiJiang", f"{level} Token 预算预警\n{msg}")
    except Exception:
        pass  # 告警失败不影响主流程
```

### 5.2 调用入口改造

```python
# llm_gateway.py 的现有 call_llm 函数改造

async def call_llm(model: str, messages: list, **kwargs) -> dict:
    """统一 LLM 调用入口（增加预算检查）"""
    
    # Step 1: 预算检查
    budget_check = check_budget()
    if not budget_check["allowed"]:
        # 降级为规则引擎
        return {
            "content": "",
            "source": "budget_exceeded",
            "fallback": True,
            "reason": budget_check["reason"],
        }
    
    # Step 2: 单次上限检查
    input_tokens_est = sum(len(m.get("content", "")) // 4 for m in messages)
    if input_tokens_est > TOKEN_BUDGET["max_input_per_call"]:
        # 截断上下文而不是拒绝
        messages = _truncate_context(messages, TOKEN_BUDGET["max_input_per_call"])
    
    # Step 3: 正常调用（现有逻辑）
    response = await _call_deepseek(model, messages, **kwargs)
    
    # Step 4: 记录用量
    usage = response.get("usage", {})
    record_token_usage(
        model=model,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cache_hit=usage.get("prompt_cache_hit_tokens", 0) > 0,
    )
    
    return response
```

### 5.3 `/api/health` 扩展

```python
# main.py — health 端点扩展

@app.get("/api/health")
async def health():
    from services.llm_gateway import _read_usage, _get_monthly_total, check_budget
    
    today_usage = _read_usage()
    monthly_usage = _get_monthly_total()
    budget_status = check_budget()
    
    return {
        # ... 现有字段 ...
        "llm_usage": {
            "today": {
                "calls": today_usage["calls"],
                "input_tokens": today_usage["input_tokens"],
                "output_tokens": today_usage["output_tokens"],
                "cost_rmb": today_usage["cost_rmb"],
                "budget_rmb": TOKEN_BUDGET["daily_budget_rmb"],
                "pct": round(today_usage["cost_rmb"] / TOKEN_BUDGET["daily_budget_rmb"] * 100, 1),
            },
            "monthly": {
                "calls": monthly_usage["calls"],
                "cost_rmb": monthly_usage["cost_rmb"],
                "budget_rmb": TOKEN_BUDGET["monthly_budget_rmb"],
                "pct": round(monthly_usage["cost_rmb"] / TOKEN_BUDGET["monthly_budget_rmb"] * 100, 1),
            },
            "status": "🟢" if budget_status["allowed"] else "🔴",
        }
    }
```

### 5.4 前端展示（Pro 模式专属）

```javascript
// app.js — Pro 模式底部状态栏

function renderTokenStatus(health) {
    if (currentMode !== 'pro' || !health.llm_usage) return '';
    
    const u = health.llm_usage;
    const todayPct = u.today.pct;
    const monthPct = u.monthly.pct;
    
    // 三色指示灯
    const color = todayPct >= 90 ? '#ef4444'   // 🔴 红
               : todayPct >= 70 ? '#f59e0b'    // 🟡 黄
               : '#22c55e';                      // 🟢 绿
    
    return `
    <div class="token-status" style="
        position: fixed; bottom: 60px; right: 12px;
        background: var(--card-bg); border: 1px solid var(--border);
        border-radius: 12px; padding: 8px 12px; font-size: 11px;
        color: var(--text2); z-index: 100; backdrop-filter: blur(10px);
    ">
        <div style="display:flex;align-items:center;gap:6px;">
            <span style="width:8px;height:8px;border-radius:50%;
                        background:${color};display:inline-block;"></span>
            <span>Token: ¥${u.today.cost_rmb.toFixed(2)}/¥${u.today.budget_rmb}</span>
            <span style="opacity:0.5">|</span>
            <span>月: ¥${u.monthly.cost_rmb.toFixed(1)}/¥${u.monthly.budget_rmb}</span>
        </div>
    </div>`;
}
```

**效果示意**：

```
┌────────────────────────────────┐
│                                │
│   （正常页面内容）              │
│                                │
├────────────────────────────────┤
│ 🟢 Token: ¥0.35/¥3  | 月: ¥12.5/¥30 │  ← Pro 模式才显示
├────────────────────────────────┤
│  🏠   📈   📰   🤖   🏦      │  ← 底部导航栏
└────────────────────────────────┘
```

---

## 六、API Key 运维清单

### 6.1 Key 清单

| # | Key 名称 | 环境变量 | 获取方式 | 过期策略 | 失效表现 |
|---|----------|---------|---------|---------|---------|
| 1 | **DeepSeek API Key** | `LLM_API_KEY` | [platform.deepseek.com](https://platform.deepseek.com) → API Keys | 永不过期，余额用完即失效 | 401 Unauthorized → 降级规则引擎 |
| 2 | **Tushare Token** | `TUSHARE_TOKEN` | [tushare.pro](https://tushare.pro) → 个人主页 → 接口TOKEN | 永不过期，积分不够接口403 | 数据接口返回空/报错 → 用 AKShare 降级 |
| 3 | **企微 Secret** | `WXWORK_SECRET` | 企微管理后台 → 应用管理 → 钱袋子 → Secret | 永不过期 | access_token 获取失败 → 推送静默失败 |
| 4 | **企微 AES Key** | `WXWORK_AES_KEY` | 企微管理后台 → 回调配置 | 手动重置才失效 | 企微回调消息解密失败 → 企微聊天无响应 |
| 5 | **Admin Key** | `ADMIN_KEY` | 自定义（config.py 默认值） | 永不过期 | 管理员操作 403 |

### 6.2 Key 失效自动检测

```python
# 集成到 scripts/datasource_health_check.py

async def check_api_keys() -> list:
    """检查所有 API Key 的健康状态"""
    results = []
    
    # 1. DeepSeek
    try:
        resp = await httpx.AsyncClient().post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY')}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}], 
                  "max_tokens": 1},
            timeout=10,
        )
        results.append({"key": "DeepSeek", "status": "🟢" if resp.status_code == 200 else "🔴",
                        "code": resp.status_code})
    except Exception as e:
        results.append({"key": "DeepSeek", "status": "🔴", "error": str(e)})
    
    # 2. Tushare（最小调用测试）
    try:
        import tushare as ts
        pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
        df = pro.trade_cal(exchange='SSE', start_date='20260101', end_date='20260102')
        results.append({"key": "Tushare", "status": "🟢" if len(df) > 0 else "🔴"})
    except Exception as e:
        results.append({"key": "Tushare", "status": "🔴", "error": str(e)})
    
    # 3. 企微（获取 access_token 测试）
    try:
        corpid = os.getenv("WXWORK_CORPID")
        secret = os.getenv("WXWORK_SECRET")
        resp = await httpx.AsyncClient().get(
            f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={secret}",
            timeout=10,
        )
        data = resp.json()
        results.append({"key": "企微", "status": "🟢" if data.get("errcode") == 0 else "🔴",
                        "detail": data.get("errmsg", "")})
    except Exception as e:
        results.append({"key": "企微", "status": "🔴", "error": str(e)})
    
    return results
```

### 6.3 Key 更换操作手册

```bash
# ═══ Key 更换标准流程 ═══

# Step 1: SSH 连接服务器
ssh ubuntu@150.158.47.189

# Step 2: 编辑环境变量
sudo nano /opt/moneybag/.env
# 修改对应的 Key 值，保存退出

# Step 3: 重启服务
sudo systemctl restart moneybag

# Step 4: 验证（3 步确认）
# 4a. 服务启动正常
sudo systemctl status moneybag

# 4b. 健康检查通过
curl -s http://localhost:8000/api/health | python3 -m json.tool | grep -A5 keys_status

# 4c. 功能验证
curl -s http://localhost:8000/api/chat -X POST \
  -H "Content-Type: application/json" \
  -d '{"message": "你好", "userId": "LeiJiang"}' | head -1
```

### 6.4 `/api/health` Key 状态返回

```json
{
    "keys_status": {
        "deepseek": {"status": "🟢", "last_check": "2026-04-16T13:00:00"},
        "tushare": {"status": "🟢", "last_check": "2026-04-16T13:00:00"},
        "wxwork": {"status": "🟢", "last_check": "2026-04-16T13:00:00"}
    }
}
```

**前端 Pro 模式展示**（配置页底部）：

```
┌─────────────────────────────┐
│  🔧 系统状态                 │
│                             │
│  DeepSeek API  🟢 正常      │
│  Tushare 数据  🟢 正常      │
│  企微推送      🟢 正常      │
│                             │
│  上次检查: 13:00            │
└─────────────────────────────┘
```

---

## 七、放入哪个步骤 & 验证方案

### 7.1 实施位置：Phase 0 Day 1（基础设施）

| 任务编号 | 内容 | 工时 | 前置依赖 |
|---------|------|------|---------|
| **1.14** | Token 预算控制 | 0.5h | 1.6（原子写）完成后 |
| **1.15** | API Key 运维 + 健康检查 | 0.5h | 1.1（数据源巡检）完成后 |

### 7.2 验证清单

#### 1.14 Token 预算控制

| # | 验证项 | 方法 | 通过标准 |
|---|--------|------|---------|
| ① | 用量记录 | 调用 `/api/chat`（发一条消息），检查 `data/llm_usage/2026-04-16.json` | 文件存在且 `calls >= 1`，`cost_rmb > 0` |
| ② | 健康检查 | `curl /api/health \| jq .llm_usage` | 返回 `today.calls`、`today.cost_rmb`、`monthly.cost_rmb`、`status` 字段 |
| ③ | 日度预警 | 临时改 `daily_budget_rmb = 0.001`，发消息，检查企微 | 收到 🔴 告警推送 + API 返回 `fallback: true` |
| ④ | 降级生效 | 日度超限后继续发消息 | 收到规则引擎回答（不是 DeepSeek） |
| ⑤ | 前端展示 | 切换到 Pro 模式，查看底部状态栏 | 显示 🟢/🟡/🔴 + 金额 + 百分比 |
| ⑥ | 恢复 | 改回 `daily_budget_rmb = 3.0`（或等第二天自动重置） | 恢复正常 LLM 调用 |

#### 1.15 API Key 运维

| # | 验证项 | 方法 | 通过标准 |
|---|--------|------|---------|
| ① | Key 检测 | `python scripts/datasource_health_check.py` | 3 个 Key 都显示 🟢 |
| ② | 健康检查 | `curl /api/health \| jq .keys_status` | 返回 3 个 Key 状态 |
| ③ | 失效模拟 | 清空 `LLM_API_KEY` → 重启 → 发消息 | DeepSeek 显示 🔴 + 自动降级规则引擎 + 服务不中断 |
| ④ | 前端展示 | Pro 模式配置页 | 显示 3 个 Key 的健康指示灯 |
| ⑤ | 恢复 | 写回正确的 Key → 重启 → 跑巡检 | 全部恢复 🟢 |

### 7.3 铁律检查

| 铁律 | 验证 |
|------|------|
| **#18** 后端做了前端必须接 | ✅ `/api/health` 扩展 → Pro 状态栏 + 配置页展示 |
| **#3** 改完立即验证 | ✅ 每项都有 curl 验证命令 |
| **#2** 改代码前先确认可回滚 | ✅ Phase 0 Day 1 开始前 `git stash` |
| **#9** 最小可用先交 | ✅ 先跑通记录+检查，再做前端展示 |
| **#5** linter ≠ 编译器 | ✅ 验证项包含服务器部署后的真实调用 |

---

## 八、文件改动清单

| 文件 | 改动类型 | 内容 |
|------|---------|------|
| `config.py` | 新增 | `TOKEN_BUDGET` + `DEEPSEEK_PRICING` 配置 |
| `services/llm_gateway.py` | 新增函数 | `record_token_usage()` + `check_budget()` + `_check_alerts()` |
| `services/llm_gateway.py` | 改造 | `call_llm()` 入口加预算检查 |
| `main.py` | 扩展 | `/api/health` 返回 `llm_usage` + `keys_status` |
| `scripts/datasource_health_check.py` | 新增 | `check_api_keys()` 函数 |
| `app.js` | 新增 | `renderTokenStatus()` + Pro 配置页 Key 状态 |
| `data/llm_usage/` | 新目录 | 每日 Token 用量 JSON 文件 |
