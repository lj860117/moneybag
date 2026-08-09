# 钱袋子（MoneyBag）— 持仓页 & 周报功能深度分析

## 📋 执行摘要

### 第 2 期改造目标
1. **持仓风险化** — 从单纯展示持仓信息 → 增强风险识别、预警、执行建议
2. **周报人话化** — 从机械数据汇总 → 生成"对话式"人类可读的周报

### 当前系统状态
| 模块 | 位置 | 功能成熟度 | 可复用程度 |
|------|------|----------|---------|
| 股票持仓页 | `pages/stocks.js` | ⭐⭐⭐⭐ | 高（已有盯盘、纪律检查） |
| 基金持仓页 | `pages/portfolio.js` | ⭐⭐⭐ | 中（需与股票统一风险体系） |
| 周报生成 | `backend/services/weekly_report.py` | ⭐⭐ | 低（仅汇总数据） |
| 风控引擎 | `backend/services/risk.py` | ⭐⭐⭐⭐⭐ | 很高（HHI、回撤、相关性完整） |
| 股票盯盘 | `backend/services/stock_monitor.py` | ⭐⭐⭐⭐ | 高（止盈止损、集中度检查） |

---

## 1️⃣ 持仓页当前渲染的信息

### 📊 股票持仓页 (`pages/stocks.js`)

#### 1.1 总览英雄区（Overview Hero）
```javascript
// 渲染内容：
- 总持仓市值（股票+基金汇总）
- 总盈亏金额 + 收益率
- 三层环形图：股票占比% / 债券占比% / 现金占比%
- 资产配置偏离度 (当前vs目标)
- 健康评分 + 评级 + 健康问题汇总

API: /portfolio/overview
返回：{
  totalMarketValue, totalPnl, totalPnlPct, 
  allocation: {equity, bond, cash},
  deviation: {equity, bond, cash},
  healthScore, healthGrade, healthIssues,
  stockCount, fundCount
}
```

#### 1.2 行为风控栏
```javascript
// 功能：显示"行为风控"启用状态 + 当前活跃干预数
// 可点击打开详情面板，展示：
- 总开关（启用/禁用）
- 活跃干预列表 (pattern, status, message, triggered_at, expires_at)
- 每项干预可"确认覆盖"

API: 
  - /behavior/guard-status
  - /behavior/active-interventions
```

#### 1.3 股票持仓列表
```javascript
// 每只股票卡片显示：
- 股票名称 + 代码 + 实时/快照标签
- 当前价 / 涨跌幅 / 行业 / 仓位占比
- 持仓市值 / 成本价
- 盈亏金额 + 盈亏率 (彩色显示)
- 数据新鲜度徽章 (⚡实时 vs 📅快照)

点击进入详情弹窗，显示：
- RSI14 / MACD趋势 / 量比 / 5日均线
- 异动信号 (多选卡片展示)
- 个股新闻 (Top 3)
- 主力资金净流 (万元)
- 解禁预警

API: /stock-holdings/scan
返回：
{
  holdings: [{
    code, name, price, changePct,
    costPrice, shares, industry, weight,
    marketValue, pnl, pnlPct,
    indicators: {rsi14, macd_trend, ma5, ma20, volume_ratio, breakthrough},
    signals: [{level, type, msg}],
    is_snapshot, data_date
  }],
  signals: [{level, type, msg}],
  discipline: [{level, type, msg}]
}
```

#### 1.4 异动信号面板 & 纪律检查面板
```javascript
// 盯盘信号 (⚡ 盯盘信号)：
- 按 level 分类 (danger/warning/opportunity)
  例：RSI超买/量比异常/5日新高/融资余额增加/...

// 纪律检查 (📏 纪律检查)：
- 仓位集中度预警（单只>15%）
- 行业集中度预警（单行>25%）
- 分散度不足（<5只）
```

#### 1.5 AI 深度分析按钮 (Pro 模式)
```javascript
// 点击调用 /stock-holdings/analyze
// 返回结构化分析：
{
  summary: "一句话总结",
  sections: [{title, content}, ...],
  risk_warnings: [...],
  suggestions: [...]
}
```

---

### 💰 基金持仓页 (`pages/portfolio.js`)

#### 2.1 总览英雄区
```javascript
- 基金持仓总市值
- 总盈亏（金额 + 率）
- 更新时间戳
```

#### 2.2 风控体检面板
```javascript
// 显示三大风控指标：
1. 集中度 HHI
   - hhi 值（0-10000）
   - 单一资产最高占比
   - 等级 (分散良好 / 适度集中 / 高度集中)
   - 可点击查看详解

2. 回撤监控
   - 当前回撤 %
   - 等级 (正常 / 中度 / 严重)

3. 相关性分析
   - 平均相关性 (0-1)
   - 对冲效果评价

API: /risk-metrics (POST)
返回：
{
  concentration: {hhi, max_single, level, detail},
  drawdown: {current, max_historical, level, detail},
  correlation: {avg, detail},
  alerts: [{type, severity, message}]
}
```

#### 2.3 风控执行指令面板
```javascript
// 显示硬阈值执行建议：
- 回撤硬阈值 (-15% / -18% / -20%)
- 单基金占比超限
- 止盈建议 (收益≥40%)
- 估值配置调整
- 集中度警告

API: /risk-actions (POST)
返回：
{
  actions: [{level, rule, action, detail}],
  summary: "🔴 2项严重风险需立即处理",
  risk_level: "danger/warning/safe",
  metrics: {...}
}
```

#### 2.4 大类资产配置建议
```javascript
// 展示偏离度指示条（股/债/现）
- 当前% vs 目标%
- 偏离度±x%
- 估值区间 (低估 / 适中 / 高估)
- 配置调整建议

API: /allocation-advice (POST)
返回：
{
  target: {stock, bond, cash},
  current: {stock, bond, cash},
  deviation: {stock, bond, cash},
  advice: [{direction, message}],
  valuation_zone, valuation_pct,
  summary
}
```

#### 2.5 基金持仓列表
```javascript
- 基金名称 / 代码
- 估算净值 + 估算涨幅
- 最新净值
- 回撤 / 连跌天数 / 盈亏率
- 可点击展开详情（估算偏差、最大回撤、波动率、风险信号）
```

---

## 2️⃣ 后端已有的风险检查能力

### 📊 Stock Monitor (`backend/services/stock_monitor.py`)

#### 核心函数：`scan_all_holdings(user_id)`

**返回数据结构**：
```python
{
  "holdings": [
    {
      "code": "600519",
      "name": "贵州茅台",
      "price": 1234.5,           # 当前价
      "changePct": -2.5,         # 今日涨跌%
      "costPrice": 1100,         # 成本价
      "shares": 100,             # 持股数
      "industry": "食品饮料",    # 行业
      "weight": 15.2,            # 占总仓位%
      "marketValue": 123450,     # 市值
      "pnl": 13450,              # 盈亏金额
      "pnlPct": 12.2,            # 盈亏率%
      
      "indicators": {
        "rsi14": 75.2,           # RSI超买>75
        "macd_trend": "多头",
        "ma5": 1200.5,
        "ma20": 1180.3,
        "volume_ratio": 2.3,     # 量比
        "breakthrough": "突破20日新高"
      },
      
      "signals": [
        {
          "level": "danger",     # danger/warning/opportunity/info
          "type": "take_profit",
          "msg": "📊 茅台 盈利 12.2%, 触发止盈线(20%)！建议立即分批卖出50%，锁定利润"
        },
        {
          "level": "warning",
          "type": "rsi_overbought",
          "msg": "⚠️ 茅台 RSI=75.2，超买区域，注意回调风险"
        }
      ],
      
      "is_snapshot": false,      # 数据新鲜度
      "data_date": "2026-05-15"
    }
  ],
  
  "signals": [...all_signals...],      # 全部异动信号（去重）
  
  "discipline": [
    {
      "level": "warning",
      "type": "concentration",
      "msg": "⚠️ 贵州茅台(600519) 占比 15.2%，超过集中度警戒线 15%"
    },
    {
      "level": "warning",
      "type": "industry_concentration",
      "msg": "⚠️ 食品饮料行业占比 28.5%，超过行业上限 25%"
    }
  ],
  
  "holdingCount": 10,
  "signalCount": 5,
  "disciplineCount": 2,
  "totalMarketValue": 812000,
  "scannedAt": "2026-05-15T15:30:45.123Z"
}
```

#### 核心异动检测逻辑 (`detect_anomalies`)

| 异动类型 | 触发条件 | 信号等级 |
|---------|--------|--------|
| 涨幅异动 | 涨 ≥ 5% | opportunity |
| 跌幅异动 | 跌 ≤ -5% | warning |
| 量比异动 | 量比 > 2 | info |
| RSI超买 | RSI > 75 | warning |
| RSI超卖 | RSI < 25 | opportunity |
| 均线突破 | 20日新高/新低 | opportunity/warning |
| **止盈触发** | `pnlPct >= 20%` | **danger** |
| **止损触发** | `pnlPct <= -8%` | **danger** |

#### 组合级纪律检查
```python
# 单只集中度
if weight > STOCK_CONCENTRATION_WARN (15%):
  → 纪律警告

# 行业集中度
if industry_weight > STOCK_INDUSTRY_MAX (25%):
  → 纪律警告

# 分散度不足
if holdings_count < STOCK_MIN_COUNT (5):
  → 纪律提示 (info级)
```

### 🎯 Risk Engine (`backend/services/risk.py`)

#### 核心函数 1：`calc_risk_metrics(transactions)`

**返回结构**：
```python
{
  "concentration": {
    "hhi": 4200,                    # 赫芬达尔指数 (0-10000)
    "max_single": 22.5,             # 单一资产最高占比
    "level": "适度集中",             # 分散良好 / 适度集中 / 高度集中
    "detail": "HHI=4200, 分析..."
  },
  
  "drawdown": {
    "current": -15.3,               # 当前回撤% (负数)
    "max_historical": -21.5,        # 最大历史回撤%
    "level": "中度回撤",             # 正常 / 中度 / 严重
    "detail": "当前回撤15.3%（基于持仓成本与当前市值的组合峰值近似）"
  },
  
  "correlation": {
    "avg": 0.45,                    # 平均相关性
    "detail": "含避险资产（债券3/黄金1），相关性中等偏低"
  },
  
  "alerts": [
    {
      "type": "concentration",
      "severity": "warning",
      "message": "⚠️ 持仓集中度过高（HHI=4200），最大单品占22.5%，建议分散配置..."
    },
    {
      "type": "drawdown",
      "severity": "warning",
      "message": "⚠️ 当前回撤15.3%，注意风险控制"
    }
  ]
}
```

**关键算法**：

1. **HHI 集中度 = Σ(w²) × 10000**
   - w = 每只基金占比
   - HHI < 3000: 分散良好
   - 3000-5000: 适度集中
   - > 5000: 高度集中

2. **回撤计算**：
   ```python
   # FIX 2026-04-19 F5: 真实峰值计算
   peak = sum(max(cost, current_value) for each_holding)
   current_market = sum(current_value for each_holding)
   drawdown = (peak - current_market) / peak * 100
   ```

3. **相关性分类**（按资产类型）
   - 全权益: 相关性 0.8（高）
   - 权益+债券+黄金: 相关性 0.3-0.45（低）

#### 核心函数 2：`generate_risk_actions(transactions, valuation_pct)`

**返回结构**：
```python
{
  "actions": [
    {
      "level": "danger",                    # danger / warning / info
      "rule": "最大回撤红线",
      "action": "🚨 立即止损！回撤已达-20%红线，清仓止损保住本金",
      "detail": "当前回撤-20.5%，触发-20%绝对红线"
    },
    {
      "level": "warning",
      "rule": "回撤警戒线 (-18%)",
      "action": "⚠️ 股票仓位降至40%，增配债券基金",
      "detail": "当前回撤-18.2%，触发-18%警戒线"
    },
    {
      "level": "warning",
      "rule": "单基金占比上限",
      "action": "⚠️ 易方达消费(110022) 占比18%，超过15%上限，建议减持至15%以内",
      "detail": "单只基金仓位上限15%，当前18%"
    },
    {
      "level": "info",
      "rule": "止盈纪律",
      "action": "🎯 易方达消费 收益42%，建议卖出50%锁定利润",
      "detail": "止盈阈值40%，当前42%"
    }
  ],
  
  "summary": "🟡 2项风险提示需关注",      # 或 "🟢 风控检查通过"
  "risk_level": "warning",               # danger / warning / safe
  "metrics": { ...calc_risk_metrics... }
}
```

**硬阈值规则表**：

| 规则 | 阈值 | 执行建议 | 等级 |
|------|------|--------|------|
| 最大回撤 | ≤ -20% | 清仓止损 | danger |
| 回撤警戒线 | ≤ -18% | 降股至40% | danger |
| 回撤预警线 | ≤ -15% | 降股至50% | warning |
| 单基金占比 | > 15% | 减持至≤15% | warning |
| 止盈建议 | ≥ 40% | 卖出50% | info |
| 高估配置 | 估值>80% | 股票≤45% | warning |
| 集中度 | HHI > 5000 | 分散配置 | warning |

---

## 3️⃣ 周报当前输出格式 & 数据来源

### 📝 周报生成器 (`backend/services/weekly_report.py`)

#### 3.1 周报结构
```python
{
  "user_id": "user_123",
  "period": "04/28 - 05/04",
  "week_start": "2026-04-28T00:00:00",
  "week_end": "2026-05-04T23:59:59",
  "generated_at": "2026-05-05T20:30:00",
  
  "summary": "分析5次｜验证4次｜准确率75%｜交易6笔",  # 一句话总结
  
  "judgments": {
    "total_judgments": 5,         # 本周作出的判断数
    "verified": 4,                # 已验证的判断数
    "correct": 3,                 # 正确的判断数
    "accuracy": 75,               # 准确率%
    "details": [                  # 最多10条判断记录
      {
        "id": "j_123",
        "description": "看好医药赛道",
        "time": "2026-04-28T09:30:00",
        "verified": true,
        "correct": true,
        "result": "验证正确 ✅"
      }
    ]
  },
  
  "portfolio_changes": {
    "total_transactions": 6,
    "buys": 4,
    "sells": 2,
    "total_bought": 50000,        # 总买入金额
    "total_sold": 30000,          # 总卖出金额
    "net_flow": 20000             # 净买入额
  },
  
  "market_review": {
    "regime": "trending_bull",    # 市场状态
    "regime_description": "市场处于趋势上升阶段",
    "confidence": 0.78            # 信心度
  },
  
  "recommendations": [
    "✅ 本周表现正常，继续保持纪律投资",
    "📉 本周判断准确率较低(75%)，建议下周减少操作，以观望为主",
    "🐂 市场处于趋势牛，可以适当跟随趋势，注意止盈"
  ]
}
```

#### 3.2 数据来源

| 数据项 | 数据源 | 可用性 |
|------|------|------|
| 判断记录 | `services.judgment_tracker` | 仅原型阶段 |
| 交易记录 | `persistence.load_user()["portfolio"]["transactions"]` | ⭐⭐⭐⭐ |
| 市场状态 | `services.regime_engine.classify()` | ⭐⭐⭐⭐ |
| 持仓变化 | 通过交易记录推算 | ⭐⭐⭐⭐ |

#### 3.3 保存机制
```
DATA_DIR / {user_id} / reports / week_20260505.json
```
- 按周一日期命名
- 每周生成一次（每周日晚8点）
- `get_history(user_id, limit=4)` 获取最近N周

---

## 4️⃣ 月度快照存储机制

### 📦 当前现状
**暂无独立的月度快照机制**。

**推导信息来源**：
```python
# 月度快照可从以下推算：
1. 月初 vs 月末的交易记录差异
2. 周报历史（4-5周堆积）
3. 持仓变化历史记录（portfolio.history[]）
```

**建议改造方向**：
```python
# 新增月度快照 (snapshot.py)
def generate_month_snapshot(user_id, year, month):
  """生成月度快照：月初持仓、月内交易、月末持仓、收益率、操作评分"""
  return {
    "month": "2026-05",
    "start_holdings": {...},        # 月初持仓明细
    "transactions": [...],          # 月内交易流水
    "end_holdings": {...},          # 月末持仓明细
    "pnl": {total, pct},            # 月度收益
    "operations": {count, quality}, # 操作质量评分
    "decisions": {...},             # 决策评价
    "snapshots_dir": f"DATA_DIR/{user_id}/snapshots/month_202605.json"
  }
```

---

## 5️⃣ 第 2 期改造方案框架

### 🎯 持仓风险化

#### 目标：从"展示数据" → "识别风险" → "建议执行"

**三层渐进**：

1. **L1 风险识别** (已有)
   - 单只持仓：止盈止损触发
   - 组合级：集中度/回撤/相关性超警戒
   - 纪律类：分散度不足、行业占比过高

2. **L2 风险预警** (需增强)
   - 关联风险：这只股票跌时，整个行业都会跌吗？
   - 系统性风险：整体回撤超15%时的组合压力测试
   - 未来风险：解禁股票预警、融资融券压力

3. **L3 执行建议** (需新增)
   - 一键快速查看"今天应该做什么"
   - 分优先级的执行指令卡片
   - 操作后的"假设收益率"计算

#### 前端改造 (stocks.js & portfolio.js)
```javascript
// 1. 增加"风险优先级"视图
//   - 危险 (danger) 红色卡片优先展示
//   - 警告 (warning) 黄色卡片次之
//   - 机会 (opportunity) 绿色卡片最后

// 2. 增加"建议面板"
//   - 顶部固定条：🎯 建议执行 (点击展开)
//   - 优先级排序的执行指令

// 3. 增加"模拟计算"
//   - 卖出 X%后的新持仓配置
//   - 新的 HHI / 回撤 / 相关性预览
```

### 🗣️ 周报人话化

#### 目标：从"数据堆砌" → "对话式讲述" → "行动建议"

**三个方向**：

1. **数据口语化**
   ```
   当前：分析5次｜验证4次｜准确率75%｜交易6笔
   人话：这周你做了 5 个判断，其中 4 个得到了验证，准确率达到了 75%。
        虽然准确率不错，但建议下周还是以观望为主，等市场信号更明确。
   ```

2. **故事线连接**
   ```
   当前：
     - 判断准确率低 → 建议减少操作
     - 交易过频繁 → 不利长期收益
     - 市场趋势牛 → 适当跟随

   人话：
     这周，市场处于上升趋势，很多人都在加仓。你的 75% 准确率其实不错，
     但如果继续频繁操作，反而可能损耗收益。我的建议是：
     1️⃣  继续保持你的分析能力
     2️⃣  但减少实际交易频次（从 6 笔降到 2-3 笔）
     3️⃣  让好的判断有更多的时间去体现价值
   ```

3. **行动清单化**
   ```
   推荐执行清单 (下周):
   □ 观察医药板块（你上周的看好判断已验证）
   □ 监控 A 股消费 ETF 的融资余额
   □ 如果市场回撤 > 10%，触发定投计划
   □ 每周一复盘判断准确率，调整选股策略
   ```

#### 后端改造 (weekly_report.py)
```python
def generate_narrative_report(user_id, weeks_ago=0):
  """生成对话式周报"""
  
  report = generate(user_id, weeks_ago)
  
  # 1. 数据口语化
  narrative = {
    "headline": "这周市场上升，你的判断准确率 75%，继续加油！",
    
    "judgment_story": "这周你做了 5 个关于市场的判断，其中 4 个验证了，准确率 75%。...",
    
    "operation_story": "你进行了 6 笔交易，买入 5 万，卖出 3 万。从交易频率看...",
    
    "market_story": "市场整体处于上升阶段，这对持有权益类资产的你是有利的。但...",
    
    "recommendation_list": [
      {"priority": 1, "action": "观察医药板块...", "reason": "验证了你的判断"},
      {"priority": 2, "action": "设置 A 股回撤预警...", "reason": "当前离-15%不远"},
    ]
  }
  
  return narrative
```

---

## 6️⃣ 关键改造点速查表

| 改造项 | 当前位置 | 改造方向 | 优先级 | 工作量 |
|------|--------|--------|------|------|
| 股票风险等级排序 | stocks.js:107 | 按 signal.level 分组展示 | P0 | 2h |
| 建议执行卡片 | portfolio.js:88-109 | 从 risk-actions 补充前端渲染 | P0 | 3h |
| 模拟计算面板 | 无 | 新增"如果我卖出X%..." | P1 | 8h |
| 周报对话化 | weekly_report.py:179-191 | 补充 narrative 字段生成 | P0 | 4h |
| 行动清单化 | 无 | 周报新增 action_list 字段 | P1 | 3h |
| 月度快照 | 无 | 新增 snapshot.py | P1 | 6h |
| 关联风险分析 | 无 | 新增相关股票/基金跌幅联动检查 | P2 | 12h |
| 未来风险识别 | 无 | 融资融券/解禁预警集成 | P2 | 10h |

---

## 附录：关键配置参数

### `config.py` 风控阈值

```python
# 股票端
STOCK_SINGLE_MAX = 0.15          # 单只占比上限 15%
STOCK_INDUSTRY_MAX = 0.25        # 行业占比上限 25%
STOCK_MIN_COUNT = 5              # 最小持仓数 5只
STOCK_STOP_LOSS = -0.08          # 止损线 -8%
STOCK_TAKE_PROFIT = 0.20         # 止盈线 +20%
STOCK_CONCENTRATION_WARN = 0.15  # 集中度预警 15%

# 基金端
RISK_MAX_DRAWDOWN_LIMIT = -0.20      # 最大回撤红线 -20%
RISK_DRAWDOWN_DANGER = -0.18         # 回撤危险线 -18%
RISK_DRAWDOWN_WARNING = -0.15        # 回撤预警线 -15%
RISK_SINGLE_FUND_MAX = 0.15          # 单基金占比 15%
RISK_TAKE_PROFIT = 0.40              # 基金止盈 40%

# 估值区间
VALUATION_HIGH = 0.80            # 高估百分位 80%

# 配置档位
ALLOCATION_PROFILES = {
  "low": {"stock": 0.75, "bond": 0.15, "cash": 0.10},    # 低估
  "mid": {"stock": 0.65, "bond": 0.25, "cash": 0.10},    # 适中
  "high": {"stock": 0.45, "bond": 0.35, "cash": 0.20}    # 高估
}

# 相关性默认值
CORRELATION_DEFAULTS = {
  "all_equity": 0.8,              # 全权益
  "with_hedge": 0.45,             # 有对冲
  "stock_bond_gold": 0.35,        # 股债金
  "mixed": 0.5                    # 混合
}
```

---

## 🎓 参考链接

- **股票盯盘**：`backend/services/stock_monitor.py:443-546`
- **风控引擎**：`backend/services/risk.py:121-341`
- **周报生成**：`backend/services/weekly_report.py:30-79`
- **持仓页渲染**：`pages/stocks.js:1-150 & pages/portfolio.js:1-145`
- **API 路由**：`backend/api/holdings.py` & `backend/api/portfolio.py`

