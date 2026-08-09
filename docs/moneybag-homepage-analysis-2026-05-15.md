# 钱袋子(MoneyBag)项目 - 当前首页完整实现分析

**文档日期**: 2026-05-15  
**分析版本**: 1.0

---

## 📖 目录

1. [首页当前架构](#首页当前架构)
2. [关键数据流](#关键数据流)
3. [API 数据结构](#api-数据结构)
4. [现有模块/卡片](#现有模块卡片)
5. [信号系统](#信号系统)
6. [配置偏离计算](#配置偏离计算)
7. [性能优化（缓存）](#性能优化缓存)
8. [改造建议](#改造建议)

---

## 首页当前架构

### 文件位置
- **前端首页**: `pages/landing.js` (约 420 行)
- **主应用逻辑**: `app.js` (约 61500 行，核心函数分散在这里)
- **后端 API 路由**: `backend/api/portfolio.py`, `backend/api/signals.py`
- **后端服务**: `backend/services/unified_networth.py`, `backend/services/portfolio_overview.py`

### 首页调用流程

```
renderLanding() 入口
├─ 计算本地净资产（calcNetWorth()）【用于快速首屏】
├─ 加载交易记录（loadTxns(), loadPortfolio()）
├─ 加载资产列表（loadAssets()）
├─ 加载记账流水（loadLedger()）
│
├─ 渲染 Hero 卡片
│  ├─ 本地净资产 Hero（immediate render）
│  └─ 后端统一净资产 Hero（async: loadUnifiedHero()）
│
├─ 渲染月度收支卡片（monthInc / monthExp）
│
├─ 异步加载 5 个副面板
│  ├─ loadDailyFocus()     → "🎯 今日关注"（AI 个性化）
│  ├─ loadSignals()         → "📊 信号总结"（持仓信号）
│  ├─ loadHomeRiskAlert()   → "⚠️ 风控预警"（危险/警告级别）
│  ├─ loadHomeAllocationAdvice() → "🎯 资产配置建议"
│  └─ loadStewardBriefing() → "🤖 管家一句话"（LLM 总结）
│
└─ 底部快速操作按钮
   ├─ 💰 配置资产
   ├─ 📋 配比历史
   ├─ ➕ 记交易
   └─ 🔄 重新测评
```

---

## 关键数据流

### 1. 首页本地 Hero 渲染

```javascript
// pages/landing.js 第 10 行
const nw = calcNetWorth();
const monthNow = new Date();
const monthStart = new Date(monthNow.getFullYear(), monthNow.getMonth(), 1).toISOString();
const monthLedger = ledger.filter(e => e.date >= monthStart);
const monthInc = monthLedger.filter(e => e.direction === 'income').reduce((s,e) => s + (e.amount||0), 0);
const monthExp = monthLedger.filter(e => e.direction !== 'income').reduce((s,e) => s + (e.amount||0), 0);
```

**calcNetWorth() 实现** (app.js 第 309-318 行):
```javascript
function calcNetWorth() {
  const txns = loadTxns();
  const assets = loadAssets();
  const ledger = loadLedger();
  const holdings = calcHoldingsFromTxns(txns);
  const fundValue = holdings.reduce((s,h) => s + h.totalCost, 0);
  const assetTotal = assets.filter(a => a.type !== 'liability').reduce((s,a) => s + (a.value||0), 0);
  const liabilities = assets.filter(a => a.type === 'liability').reduce((s,a) => s + (a.value||0), 0);
  const ledgerIncome = ledger.filter(e => e.direction === 'income').reduce((s,e) => s + (e.amount||0), 0);
  const ledgerExpense = ledger.filter(e => e.direction !== 'income').reduce((s,e) => s + (e.amount||0), 0);
  
  return {
    fundValue,              // 基金成本
    assetTotal,             // 资产总额（现金+房产+车+保险+其他）
    liabilities,            // 负债
    ledgerIncome,           // 总收入
    ledgerExpense,          // 总支出
    ledgerNet: ledgerIncome - ledgerExpense,
    netWorth: fundValue + assetTotal - liabilities + ledgerIncome - ledgerExpense,
    holdings
  };
}
```

**本地数据来源**:
- `loadPortfolio()` → localStorage[STORAGE_KEY] (持仓数据)
- `loadTxns()` → localStorage[TXN_KEY] (交易流水)
- `loadAssets()` → localStorage[ASSETS_KEY] (手动资产)
- `loadLedger()` → localStorage[LEDGER_KEY] (记账流水)

### 2. 后端统一净资产 API

**触发**: `loadUnifiedHero()` → `fetchUnifiedNetworth()`

```javascript
// app.js 第 321-328 行
async function fetchUnifiedNetworth() {
  if (!API_AVAILABLE) return null;
  const uid = getProfileId();
  if (!uid) return null;
  try {
    const r = await fetch(`${API_BASE}/unified-networth?userId=${uid}`, 
                         {signal: AbortSignal.timeout(10000)});
    if (!r.ok) return null;
    return await r.json();
  } catch(e) {
    console.warn('unified-networth:', e);
    return null;
  }
}
```

**API 端点**: `GET /api/unified-networth?userId=xxx`  
**路由**: `backend/api/portfolio.py` 第 497-501 行

```python
@router.get("/api/unified-networth")
def unified_networth_api(userId: str = ""):
    """统一净资产 — 合并所有数据源（股票+基金+手动资产+负债）"""
    if not userId:
        return {"netWorth": 0, "breakdown": {}}
    return calc_unified_networth(userId)
```

**实现**: `backend/services/unified_networth.py` 第 63-200+ 行

---

## API 数据结构

### `/api/unified-networth?userId=xxx` 返回格式

```json
{
  "netWorth": 2850000,
  "breakdown": {
    "investment": {
      "total": 1200000,
      "stocks": [
        {
          "code": "600000",
          "name": "浦发银行",
          "shares": 1000,
          "costPrice": 12.5,
          "currentPrice": 12.8,
          "marketValue": 12800,
          "pnl": 300
        }
      ],
      "funds": [
        {
          "code": "110020",
          "name": "易方达沪深300ETF联接A",
          "shares": 8000,
          "costNav": 1.5,
          "marketValue": 12000
        }
      ]
    },
    "cash": {
      "total": 450000,
      "items": [
        {"id": "asset_1", "name": "银行活期", "value": 450000}
      ]
    },
    "property": {
      "total": 1200000,
      "items": [
        {"id": "asset_2", "name": "北京房产", "value": 1200000}
      ]
    },
    "car": {
      "total": 0,
      "items": []
    },
    "insurance": {
      "total": 0,
      "items": []
    },
    "other": {
      "total": 0,
      "items": []
    },
    "liability": {
      "total": -50000,
      "items": [
        {"id": "asset_3", "name": "房贷余额", "value": 50000}
      ]
    }
  },
  "healthScore": 92,
  "healthGrade": "🟢 健康",
  "healthIssues": [],
  "monthlyIncome": 45000,
  "monthlyExpense": 28000,
  "allocation": {
    "investment": 42.1,
    "cash": 15.8,
    "property": 42.1,
    "car": 0,
    "insurance": 0,
    "other": 0
  }
}
```

### `/api/daily-focus` 返回格式

```json
{
  "source": "ai",  // 或 "default"
  "tips": [
    "📈 沪深300估值处于历史低位，适合定投建仓",
    "💡 本周有3场央行新闻发布会，关注流动性政策",
    "🔔 您持仓的5只基金今年都跑赢同类，继续持有"
  ]
}
```

### `/api/steward/briefing?userId=xxx` 返回格式

```json
{
  "one_line": "市场震荡，继续坚守阵地",
  "regime_description": "市场处于震荡区间，IPO降温，流动性充裕",
  "risk_level": "normal",  // 或 "warning"/"danger"/"blocked"
  "top_signal": "月度配置偏离幅度 +8%（超买区），建议部分获利了结",
  "timestamp": "2026-05-15T14:30:00",
  "elapsed": 2.3  // LLM 调用耗时（秒）
}
```

### `/api/signals` (POST) 返回格式

```json
[
  {
    "icon": "🟢",
    "title": "当前是好的入场时机！",
    "message": "沪深300估值百分位 25%（低估），处于近3年较低水平。历史上低估区间买入，持有3年盈利概率超85%。现在入场性价比高。",
    "type": "timing",
    "severity": "opportunity"
  },
  {
    "icon": "⚠️",
    "title": "止盈建议",
    "message": "您持仓的易方达沪深300已获利 +15%，处于获利目标区间。建议考虑部分获利了结，锁定收益。",
    "type": "take_profit",
    "severity": "warning"
  }
]
```

---

## 现有模块/卡片

### 1. 首屏 Hero（净资产总览）

```html
<div class="pnl-hero">
  <div class="pnl-label">💰 我的净资产 <span>ℹ️</span></div>
  <div>含投资+现金+房产+车辆+保险 - 负债</div>
  <div class="pnl-total-value">¥2,850,000</div>
  
  <div id="heroBreakdown">
    <!-- 从 loadUnifiedHero() 异步填充 -->
    <div>📈 投资 ¥1,200,000</div>
    <div>💵 现金 ¥450,000</div>
    <div>🏠 房产 ¥1,200,000</div>
    <div>💳 负债 -¥50,000</div>
  </div>
  
  <div id="heroHealth">
    🟢 健康 · 92分 · 配置稳定
  </div>
</div>
```

### 2. 月度收支卡片

```html
<div style="display:flex;gap:8px;margin-bottom:16px">
  <div style="flex:1;background:rgba(16,185,129,.08);...">
    <div>本月收入</div>
    <div>+¥45,000</div>
  </div>
  <div style="flex:1;background:rgba(239,68,68,.08);...">
    <div>本月支出</div>
    <div>-¥28,000</div>
  </div>
</div>
```

### 3. 今日关注（AI 个性化）

`loadDailyFocus()` → `/api/daily-focus` (landing.js 第 120-125 行)

```html
<div style="background:rgba(99,102,241,.06);...">
  <div>🎯 今日关注 <span style="font-size:10px">AI</span></div>
  <div>📈 沪深300估值处于历史低位，适合定投建仓</div>
  <div>💡 本周有3场央行新闻发布会，关注流动性政策</div>
  ...
</div>
```

### 4. 管家一句话（LLM 总结）

`loadStewardBriefing()` → `/api/steward/briefing` (landing.js 第 62-70 行)

```html
<div id="stewardBriefingCard" class="dashboard-card" style="border-left:3px solid #6366F1">
  <div class="dashboard-card-title">🤖 管家一句话</div>
  <div>市场震荡，继续坚守阵地</div>
  <div style="font-size:12px;color:var(--text2)">
    📊 市场处于震荡区间，IPO降温，流动性充裕
    🎯 月度配置偏离幅度 +8%（超买区），建议部分获利了结
  </div>
  <button onclick="showLatestReview()">📋 查看收盘复盘</button>
</div>
```

### 5. 信号总结（持仓信号）

`loadSignals()` → `/api/signals` (landing.js 第 47 行，Pro 模式)

- 入场时机信号（🟢/🟡/🔴）
- 止盈止损建议
- 智能定投建议
- 持仓异常警告

### 6. 风控预警摘要

`loadHomeRiskAlert()` → `/api/risk-actions` (landing.js 第 128-140 行)

```html
<div>
  <div style="background:rgba(239,68,68,.08);...;border-radius:12px;...">
    🔴 风险分散度过低：持仓仅2只股票，建议加入债券和现金
  </div>
  <div style="background:rgba(245,158,11,.08);...">
    ⚠️ 净值最大回撤 -12%，高于风险承受度 10%
  </div>
</div>
```

### 7. 资产配置建议

`loadHomeAllocationAdvice()` → `/api/allocation-advice` (landing.js 第 142-166 行，Pro 模式)

```json
{
  "target": {"stock": 50, "bond": 30, "cash": 20},
  "current": {"stock": 58, "bond": 22, "cash": 20},
  "deviation": {"stock": +8, "bond": -8, "cash": 0},
  "valuation_zone": "高估区间",
  "summary": "市场高估，建议减仓权益类资产",
  "advice": [
    {
      "direction": "reduce",
      "message": "📉 股票类超配 +8%，建议减持 ¥120,000"
    },
    {
      "direction": "increase",
      "message": "📈 债券类欠配 -8%，建议增持 ¥120,000"
    }
  ]
}
```

### 8. 快速操作按钮

```html
<div class="bottom-actions">
  <button onclick="showAllocateAssets()">
    💰 配置资产
    <div style="font-size:10px">新存款到账？一键按方案分配</div>
  </button>
  <button onclick="showAllocHistory()">📋 配比历史</button>
  <button onclick="showAddTxn()">➕ 记交易</button>
  <button onclick="startQuiz()">🔄 重新测评</button>
</div>
```

---

## 信号系统

### 信号数据来源

**前端**: `pages/landing.js` 中的 `loadSignals()` 和 `loadHomeRiskAlert()`

**后端**: 
- `backend/api/signals.py::get_signals()` — 买卖信号
- `backend/api/portfolio.py::get_risk_actions_api()` — 风控硬阈值
- `backend/api/portfolio.py::get_allocation_advice_api()` — 配置建议

### 信号生成逻辑

```python
# backend/api/signals.py 第 39-80 行

@router.post("/api/signals")
def get_signals(portfolio: Portfolio):
    """根据持仓生成买卖信号"""
    signals = []
    
    if not portfolio.holdings:
        return signals
    
    # 1. 入场时机 — 基于估值百分位
    val = get_valuation_percentile()
    if val["percentile"] < 30:
        signals.append({
            "icon": "🟢",
            "title": "当前是好的入场时机！",
            "message": f"{val['index']}估值百分位 {val['percentile']}%...",
            "type": "timing",
            "severity": "opportunity"
        })
    elif val["percentile"] >= 70:
        signals.append({
            "icon": "🔴",
            "title": "现在入场要谨慎",
            "message": f"{val['index']}估值百分位 {val['percentile']}%...",
            "type": "timing",
            "severity": "warning"
        })
    
    # 2. 止盈止损策略
    profile_name = portfolio.profile or "平衡型"
    total_cost = sum(h.amount for h in portfolio.holdings)
    total_market = 0
    for h in portfolio.holdings:
        if h.code == "余额宝":
            total_market += h.amount
            continue
        nav_info = get_fund_nav(h.code)
        if nav_info:
            current_nav = nav_info.get("nav")
            cost_nav = h.amount / nav_info.get("shares", 1) if nav_info.get("shares") else 1
            market_val = current_nav * nav_info.get("shares", 0)
            total_market += market_val
    
    # 超过盈利目标则建议止盈
    if total_cost > 0 and total_market > 0:
        pnl_pct = (total_market - total_cost) / total_cost
        if pnl_pct >= 0.15:
            signals.append({...})
    
    return signals
```

### 风控信号 (Risk Actions)

```python
# backend/services/shared_helpers.py 中的 generate_risk_actions()

def generate_risk_actions(transactions: list, val_pct: float) -> dict:
    """根据风控硬阈值生成建议"""
    actions = []
    
    # 1. 集中度检查
    # 2. 最大回撤检查
    # 3. 波动率检查
    # 4. 流动性检查
    
    return {"actions": actions}
```

### 配置建议信号 (Allocation Advice)

```python
# backend/services/shared_helpers.py 中的 generate_allocation_advice()

def generate_allocation_advice(transactions: list, val_pct: float, fg_val: float) -> dict:
    """生成配置调整建议"""
    
    # 计算当前配置（股票/债券/现金占比）
    # 根据估值百分位 + 恐贪指数动态调整目标配置
    # 计算偏离度
    # 生成调整建议
    
    return {
        "target": {...},
        "current": {...},
        "deviation": {...},
        "advice": [...]
    }
```

---

## 配置偏离计算

### 前端实现（无）

前端目前**没有**现成的配置偏离计算逻辑——只是渲染后端返回的数据。

### 后端实现

#### 1. `backend/services/portfolio_overview.py` 第 33-150 行

```python
def get_portfolio_overview(user_id: str = "default") -> dict:
    """汇总全资产，返回统一概览数据"""
    
    # 1. 加载股票+基金持仓
    stock_holdings = unified_load_stock_holdings(user_id)
    fund_holdings = unified_load_fund_holdings(user_id)
    
    # 2. 计算当前配置占比
    # 股票类 = 股票持仓 + 股票型基金
    # 债券类 = 债券型基金
    # 现金类 = 货币基金
    equity = stock_total_mv + fund_stock_type
    bond = fund_bond_type
    cash = fund_money_type
    total_for_alloc = equity + bond + cash
    
    allocation = {
        "equity": round(equity / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "bond": round(bond / total_for_alloc * 100, 1),
        "cash": round(cash / total_for_alloc * 100, 1),
    }
    
    # 3. 默认目标配置
    target = {"equity": 50, "bond": 30, "cash": 20}  # 基于稳健型
    
    # 4. 计算偏离度
    deviation = {
        "equity": round(allocation["equity"] - target["equity"], 1),
        "bond": round(allocation["bond"] - target["bond"], 1),
        "cash": round(allocation["cash"] - target["cash"], 1),
    }
    
    # 5. 健康评分
    health_score = 100
    health_issues = []
    
    # 偏离度检查
    max_dev = max(abs(deviation["equity"]), abs(deviation["bond"]), abs(deviation["cash"]))
    if max_dev > 20:
        health_score -= 25
        health_issues.append(f"资产配置严重偏离目标（最大偏离 {max_dev}%）")
    elif max_dev > 10:
        health_score -= 10
        health_issues.append(f"资产配置偏离目标（{max_dev}%），建议再平衡")
    
    # 6. 再平衡建议
    rebalance = []
    for asset, label in [("equity", "股票类"), ("bond", "债券类"), ("cash", "现金类")]:
        d = deviation[asset]
        if abs(d) > 10:
            direction = "reduce" if d > 0 else "increase"
            emoji = "📉" if d > 0 else "📈"
            amount = abs(d) / 100 * total_for_alloc
            rebalance.append({
                "asset": asset,
                "label": label,
                "direction": direction,
                "deviation": d,
                "amount": round(amount, 0),
                "message": f"{emoji} {label}{'超配' if d > 0 else '欠配'}{abs(d):.0f}%，"
                          f"建议{'减持' if d > 0 else '增持'} ¥{amount:,.0f}",
            })
    
    return {
        "totalMarketValue": round(total_mv, 2),
        "totalCost": round(total_cost, 2),
        "allocation": allocation,
        "target": target,
        "deviation": deviation,
        "healthScore": health_score,
        "healthGrade": health_grade,
        "healthIssues": health_issues,
        "rebalance": rebalance,
    }
```

#### 2. `backend/services/shared_helpers.py` 中的 `generate_allocation_advice()`

```python
def generate_allocation_advice(transactions: list, val_pct: float, fg_val: float) -> dict:
    """生成大类资产配置建议（股/债/现金目标比例+偏离度）"""
    
    # 根据风险偏好计算当前配置
    holdings_result = calc_holdings_from_transactions(transactions)
    
    # 计算当前占比
    current_allocation = {"stock": ..., "bond": ..., "cash": ...}
    
    # 动态调整目标配置（基于估值+恐贪）
    # 高估 → 目标股票减少
    # 低估 → 目标股票增加
    target_allocation = adjust_target_by_valuation(base_target, val_pct, fg_val)
    
    # 计算偏离度
    deviation = {
        "stock": current_allocation["stock"] - target_allocation["stock"],
        "bond": current_allocation["bond"] - target_allocation["bond"],
        "cash": current_allocation["cash"] - target_allocation["cash"],
    }
    
    # 生成建议
    advice = []
    for asset, dev in deviation.items():
        if abs(dev) > 5:  # 阈值
            advice.append({
                "direction": "reduce" if dev > 0 else "increase",
                "message": f"{'减持' if dev > 0 else '增持'} {asset}..."
            })
    
    return {
        "target": target_allocation,
        "current": current_allocation,
        "deviation": deviation,
        "advice": advice,
        "valuation_zone": "高估" if val_pct > 70 else "低估" if val_pct < 30 else "适中",
        "summary": "..."
    }
```

---

## 性能优化（缓存）

### 前端缓存策略

**文件**: `app.js` 第 393-450 行

```javascript
const INSIGHT_CACHE = {
  dashboard: { ttl: 120000 },      // 2 分钟
  news: { ttl: 300000 },            // 5 分钟
  policy: { ttl: 600000 },          // 10 分钟
  macro: { ttl: 900000 },           // 15 分钟
  global: { ttl: 900000 },          // 15 分钟
  fund_news: { ttl: 600000 },       // 10 分钟
  portfolio_news: { ttl: 600000 },  // 10 分钟
  signals: { ttl: 900000 },         // 15 分钟
  pnl: { ttl: 900000 },             // 15 分钟
  nav: { ttl: 600000 },             // 10 分钟
  // ...
};

function getCached(key) {
  // 检查缓存是否过期
  if (age > cfg.ttl) {
    cfg.cached = null;
    return null;
  }
  return cfg.cached;
}

function setCached(key, data) {
  cfg.cached = data;
  cfg.timestamp = Date.now();
}
```

### 后端缓存策略

**文件**: `backend/services/unified_networth.py` 第 36-45 行

```python
_NW_CACHE = {}  # userId -> {data, ts}
_NW_CACHE_TTL = 120  # 2 分钟

def invalidate_networth_cache(user_id: str = ""):
    """清除指定用户的净资产缓存（资产变更后调用）"""
    if user_id and user_id in _NW_CACHE:
        del _NW_CACHE[user_id]
```

### API 响应时间优化

- **首屏 Hero**: 同步渲染本地数据（0-50ms）
- **后端数据**: 异步加载（200-500ms）
- **市场数据**: 使用预计算缓存（<50ms）

---

## 改造建议

### 为"家庭CFO今日面板"改造的关键点

#### 1. 重新组织首页结构

```
家庭CFO今日面板 (新首页)
├─ Header（简洁的导航）
├─ 🎯 四大关键指标 Hero（更突出净资产变化）
│  ├─ 净资产 + 日/周/月变化%
│  ├─ 月度收入
│  ├─ 月度支出
│  └─ 本周盈亏
├─ 📊 配置执行状态（当前 vs 目标的同心圆）
├─ ⚡ 今日3条提醒（从 loadSignals 提取 TOP 3）
├─ 🚀 快速操作区（记账/查看详情/调整配置）
└─ 📈 本周市场视图（下周重点事件）
```

#### 2. 新增"配置执行状态"卡片

```javascript
// 从 /api/allocation-advice 提取数据
// 用同心圆图表显示当前 vs 目标

需要的 API:
- 当前配置占比（已有: /api/allocation-advice）
- 目标配置（已有: 内嵌在 generate_allocation_advice）
- 偏离度警告级别（绿/黄/红）
```

#### 3. 提炼"今日3条提醒"

```javascript
// 从现有的 5 个异步面板提取 TOP 3:
1. loadSignals() → 最紧急的信号
2. loadHomeRiskAlert() → 第一个 danger/warning
3. loadDailyFocus() OR loadStewardBriefing() → AI 综合建议

实现逻辑:
const reminders = [];
const signals = await loadSignals();
if (signals?.length) reminders.push(signals[0]);
const risks = await loadHomeRiskAlert();
if (risks?.length) reminders.push(risks[0]);
const brief = await loadStewardBriefing();
if (brief?.top_signal) reminders.push(brief.top_signal);
return reminders.slice(0, 3);
```

#### 4. 增强"月度关键指标"

```javascript
// 新增：净资产变化%
const thisMonth = /* 获取本月初净资产 */;
const thisMonthNow = /* 当前净资产 */;
const change_pct = ((thisMonthNow - thisMonth) / thisMonth * 100).toFixed(1);

// 新增：本周盈亏
const thisWeek = /* 获取本周交易盈亏 */;

// 新增：现金流量（收入-支出）
const cashFlow = monthIncome - monthExpense;
```

#### 5. 后端新增 `/api/dashboard/cfo-summary` 接口

```python
@router.get("/api/dashboard/cfo-summary")
def cfo_summary_api(userId: str):
    """CFO 仪表盘一站式数据聚合"""
    nw_data = calc_unified_networth(userId)
    alloc_data = generate_allocation_advice(...)
    signals_data = get_signals(...)
    
    # 提取 TOP 3 提醒
    top_reminders = extract_top_reminders(
        signals_data,
        nw_data.get("healthIssues"),
        brief_data.get("top_signal")
    )
    
    return {
        "net_worth": nw_data["netWorth"],
        "net_worth_change_pct": calculate_change_pct(...),
        "monthly_income": nw_data.get("monthlyIncome"),
        "monthly_expense": nw_data.get("monthlyExpense"),
        "cash_flow": nw_data.get("monthlyIncome", 0) - nw_data.get("monthlyExpense", 0),
        
        "allocation_status": {
            "current": alloc_data.get("current"),
            "target": alloc_data.get("target"),
            "deviation": alloc_data.get("deviation"),
            "health_grade": nw_data.get("healthGrade"),
        },
        
        "top_reminders": top_reminders,  # 前 3 条
        
        "recent_trades": get_recent_trades(userId, limit=5),
        "upcoming_tasks": get_upcoming_tasks(userId),
    }
```

---

## 总结

| 维度 | 现状 | 改造方向 |
|------|------|--------|
| **首页定位** | 智能决策中心 | 家庭CFO今日面板 |
| **Hero 展示** | 净资产总额 | 净资产 + 日/周/月变化% |
| **关键指标** | 月收入/支出 | 加上月初净资产、本周盈亏、现金流 |
| **配置显示** | 文字列表 | 同心圆图表（当前 vs 目标） |
| **提醒数量** | 5 个异步面板 | 精简到 TOP 3（优先级排序） |
| **数据聚合** | 多个 API 调用 | 新增 `/api/dashboard/cfo-summary` 一站式 |
| **首屏性能** | 已有缓存 | 继续优化（预加载 TOP 3 提醒） |

---

**下一步**: 基于这份分析，开始设计 CFO Dashboard 的 UI/UX 和后端 API。
