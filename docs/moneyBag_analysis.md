# 钱袋子(MoneyBag) 项目现状全面分析

## 📊 项目概览

**钱袋子** 是一个全栈资产管理系统（V4.0），包含：
- **前端**: 纯JS前端（app.js 795行 + pages/*.js）
- **后端**: FastAPI Python 后端（api/*.py + services/*.py）
- **架构**: 客户端持久化 + 服务端数据统一 + 云端同步

---

## 🏠 当前首页（landing.js）实现

### 当前首页渲染流程

```javascript
renderLanding() {
  1. 加载本地数据: loadPortfolio() → 持仓
                  loadTxns() → 交易流水
                  loadAssets() → 资产
                  loadLedger() → 记账
  
  2. 计算净资产: calcNetWorth() → 本地计算版
  
  3. 渲染UI结构:
     ├─ 💰 净资产 Hero (pnl-hero)
     │  ├─ 总净资产值
     │  ├─ 投资/现金/房产/负债 分桶
     │  └─ 本月收入/支出
     │
     ├─ 🤖 管家一句话 (stewardBriefingCard) → API /steward/briefing
     │
     ├─ 🎯 今日关注 (dailyFocusSection) → API /daily-focus
     │
     ├─ 🎯 信号系统 (signalsSection) → 来自 loadSignals()
     │
     ├─ ⚠️ 风控预警 (riskAlertSection) → API /risk-actions
     │
     ├─ 📊 资产配置建议 (allocationAdviceSection) → API /allocation-advice
     │
     └─ 💰 底部快捷按钮
        ├─ 配置资产
        ├─ 配比历史
        ├─ 记交易
        └─ 重新测评
        
  4. 异步加载数据:
     - loadSignals() → 从 /api/signals 拉买卖信号
     - loadDailyFocus() → 从 /api/daily-focus 拉今日3个提醒
     - loadHomeRiskAlert() → 风控预警
     - loadHomeAllocationAdvice() → 配置建议
     - loadUnifiedHero() → 统一净资产更新
     - loadStewardBriefing() → 管家简报
}
```

### 首页当前有哪些卡片/模块

| 卡片ID | 名称 | 数据源 | 显示条件 |
|--------|------|--------|---------|
| `pnl-hero` | 💰 净资产英雄卡 | 本地计算 | 必显 |
| `dailyFocusSection` | 🎯 今日关注 | `/api/daily-focus` | API可用 |
| `stewardBriefingCard` | 🤖 管家一句话 | `/api/steward/briefing` | API可用 |
| `signalsSection` | 📊 信号系统 | `/api/signals` | Pro模式 |
| `riskAlertSection` | ⚠️ 风控预警 | `/api/risk-actions` | API可用 |
| `allocationAdviceSection` | 📊 资产配置建议 | `/api/allocation-advice` | Pro模式 |
| `v6EmptyHome` | 👋 空仓欢迎卡 | `/api/timing`, `/api/daily-signal`, `/api/news/impact` | 空仓用户 |
| `v6HouseholdHero` | 👨‍👩‍👧‍👦 家庭总资产 | `/api/household/summary` | Pro + 多成员 |

---

## 📡 核心API数据结构

### 1️⃣ fetchUnifiedNetworth() → /api/unified-networth

**前端调用**:
```javascript
async function fetchUnifiedNetworth() {
  const r = await fetch(`${API_BASE}/unified-networth?userId=${uid}`);
  return r.json();
}
```

**返回数据结构** (来自 backend/services/unified_networth.py):

```python
{
  "netWorth": 1234567.89,  # 总净资产
  "healthScore": 85,        # 健康分（0-100）
  "healthGrade": "🟢 健康",  # 健康等级
  "healthIssues": [         # 健康问题列表
    "负债占比 20%",
    "资产配置偏离目标 15%"
  ],
  
  # 大类分桶汇总
  "breakdown": {
    "investment": {
      "total": 500000,      # 投资总额
      "stockTotal": 200000, # 股票市值
      "fundTotal": 300000,  # 基金市值
      "txnFundTotal": 0,    # V4交易流水基金（去重）
      "stockCount": 5,
      "fundCount": 8,
      "stockItems": [...],  # 前10只
      "fundItems": [...]    # 前10只
    },
    "cash": {
      "total": 150000,
      "items": [{"name": "工商银行", "value": 100000}]
    },
    "property": {
      "total": 800000,
      "items": [{"name": "北京朝阳房产", "value": 800000}]
    },
    "car": {"total": 250000, "items": [...]},
    "insurance": {"total": 50000, "items": [...]},
    "other": {"total": 0, "items": []},
    "liability": {
      "total": 200000,
      "items": [{"name": "房贷", "value": 200000}]
    }
  },
  
  # 配置占比（环形图用）
  "allocation": {
    "investment": 35.2,
    "cash": 10.5,
    "property": 56.3,
    "car": 17.6,
    "insurance": 3.5,
    "other": 0
  },
  
  # 记账现金流（参考展示，不计入净资产）
  "cashFlow": {
    "totalIncome": 500000,
    "totalExpense": 300000,
    "totalNet": 200000,
    "monthIncome": 45000,
    "monthExpense": 30000,
    "monthNet": 15000
  },
  
  "updatedAt": "2026-05-15T10:30:00"
}
```

---

### 2️⃣ Signals API → /api/signals

**前端调用**:
```javascript
async function fetchSignals() {
  const p = loadPortfolio();  // 持仓对象
  const r = await fetch(API_BASE + '/signals', {
    method: 'POST',
    body: JSON.stringify(p)
  });
  return r.json();
}
```

**返回数据结构** (来自 backend/api/signals.py):

```python
[
  {
    "icon": "🟢",
    "title": "当前是好的入场时机",
    "message": "沪深300估值百分位 25%（较低），处于近3年较低水平...",
    "type": "timing",
    "severity": "opportunity"  # opportunity/warning/info
  },
  {
    "icon": "🎯",
    "title": "止盈止损 | 目标+20% / 止损-10%",
    "message": "你的持仓已浮盈15%，距离目标 +20% 还差 5%...",
    "type": "take_profit",
    "severity": "info"
  },
  {
    "icon": "🧠",
    "title": "智能定投：本月建议 ¥10,000",
    "message": "基准定投 ¥5,000，当前估值低估建议增加投入...",
    "type": "smart_dca",
    "severity": "info"
  },
  {
    "icon": "⚖️",
    "title": "股票型基金需要再平衡",
    "message": "当前占比 45%，目标 40%，超配 5%，建议调整",
    "type": "rebalance",
    "severity": "warning"
  },
  {
    "icon": "😱",
    "title": "市场极度恐惧 — 可能是加仓机会",
    "message": "恐惧贪婪指数 78/100...",
    "type": "fear",
    "severity": "opportunity"
  }
]
```

---

### 3️⃣ Daily Focus API → /api/daily-focus

**返回数据** (来自 backend/api/enhance.py):

```python
{
  "source": "ai",  # "ai" or "default"
  "tips": [
    "📊 今日权重股偏弱，消费板块走强，可重点关注食饮+旅游",
    "🎯 北向资金净流入 8 亿，两融余额微升，市场热度尚可",
    "⚠️ 近期可转债大幅波动，持有可转债的要留意价格变化"
  ],
  "timestamp": "2026-05-15T08:00:00"
}
```

---

### 4️⃣ Allocation Advice API → /api/allocation-advice

**返回数据** (来自 backend/api/portfolio.py):

```python
{
  "user_id": "user_123",
  "valuation_zone": "中估区间",
  
  # 大类配置当前值
  "current": {
    "stock": 45,      # 股票类占比
    "bond": 25,       # 债券类占比
    "cash": 30        # 现金类占比
  },
  
  # 目标配置
  "target": {
    "stock": 50,
    "bond": 30,
    "cash": 20
  },
  
  # 偏离度
  "deviation": {
    "stock": -5,      # 当前 - 目标
    "bond": -5,
    "cash": 10
  },
  
  # AI 建议
  "advice": [
    {
      "direction": "increase",  # increase/reduce/hold
      "message": "现金类超配 10%，考虑逢低入场"
    },
    {
      "direction": "hold",
      "message": "债券类偏低，当前估值下不急着增加"
    }
  ],
  
  "summary": "整体配置合理，本月可尝试用现金逢低建仓"
}
```

---

### 5️⃣ Risk Actions API → /api/risk-actions

**返回数据**:

```python
{
  "actions": [
    {
      "action": "⚠️ 持仓集中度偏高（前3大占比 60%），建议分散",
      "level": "warning"  # danger/warning/info
    },
    {
      "action": "🔴 最大回撤风险警告：近期波动率升高",
      "level": "danger"
    }
  ]
}
```

---

### 6️⃣ Timing API → /api/timing

**返回数据** (来自 backend/api/signals.py):

```python
{
  "timingScore": 35.2,         # 综合入场分数（0-100）
  "signal": "🟢",               # emoji 信号
  "verdict": "🟢 非常适合入场",  # 推荐
  "detail": "估值低 + 市场恐惧，历史上是最佳买入窗口",
  "valuationPct": 25,           # 沪深300估值百分位
  "fgi": 78,                    # 恐惧贪婪指数
  "fgiLevel": "极度恐惧",
  "confidence": 0.85            # 置信度
}
```

---

## 🧠 后端计算逻辑梳理

### A. 净资产计算 (calc_unified_networth)

**文件**: `backend/services/unified_networth.py`

**公式**:
```
净资产 = 投资总额 + 手动资产 - 负债

投资总额 = 股票市值 + 基金市值 + V4交易流水基金
手动资产 = 现金 + 房产 + 车辆 + 保险 + 其他
负债 = 贷款 + 其他负债
```

**数据来源优先级**:
1. **股票**: 来自 `stock_monitor.load_stock_holdings()` (盯盘系统)
2. **基金**: 来自 `fund_monitor.load_fund_holdings()` (盯盘系统)
3. **手动资产**: 来自 `portfolio["assets"]` (资产管理页添加)
4. **V4交易流水基金**: 来自 `portfolio["transactions"]` (去重)

**健康评分逻辑**:
- 基础分: 100
- -25 if 负债占比 > 50%
- -10 if 负债占比 > 30%
- -20 if 最大类型占比 > 80%（集中度）
- -10 if 最大类型占比 > 60%
- -15 if 月现金 < 月支出 × 3（应急储备不足）
- -10 if 无投资但资产 > 50000

---

### B. 资产配置偏离检测 (get_portfolio_overview)

**文件**: `backend/services/portfolio_overview.py`

**配置分类**:
```
股票类 = 直接持股 + 股票型/混合型基金
债券类 = 纯债/信用债基金
现金类 = 货币基金 + 现金存款
```

**目标配置** (默认稳健型):
```python
target = {
  "equity": 50,      # 股票类
  "bond": 30,        # 债券类
  "cash": 20         # 现金类
}
```

**偏离度计算**:
```python
deviation = current - target

# 判断
if max(abs(deviation)) > 20:
  health_score -= 25  # "严重偏离"
elif max(abs(deviation)) > 10:
  health_score -= 10  # "需要再平衡"
```

---

### C. 信号生成逻辑 (get_signals)

**文件**: `backend/api/signals.py`

**6类信号**:

| 类型 | 触发条件 | 优先级 |
|------|---------|-------|
| timing | 估值百分位 < 30 / > 70 | 高 |
| take_profit | 当前盈利 | 高 |
| smart_dca | 每周推荐定投 | 中 |
| rebalance | 单类偏离 > 5% | 中 |
| fear | 恐惧贪婪 > 75 或 < 25 | 中 |
| patience | 持仓 < 30 天 | 低 |

---

## 🎯 重新设计为"家庭CFO今日面板"的可用能力

### ✅ 可直接复用的能力

1. **统一净资产引擎** (calc_unified_networth)
   - ✓ 已按5类分桶（投资/现金/房产/车辆/保险/负债）
   - ✓ 已包含健康评分和问题诊断
   - ✓ 已支持多用户隔离

2. **配置偏离检测** (get_portfolio_overview)
   - ✓ 已有股债现配置占比
   - ✓ 已有目标配置和偏离度
   - ✓ 已有再平衡建议

3. **信号系统** (/api/signals)
   - ✓ 已支持6类信号
   - ✓ 可提炼"今日3条提醒"的逻辑
   - ✓ 已有severity分级（opportunity/warning/info）

4. **风控告警** (/api/risk-actions)
   - ✓ 已有集中度检测
   - ✓ 已有回撤风险警告
   - ✓ 可直接用于"风险摘要"

5. **入场时机** (/api/timing)
   - ✓ 已有综合评分
   - ✓ 已结合估值+恐惧贪婪
   - ✓ 可用于"市场温度"

### ⚙️ 需要改造/新增的能力

1. **家庭成员汇总** 
   - 现有: `/api/household/summary` (已有)
   - 需改: 改为"家庭CFO面板" 视角（按角色权限）

2. **今日3条提醒**
   - 现有: `/api/daily-focus` (已有)
   - 需改: 改为"今日3大行动" (Action-oriented，不是信息)
   - 示例:
     ```
     ✅ 行动1: 用 ¥50,000 现金逢低定投（估值低估25%）
     ⚠️ 行动2: 检查房贷配置，利率有优化空间
     💡 行动3: 本月定投 ¥12,000 替代 ¥8,000（市场机会）
     ```

3. **资产健康评分卡**
   - 现有: healthScore + healthGrade
   - 需改: 增加"改善行动清单"（actionable items）

4. **配置偏离的优先级排序**
   - 现有: 按偏离度绝对值排序
   - 需改: 按"当前市场机会 + 风险等级" 排序

5. **家庭成员对账**
   - 新增: 跨多个profile的数据一致性检测
   - 示例: "配偶账户基金X未更新，与主账户偏离 20%"

---

## 📐 前端当前架构

### 页面组织

```
pages/
  ├─ landing.js       # 首页（智能决策中心）
  ├─ assets.js        # 资产管理页
  ├─ portfolio.js     # 持仓管理页
  ├─ stocks.js        # 股票盯盘
  ├─ insight.js       # 资讯页
  ├─ analysis.js      # 分析页
  ├─ history.js       # 历史记录
  ├─ chart.js         # 图表
  ├─ chat.js          # AI对话
  ├─ ledger.js        # 记账
  ├─ quiz.js          # 风险测评
  └─ alloc.js         # 配置工具
```

### 数据流

```
客户端数据持久化:
  localStorage
  ├─ moneybag_current_profile   # 当前用户
  ├─ moneybag_portfolio         # 持仓（STORAGE_KEY）
  ├─ moneybag_txns              # 交易流水（TXN_KEY）
  ├─ moneybag_assets            # 资产（ASSETS_KEY）
  └─ moneybag_ledger            # 记账（LEDGER_KEY）

  ↓ 同步 ↓

后端持久化:
  /data/{userId}/user.json
  ├─ portfolio.holdings
  ├─ portfolio.transactions
  ├─ portfolio.assets
  ├─ ledger
  └─ ...
```

---

## 🚀 重新设计实施路线图

### Phase 1: 复用现有能力
- [x] 统一净资产计算 (已有)
- [x] 配置偏离检测 (已有)
- [x] 信号系统 (已有)
- [x] 入场时机 (已有)

### Phase 2: 改造首页UI
- [ ] 首页改为"CFO今日面板" 布局
  - [ ] 核心指标卡 (净资产 + 配置 + 风险)
  - [ ] 今日3大行动（Action-oriented）
  - [ ] 本月现金流仪表盘
  - [ ] 家庭成员简表

### Phase 3: 新增服务端能力
- [ ] `/api/cfo-dashboard` 统一聚合路由
- [ ] `/api/today-actions` 提炼"今日3大行动"
- [ ] `/api/cash-flow-summary` 现金流仪表盘
- [ ] `/api/family-health-check` 家庭资产体检

### Phase 4: 前端集成
- [ ] 改造 landing.js 页面布局
- [ ] 新增 CFO 面板组件
- [ ] 优化响应式设计

---

## 📊 数据示例：完整渲染流程

### 用户打开首页时的数据流

```
1. 前端发起请求:
   - /api/unified-networth?userId=user_123
   → 返回完整净资产 + 5分桶 + 健康评分
   
2. 前端异步更新:
   - /api/signals (POST portfolio)
   → 返回买卖信号数组
   
   - /api/daily-focus
   → 返回今日3条提醒
   
   - /api/timing
   → 返回入场时机评分
   
   - /api/allocation-advice
   → 返回配置偏离 + 建议
   
   - /api/risk-actions
   → 返回风控告警

3. 前端聚合展示:
   Hero卡 (净资产 + 配置占比)
   ↓
   今日3大行动 (从signals提炼)
   ↓
   现金流卡 (本月收支)
   ↓
   配置偏离卡 (需要再平衡？)
   ↓
   风险告警卡 (需要关注？)
```

---

## 🎬 总结

### 当前首页的优势
✅ 数据聚合完整（投资+资产+负债+现金流）
✅ 后端信号系统完善（6类信号）
✅ 健康评分有逻辑
✅ 配置偏离检测准确

### 重新设计的空间
🔧 从"展示信息" → "驱动行动"
🔧 从"多个卡片" → "3个优先级行动"
🔧 从"个人理财" → "家庭CFO视角"
🔧 从"静态信息" → "动态洞察"

### 建议的改进方向
1. 新增 `/api/cfo-dashboard` 聚合路由
2. 设计"行动驱动"的Today Actions逻辑
3. 强化家庭成员维度的协同
4. 优化移动端的"一屏CPM指标"
