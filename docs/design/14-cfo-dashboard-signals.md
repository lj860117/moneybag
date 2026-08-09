# 钱袋子系统 — "提醒/建议" 信号数据源完整地图

## 📊 Executive Summary

系统已具备**完整的提醒产出体系**，从多个维度生成可作用的信号。你的"家庭CFO面板"可以直接复用这些接口，只需做**前端翻译层**把技术信号转成人话即可。

---

## 🎯 系统产出的提醒信号分类

### 1️⃣ **入场时机信号** (`/api/signals` & `/api/timing`)
**来源**: `backend/api/signals.py` 第 36-70 行 + 第 167-223 行

| 信号类型 | 数据源 | 触发条件 | 严重级别 | 典型提醒 |
|---------|--------|---------|----------|---------|
| **好的入场** | 估值百分位 | < 30% | 🟢 opportunity | "当前是好的入场时机！估值处于近3年较低水平" |
| **适度入场** | 估值百分位 | 30-70% | 🟡 info | "入场时机尚可，适合正常定投节奏" |
| **谨慎入场** | 估值百分位 | > 70% | 🔴 warning | "现在入场要谨慎，处于近3年较高水平" |
| **恐惧贪婪混合** | FGI + 估值 | FGI>75 或 <25 | 🟡/🟢 | "市场恐惧/贪婪，是加仓/风控机会" |

**API 返回示例**:
```python
{
  "timingScore": 45.5,        # 0-100，< 30 最佳 > 70 最差
  "signal": "🟡",
  "verdict": "🟡 适合定投入场",
  "detail": "估值合理，适合定投。",
  "valuationPct": 45,
  "fgi": 42,
  "confidence": 0.91
}
```

---

### 2️⃣ **止盈止损信号** (`/api/take-profit`)
**来源**: `backend/api/signals.py` 第 71-105 行 + 第 240-265 行

| 信号 | 触发条件 | 推荐操作 | 典型提醒 |
|------|---------|---------|---------|
| `reached_target` | 收益 ≥ 目标% | 🎯 分批止盈 | "止盈目标 +30%，建议卖出50%锁定利润" |
| `partial_profit` | 收益 ≥ 20% | 📈 加仓或持有 | "已获利20%，可考虑加仓或持有" |
| `stop_loss` | 亏损 ≤ -目标% | 🚨 止损 | "止损触发，建议立即止损" |
| `in_loss` | 亏损 -10% 左右 | 📉 观察 | "略有亏损，保持耐心持有" |
| `holding` | 正常状态 | 💎 持有 | "目标+30% / 止损-8%，持有中" |

---

### 3️⃣ **智能定投信号** (`/api/smart-dca`)
**来源**: `backend/api/signals.py` 第 107-115 行

```python
{
  "smartAmount": 15000,        # 本月建议定投
  "baseAmount": 10000,         # 基准定投
  "advice": "估值适中，按基准定投",   # 人话解释
  "percentile": 55             # 估值百分位
}
```

**规则**:
- 低估 (< 30%): 定投基准 × 150% = 多买
- 适中 (30-70%): 定投基准 × 100% = 正常
- 高估 (> 70%): 定投基准 × 70% = 少买

---

### 4️⃣ **再平衡信号** (`/api/signals`)
**来源**: `backend/api/signals.py` 第 117-127 行

| 触发条件 | 提醒内容 |
|---------|---------|
| 配置偏离 > 5% | "股票类偏多 6%，目标 40%，建议调整" |
| 仓位过重 | "沪深300 占比 35%，超过目标 30%" |

---

### 5️⃣ **风控硬阈值信号** (`/api/portfolio/risk-actions`)
**来源**: `backend/services/risk.py` 第 230-341 行

#### 规则1: 回撤硬阈值
| 回撤幅度 | 操作 | 严重级别 |
|---------|------|--------|
| ≤ -20% | 🚨 **立即清仓止损** | 🔴 danger |
| -18% ~ -20% | ⚠️ **股票降至40%** | 🔴 danger |
| -15% ~ -18% | ⚠️ **股票降至50%，暂停新增买入** | 🟡 warning |

#### 规则2: 单品占比检查
| 占比 | 提醒 |
|-----|------|
| > 15% | "基金XXX占比18%，超过15%上限，建议减持" |

#### 规则3: 止盈纪律
| 收益 | 提醒 |
|-----|------|
| ≥ 40% | "基金XXX收益45%，建议卖出50%锁定利润" |

#### 规则4: 估值配置调整
| 估值 | 提醒 |
|-----|------|
| > 80% | "估值高估，建议股票≤30% / 债券≥40% / 现金≥15%" |
| < 20% | "估值低估，可提升股票至60%" |

#### 规则5: 集中度警告
| HHI指数 | 提醒 |
|--------|------|
| > 5000 | "持仓过于集中，建议分散到3-5只不同类型基金" |

**返回示例**:
```python
{
  "actions": [
    {
      "level": "warning",
      "rule": "回撤预警线",
      "action": "⚠️ 股票仓位降至50%，暂停新增买入",
      "detail": "当前回撤-16%，触发-15%预警线"
    }
  ],
  "summary": "🟡 2项风险提示需关注",
  "risk_level": "warning",  # safe / warning / danger
  "metrics": {
    "concentration": {...},
    "drawdown": {...},
    "alerts": [...]
  }
}
```

---

### 6️⃣ **信号侦察兵** (`/api/signal-scout/latest`)
**来源**: `backend/services/signal_scout.py` 第 85-388 行

**收集的信号类型**:
```python
SIGNAL_TYPES = {
    "news_policy": "📜 政策信号",
    "news_market": "📰 市场新闻",
    "holder_change": "👔 增减持",
    "pledge_risk": "⚠️ 质押风险",
    "unlock": "🔓 解禁预警",
    "dividend": "💰 分红送转",
    "announcement": "📋 公告",
    "fund_flow": "💹 资金异动",
    "technical": "📊 技术信号",
    "st_warning": "🔴 ST预警",
}
```

**每条信号结构**:
```python
{
  "type": "news_policy",           # 信号类型
  "title": "央行降准0.5%",          # 标题
  "content": "...",                # 内容
  "codes": ["000001", "000002"],   # 相关代码
  "source": "政策",                 # 来源
  "time": "2026-05-15 10:00",     # 时间
  "level": "info/warning/danger",  # 级别
  "tags": ["利好", "降准"],        # 标签
  "relevance": 100,                # 与用户持仓的相关性 (0-100)
  "related_holding": "沪深300"     # 相关持仓
}
```

**返回示例**:
```python
{
  "signals": [
    {
      "type": "news_policy",
      "title": "央行宣布降准0.5%",
      "relevance": 100,
      "related_holding": "沪深300",
      "level": "info"
    },
    ...
  ],
  "total": 23,
  "high_relevance": 8,
  "scanned_at": "2026-05-15T10:15:00",
  "is_trading_day": true
}
```

---

### 7️⃣ **股票持仓异动信号** (`/api/stock-monitor/scan`)
**来源**: `backend/services/stock_monitor.py` 第 200+ 行

**单只股票异动检测**:
```python
{
  "code": "000858",
  "name": "五粮液",
  "price": 150.5,
  "changePct": 2.3,
  "pnlPct": 15.2,
  "weight": 12.5,  # 占比
  "signals": [
    {
      "type": "take_profit",
      "level": "danger",
      "msg": "🎯 五粮液 盈利 15.2%，触发止盈线(20%)！建议立即分批卖出 50%，锁定利润"
    }
  ]
}
```

**组合级纪律检查**:
```python
{
  "discipline_alerts": [
    {
      "type": "concentration",
      "level": "warning",
      "msg": "⚠️ 五粮液(000858) 占比 12.5%，超过集中度警戒线 10%"
    },
    {
      "type": "industry_concentration",
      "level": "warning",
      "msg": "⚠️ 食品饮料行业占比 25%，超过行业上限 20%"
    }
  ]
}
```

---

### 8️⃣ **Steward 晨报中的风控信号**
**来源**: `backend/services/steward.py` + `backend/services/decision_context.py`

Steward 晨报会包含：
```python
{
  "risk_level": "normal",  # normal / warning / danger / blocked
  "risk_actions": [
    {
      "level": "warning",
      "rule": "回撤预警线",
      "action": "⚠️ 股票仓位降至50%，暂停新增买入"
    }
  ],
  "one_line": "📊 震荡整理，风控正常，可定投"
}
```

---

## 🗺️ "今日最重要的 1-3 条提醒" 的构建方案

### 优先级排序逻辑

```python
# 伪代码：提醒排序引擎
def rank_alerts():
    alerts = []
    
    # 第1优先级: 风控危险信号 (danger)
    if risk_actions and any(a.level == "danger"):
        alerts.extend([a for a in risk_actions if a.level == "danger"])
    
    # 第2优先级: 个股止盈/止损信号 (danger/take_profit/stop_loss)
    if stock_signals and any(s.type in ["take_profit", "stop_loss"]):
        alerts.extend([s for s in stock_signals if s.type in ["take_profit", "stop_loss"]])
    
    # 第3优先级: 高相关性新闻 (relevance >= 50)
    if scout_signals and any(s.relevance >= 50):
        alerts.extend([s for s in scout_signals if s.relevance >= 50])
    
    # 第4优先级: 再平衡建议
    if rebalance_signals:
        alerts.extend(rebalance_signals)
    
    # 第5优先级: 入场时机/恐贪指数
    if timing_signals:
        alerts.extend(timing_signals)
    
    return alerts[:3]  # 只取前3条
```

### 前端翻译示例

```javascript
// 从后端拿到的原始信号
const rawSignal = {
  level: "danger",
  rule: "回撤预警线",
  action: "⚠️ 股票仓位降至50%，暂停新增买入",
  detail: "当前回撤-16%，触发-15%预警线"
};

// 翻译成"家庭CFO"语言
const cfoPanelAlert = {
  priority: 1,                    // 最高优先级
  emoji: "⚠️",
  title: "注意风险：回撤超过15%",
  description: "你的组合下跌了16%，已触发风控预警。我的建议是：",
  actions: [
    "暂时停止定投，等待回升",
    "考虑增配债券或货币基金（降低波动）",
    "检查一下持仓的基本面是否有变化"
  ],
  detail: "当前回撤-16%，触发-15%预警线",
  urgency: "medium",
  suggested_action_date: "today"
};
```

---

## 📡 现有 API 端点速查表

| 端点 | 方法 | 用途 | 返回结构 | 示例调用 |
|------|------|------|---------|---------|
| `/api/signals` | POST | 根据持仓生成所有信号 | `[{icon, title, message, type, severity}, ...]` | `curl -X POST /api/signals -d '{...}'` |
| `/api/timing` | GET | 入场时机评分 | `{timingScore, verdict, ...}` | `curl /api/timing` |
| `/api/daily-signal` | GET | 每日综合交易信号 | `{...}` | `curl /api/daily-signal` |
| `/api/take-profit` | POST | 止盈止损策略 | `{status, targetPct, action, ...}` | `curl -X POST /api/take-profit -d '{...}'` |
| `/api/smart-dca` | POST | 智能定投 | `{smartAmount, baseAmount, advice, ...}` | `curl -X POST /api/smart-dca -d '{...}'` |
| `/api/portfolio/risk-actions` | POST | 风控硬阈值 | `{actions, summary, risk_level, ...}` | `curl -X POST /api/portfolio/risk-actions -d '{...}'` |
| `/api/signal-scout/latest?user_id=...` | GET | 信号侦察兵最新信号 | `{signals, total, high_relevance, ...}` | `curl '/api/signal-scout/latest?user_id=...'` |
| `/api/stock-monitor/scan?user_id=...` | GET | 股票持仓扫描 | `{holdings, signals, discipline, ...}` | `curl '/api/stock-monitor/scan?user_id=...'` |
| `/api/dashboard` | GET | 综合市场仪表盘 | `{valuation, fear_greed, ...}` | `curl /api/dashboard` |

---

## 🎨 前端集成点

### 推荐新组件

```html
<!-- 家庭CFO面板 - 今日最重要的1-3条提醒 -->
<div class="cfo-panel">
  <h2>📌 今日需要关注</h2>
  
  <div class="alerts-container">
    <!-- 根据优先级排序渲染 -->
    <div class="alert alert-priority-1 alert-danger">
      <div class="alert-icon">⚠️</div>
      <div class="alert-content">
        <h3>注意风险：回撤超过15%</h3>
        <p>你的组合下跌了16%，已触发风控预警。</p>
        <ul class="actions">
          <li>暂时停止定投，等待回升</li>
          <li>考虑增配债券或货币基金</li>
          <li>检查持仓的基本面</li>
        </ul>
        <small>当前回撤-16%，触发-15%预警线</small>
      </div>
    </div>
    
    <div class="alert alert-priority-2 alert-warning">
      <div class="alert-icon">🎯</div>
      <div class="alert-content">
        <h3>止盈机会：五粮液 +20%</h3>
        <p>已达到止盈目标，建议卖出50%锁定利润</p>
        <small>当前持仓：15200元，建议分批卖7600元</small>
      </div>
    </div>
    
    <div class="alert alert-priority-3 alert-info">
      <div class="alert-icon">💰</div>
      <div class="alert-content">
        <h3>定投提醒：本月建议 ¥15,000</h3>
        <p>估值处于适中水平，按基准定投</p>
        <small>基准月投 ¥10,000 × 1.5 = ¥15,000</small>
      </div>
    </div>
  </div>
</div>
```

---

## ✅ 你只需要做的事

1. **前端展示层** - 把技术信号翻译成"家庭CFO"语言
2. **优先级排序** - 按 danger/warning/info 排序，只显示 TOP 3
3. **实时刷新** - 轮询 `/api/portfolio/risk-actions` + `/api/signals` (每 5 分钟或按需)
4. **个性化文案** - 根据用户持仓名称和收益率填充具体数字

---

## 🔧 数据流完整链路

```
用户持仓 (portfolio.transactions)
    ↓
[1] backend/api/signals.py → 生成 5 类信号
    ├── 入场时机 (valuation + FGI)
    ├── 止盈止损 (成本 + 当前NAV)
    ├── 智能定投 (估值百分位)
    ├── 再平衡 (配置偏离)
    └── 恐贪指数反应
    ↓
[2] backend/services/risk.py → 风控硬阈值
    ├── 回撤监控
    ├── 单品占比
    ├── 止盈纪律
    ├── 估值配置
    └── 集中度警告
    ↓
[3] backend/services/signal_scout.py → 信号侦察
    ├── 政策新闻
    ├── 市场新闻
    ├── 增减持
    ├── 解禁预警
    └── 资金异动
    ↓
[4] backend/services/stock_monitor.py → 股票盯盘
    ├── 个股异动
    ├── 止盈止损
    └── 纪律检查
    ↓
[5] 前端聚合 → TOP 3 提醒
    └── 家庭CFO面板展示
```

---

## 💡 建议实现顺序

### Phase 1: MVP (2-3 天)
- [ ] 新建 `/pages/cfo-dashboard.js` 
- [ ] 调用 `/api/portfolio/risk-actions` 获取风控信号
- [ ] 调用 `/api/signals` 获取交易信号
- [ ] 简单文案翻译 + 优先级排序
- [ ] 渲染 TOP 3 提醒卡片

### Phase 2: 完整集成 (1 周)
- [ ] 增加 `/api/signal-scout/latest` 集成
- [ ] 增加 `/api/stock-monitor/scan` 集成
- [ ] 个性化文案库（基于信号类型 + 用户持仓）
- [ ] 定时轮询（每 5 分钟或推送）
- [ ] 通知动画/声音反馈

### Phase 3: 高级功能 (可选)
- [ ] AI 生成个性化建议文案 (DeepSeek)
- [ ] 历史提醒日志 + 执行追踪
- [ ] 提醒关键词搜索
- [ ] 告警规则自定义

---

## 📚 相关源文件

- 信号生成: `backend/api/signals.py` (完整)
- 风控规则: `backend/services/risk.py` (完整)
- 信号侦察: `backend/services/signal_scout.py` (完整)
- 股票盯盘: `backend/services/stock_monitor.py` (相关部分)
- 决策上下文: `backend/services/decision_context.py` (risk_level/risk_actions 字段)
- 晨报集成: `backend/services/steward.py` (优先级参考)

---

