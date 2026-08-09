# 家庭CFO今日面板 - 重新设计执行方案

## 🎯 设计目标

从 **"投资理财信息展示"** 转变为 **"家庭财务决策驾驶舱"**

### 核心要点
- **One-Screen Clarity**: 一屏内掌握财务全貌
- **Action-Oriented**: 每个卡片都带有明确行动建议
- **Family-First**: 以家庭总资产视角而非个人
- **Real-time Alerts**: 实时风险提醒
- **Prioritized Actions**: 按重要性排序的3大行动

---

## 📐 新首页布局设计

### 布局结构（从上到下）

```
┌─────────────────────────────────────────────────────────┐
│ 顶栏：👤 个人/家庭 | 📅 2026年5月15日 10:30 | ⚙️ 设置  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 1️⃣ 核心指标卡（hero section）                        │
│  ├─ 左: 💰 净资产 ¥2,345,678                         │
│  │   环形图: 投资45% | 房产35% | 现金15% | 车5%      │
│  ├─ 右: 📊 配置健康度                                │
│  │   🟢 健康(85分)                                   │
│  │   ⚠️ 1条预警: 现金不足3月支出                    │
│  └─ 底: 📈 本月现金流 +15,000 (收-支)              │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 2️⃣ 今日3大行动（优先级排序）                       │
│                                                         │
│  ✅ 行动1: 立即行动 | 高优先级                     │
│     💡 用 ¥50,000 现金逢低定投                      │
│     📊 原因: 估值低于历史30%分位数                 │
│     ⏱️  建议时间: 今日  | ▶️ 开始定投              │
│                                                         │
│  ⚠️ 行动2: 本周检查 | 中优先级                      │
│     🏠 房贷利率优化空间                             │
│     📊 当前: 4.8% → 市场现价: 4.3%                 │
│     💰 潜在节省: ¥8,000/年                         │
│     ▶️ 对比方案                                      │
│                                                         │
│  💡 行动3: 月度计划 | 低优先级                      │
│     📊 调整配置偏离: 债券类欠配 8%                 │
│     💰 建议增持: ¥30,000 纯债基金                  │
│     ▶️ 查看基金筛选                                 │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 3️⃣ 市场温度 & 入场时机                              │
│                                                         │
│  🌡️ 市场情绪: 极度恐惧(78/100) 📉                 │
│  📈 估值水位: 低于历史25%分位 🟢                  │
│  🎯 综合信号: 👉 非常适合入场 (85%置信度)         │
│  ⏰ 投资时钟: Q2偏股配置窗口已开启                │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 4️⃣ 家庭资产简表（多成员视图）                       │
│                                                         │
│  👨 主账户 (LeiJiang)                               │
│    ├─ 总资产: ¥1,500,000                           │
│    ├─ 本月: +¥80,000                               │
│    └─ 状态: 🟢 健康                                 │
│                                                         │
│  👩 配偶账户 (Wife)                                 │
│    ├─ 总资产: ¥800,000                            │
│    ├─ 本月: +¥25,000                               │
│    └─ 状态: 🟡 需再平衡                            │
│    └─ ⚠️ 债券配置偏低15%                          │
│                                                         │
│  👶 其他账户                                        │
│    └─ 家庭总计: ¥2,345,678 (+¥105,000)           │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 5️⃣ 风险告警 & 提醒（如有）                          │
│                                                         │
│  🔴 危险级: 1条
│     ├─ 持仓集中度过高（前3大占比 72%）           │
│     └─ 建议分散到 <60%                            │
│                                                         │
│  ⚠️ 警告级: 2条
│     ├─ 现金储备不足                                │
│     └─ 配置偏离目标                                │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ 快捷按钮组                                              │
│  [💰 配置资产] [📋 交易记录] [📊 详细分析] [⚙️ 设置] │
└─────────────────────────────────────────────────────────┘
```

---

## 🔄 数据聚合流程

### 后端新增聚合路由: `/api/cfo-dashboard`

```python
@router.get("/api/cfo-dashboard")
def get_cfo_dashboard(userId: str = "", include_family: bool = True):
    """
    CFO 面板 - 统一聚合路由
    
    返回数据:
    {
        "user_id": "user_123",
        "timestamp": "2026-05-15T10:30:00",
        
        # 核心指标卡
        "netWorth": {
            "total": 2345678.89,
            "healthScore": 85,
            "healthGrade": "🟢 健康",
            "breakdown": {...},  # 来自 calc_unified_networth
            "cashFlow": {...}
        },
        
        # 今日3大行动
        "todayActions": [
            {
                "priority": "high",
                "title": "用 ¥50,000 现金逢低定投",
                "description": "估值低于历史30%分位数",
                "reason": {
                    "type": "valuation_opportunity",
                    "valuationPct": 25,
                    "fgiScore": 78
                },
                "action": {
                    "type": "invest",
                    "amount": 50000,
                    "method": "lumpsum_or_dca"
                },
                "suggestedTime": "today",
                "actionUrl": "/api/show-invest-modal"
            },
            ...
        ],
        
        # 市场温度
        "marketMood": {
            "temperatureScore": 78,
            "temperatureLevel": "极度恐惧",
            "timingSignal": "🟢 非常适合入场",
            "valuationPct": 25,
            "fgiScore": 78,
            "confidence": 0.85
        },
        
        # 家庭成员简表
        "familyMembers": [
            {
                "userId": "user_123",
                "name": "LeiJiang",
                "role": "owner",
                "totalAssets": 1500000,
                "monthChange": 80000,
                "healthGrade": "🟢",
                "warnings": []
            },
            ...
        ],
        
        # 风险告警
        "alerts": [
            {
                "level": "danger",
                "title": "持仓集中度过高",
                "message": "前3大占比 72%，建议分散到 <60%"
            },
            ...
        ]
    }
    """
```

---

## 🎬 "今日3大行动"生成算法

### 优先级排序规则

```python
def generate_today_actions(user_data) -> List[Action]:
    """
    优先级排序规则（从高到低）:
    
    1️⃣ 风险规避 (Risk Avoidance)
       - 集中度 > 70%: 立即分散
       - 负债占比 > 50%: 立即还债
       - 应急金 < 3月支出: 立即补充
    
    2️⃣ 机会捕捉 (Opportunity)
       - 估值百分位 < 30 + 有现金: 逢低投资
       - 极度恐惧 (FGI > 75): 加仓信号
       - 配置偏低端资产: 均衡投资
    
    3️⃣ 收益优化 (Optimization)
       - 止盈信号 (浮盈 > 目标): 获利了结
       - 配置再平衡: 定期调整
       - 债券利率优化: 月度检查
    """
    
    actions = []
    
    # 收集所有可能的行动
    candidates = [
        check_risk_concentration(user_data),      # 集中度检测
        check_emergency_reserve(user_data),       # 应急储备检测
        check_liability_ratio(user_data),         # 负债比检测
        check_valuation_opportunity(user_data),   # 估值机会检测
        check_fgi_extreme(user_data),             # 恐惧贪婪极值
        check_profit_taking(user_data),           # 止盈检测
        check_allocation_rebalance(user_data),    # 配置再平衡
        check_debt_rate_optimization(user_data)   # 债券优化
    ]
    
    # 按优先级分类
    for candidate in candidates:
        if candidate["priority"] == "high":
            actions.append(candidate)
        elif candidate["priority"] == "medium" and len(actions) < 3:
            actions.append(candidate)
        elif candidate["priority"] == "low" and len(actions) < 3:
            actions.append(candidate)
    
    # 返回前3个
    return actions[:3]
```

### 示例行动卡数据

```python
# 示例1: 逢低定投
{
    "priority": "high",
    "title": "用 ¥50,000 现金逢低定投",
    "emoji": "✅",
    "description": "沪深300估值处于历史低位",
    "reason": {
        "type": "valuation_opportunity",
        "valuationPct": 25,
        "fgiScore": 78,
        "insight": "估值低于历史30%分位数，恐惧贪婪指数极度恐惧，历史上是最佳买入窗口"
    },
    "action": {
        "type": "invest",
        "amount": 50000,
        "products": ["沪深300指数基金", "中证500"],
        "method": "一次性或分次"
    },
    "expectedReturn": "3年平均 12-15% (历史数据)",
    "suggestedTime": "今日或明日开盘",
    "actionUrl": "/show-invest-modal",
    "priority_reason": "时间敏感，机会窗口可能关闭"
}

# 示例2: 房贷利率优化
{
    "priority": "medium",
    "title": "房贷利率优化空间",
    "emoji": "⚠️",
    "description": "检查重新定价或转贷机会",
    "reason": {
        "type": "debt_optimization",
        "currentRate": 4.8,
        "marketRate": 4.3,
        "insight": "市场利率下降50bp，有优化空间"
    },
    "action": {
        "type": "check_debt",
        "currentAmount": 1000000,
        "currentRate": 4.8,
        "suggestedRate": 4.3,
        "annualSaving": 5000
    },
    "actionUrl": "/show-debt-optimization",
    "priority_reason": "持续性收益，但需要主动检查"
}

# 示例3: 配置再平衡
{
    "priority": "medium",
    "title": "调整配置偏离: 债券类欠配 8%",
    "emoji": "💡",
    "description": "按目标配置调整资产比例",
    "reason": {
        "type": "allocation_rebalance",
        "current": {"stock": 55, "bond": 22, "cash": 23},
        "target": {"stock": 50, "bond": 30, "cash": 20},
        "deviation": {"stock": +5, "bond": -8, "cash": +3},
        "insight": "债券类配置低于目标，在当前估值下适合增加"
    },
    "action": {
        "type": "rebalance",
        "increaseAsset": "纯债基金",
        "suggestedAmount": 30000,
        "products": ["中债总财富指数", "招商纯债"]
    },
    "actionUrl": "/show-fund-selector",
    "priority_reason": "月度计划，不紧急"
}
```

---

## 💻 前端实现步骤

### Step 1: 创建新的 CFO Dashboard 页面结构

```javascript
// pages/cfo-dashboard.js (新建)
function renderCFODashboard() {
  currentPage = 'cfo-dashboard';
  
  // 并行加载所有数据
  const [nw, actions, mood, family, alerts] = await Promise.all([
    fetchUnifiedNetworth(),
    fetchTodayActions(),
    fetchMarketMood(),
    fetchFamilyMembers(),
    fetchAlerts()
  ]);
  
  renderCFOTemplate({
    netWorth: nw,
    todayActions: actions,
    marketMood: mood,
    familyMembers: family,
    alerts: alerts
  });
}
```

### Step 2: 更新首页 landing.js

```javascript
// 在 renderLanding() 中替换现有的卡片逻辑
// 改为调用 CFO Dashboard 组件

// 之前: 多个异步加载 loadSignals(), loadDailyFocus() 等
// 之后: 单次调用 fetchCFODashboard()，一次性获取所有数据
```

### Step 3: 添加交互逻辑

```javascript
// 每个行动卡都有 CTA (Call-To-Action) 按钮
function performTodayAction(actionId) {
  // 根据 actionId 跳转到对应的执行页面
  // 如: 逢低定投 → 打开投资模态框
  //    房贷优化 → 打开债务优化页面
  //    配置再平衡 → 打开基金筛选
}
```

---

## 📊 后端实现步骤

### Step 1: 实现行动生成引擎

```python
# backend/services/cfo_actions.py (新建)

def generate_today_actions(user_id: str) -> List[Dict]:
    """生成"今日3大行动"的核心引擎"""
    
    # 1. 获取基础数据
    nw_data = calc_unified_networth(user_id)
    signals = get_signals(portfolio_data)
    timing = get_timing_advice()
    
    # 2. 检测所有可能的行动
    all_actions = []
    
    # 风险规避类
    if check_concentration_risk(nw_data):
        all_actions.append(create_concentration_action(nw_data))
    
    if check_emergency_reserve(nw_data):
        all_actions.append(create_emergency_action(nw_data))
    
    # 机会捕捉类
    if timing.get("signal") in ["🟢", "🟡"]:
        all_actions.append(create_invest_action(
            valuation=timing.get("valuationPct"),
            fgi=timing.get("fgi")
        ))
    
    # 收益优化类
    for signal in signals:
        if signal["type"] == "take_profit":
            all_actions.append(create_profit_taking_action(signal))
        elif signal["type"] == "rebalance":
            all_actions.append(create_rebalance_action(signal))
    
    # 3. 排序 + 返回前3个
    all_actions.sort(key=lambda x: get_priority_score(x))
    return all_actions[:3]


def get_priority_score(action: Dict) -> float:
    """计算行动的优先级分数（越高越优先）"""
    score = 0.0
    
    # 基础优先级权重
    priority_weight = {
        "high": 1000,
        "medium": 100,
        "low": 10
    }
    score += priority_weight.get(action["priority"], 0)
    
    # 时间敏感度加权
    if action.get("timeframe") == "today":
        score += 500
    elif action.get("timeframe") == "this_week":
        score += 100
    
    # 潜在收益加权
    if action.get("potentialSaving"):
        score += action["potentialSaving"] / 1000
    
    return score
```

### Step 2: 实现聚合路由

```python
# backend/api/cfo.py (新建)

from fastapi import APIRouter
from services.cfo_actions import generate_today_actions
from services.unified_networth import calc_unified_networth
from services.data_layer import get_valuation_percentile, get_fear_greed_index

router = APIRouter(tags=["CFO Dashboard"])

@router.get("/api/cfo-dashboard")
def get_cfo_dashboard(userId: str = ""):
    """CFO 面板统一聚合路由"""
    if not userId:
        return {"error": "userId required"}
    
    # 并行获取所有数据
    nw_data = calc_unified_networth(userId)
    today_actions = generate_today_actions(userId)
    
    timing = get_timing_advice()
    fgi = get_fear_greed_index()
    
    family_data = get_family_summary(userId) if is_pro_mode(userId) else None
    
    return {
        "netWorth": nw_data,
        "todayActions": today_actions,
        "marketMood": {
            "temperatureScore": fgi.get("score", 50),
            "temperatureLevel": fgi.get("level", "中性"),
            "timingSignal": timing.get("verdict"),
            "valuationPct": timing.get("valuationPct"),
            "confidence": timing.get("confidence")
        },
        "familyMembers": family_data,
        "timestamp": datetime.now().isoformat()
    }
```

---

## 🎨 UI 组件设计

### 行动卡片组件

```html
<div class="action-card" data-priority="high">
  <div class="action-header">
    <span class="action-emoji">✅</span>
    <h3>用 ¥50,000 现金逢低定投</h3>
    <span class="priority-badge">高优先级</span>
  </div>
  
  <div class="action-reason">
    <p>📊 原因: 估值低于历史30%分位数</p>
    <p>😟 情绪: 市场极度恐惧 (78/100)</p>
  </div>
  
  <div class="action-details">
    <div class="detail-item">
      <span class="label">建议金额:</span>
      <span class="value">¥50,000</span>
    </div>
    <div class="detail-item">
      <span class="label">建议产品:</span>
      <span class="value">沪深300 / 中证500</span>
    </div>
    <div class="detail-item">
      <span class="label">预期收益:</span>
      <span class="value text-green">3年 +12-15%</span>
    </div>
  </div>
  
  <div class="action-footer">
    <span class="timeframe">⏱️ 建议时间: 今日</span>
    <button class="action-btn primary" onclick="startInvest()">
      ▶️ 开始定投
    </button>
  </div>
</div>
```

---

## ⏰ 实施时间表

| 阶段 | 任务 | 工时 | 预计完成 |
|------|------|------|---------|
| Phase 1 | 后端: `/api/cfo-dashboard` + 行动生成算法 | 8h | 5/16 |
| Phase 2 | 前端: 新建 cfo-dashboard.js | 6h | 5/17 |
| Phase 3 | UI 设计 + 组件开发 | 8h | 5/18 |
| Phase 4 | 集成 + 联调 + 优化 | 6h | 5/19 |
| Phase 5 | 测试 + 灰度发布 | 4h | 5/20 |

---

## ✅ 成功指标

- [ ] 首页加载时间 < 2s (并行数据加载)
- [ ] 用户点击"行动卡"的 CTR > 30%
- [ ] 每日平均执行 1.5+ 个行动
- [ ] 用户留存率提升 15%+
- [ ] NPS (Net Promoter Score) 提升 10+

