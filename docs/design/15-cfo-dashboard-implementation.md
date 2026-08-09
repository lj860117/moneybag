# 钱袋子 — 家庭CFO面板实现指南

## 快速总结

✅ **好消息**：系统已产出8类提醒信号，你只需做前端展示层

❌ **不需要**：
- 不需要新建后端API（都已存在）
- 不需要改规则引擎（已完成）
- 不需要爬新闻源（signal_scout已做）

⚡ **只需做**：
- 前端UI组件：`/pages/cfo-dashboard.js`
- 聚合逻辑：把 8 个不同格式的信号统一成 TOP 3
- 文案翻译：技术信号 → 人话建议

---

## 第一步：理解信号格式 & 优先级

### 信号优先级（从高到低）

```javascript
// 等级 1: 风险红灯 (must-do)
{
  level: "danger",
  type: "stop_loss",  // 止损
  emoji: "🚨",
  urgency: "immediate"
}

// 等级 2: 风险黄灯 (should-do)
{
  level: "warning",
  type: "concentration",
  emoji: "⚠️",
  urgency: "today"
}

// 等级 3: 信息提示 (nice-to-know)
{
  level: "info",
  type: "timing",
  emoji: "💡",
  urgency: "optional"
}
```

### 信号来源对照表

| API 端点 | 返回字段 | 优先级 | 示例 |
|---------|--------|------|------|
| `/api/portfolio/risk-actions` | `actions[].level` | 1-2 | 回撤告警、仓位超限 |
| `/api/signals` | `severity` | 1-3 | 入场时机、再平衡 |
| `/api/stock-monitor/scan` | `signals[].level` | 1-2 | 个股止盈/止损 |
| `/api/signal-scout/latest` | `relevance` | 2-3 | 新闻、增减持 |

---

## 第二步：前端组件架构

### 新增文件

```
pages/
├── cfo-dashboard.js      # 新增：CFO面板主组件
├── cfo-alerts.js         # 新增：提醒聚合引擎
└── cfo-translator.js     # 新增：技术信号→人话翻译库
```

### `pages/cfo-dashboard.js` 框架

```javascript
/**
 * 家庭CFO面板 — 今日最重要的1-3条提醒
 */

const CFO_DASHBOARD_CONFIG = {
  maxAlerts: 3,
  refreshInterval: 300000,  // 5分钟
  dataRetentionDays: 7,
};

let cfoDashboardState = {
  alerts: [],           // TOP 3 提醒
  lastUpdateTime: null,
  isLoading: false,
  errors: [],
};

function renderCfoDashboard() {
  const container = document.getElementById('cfo-dashboard');
  if (!container) return;
  
  if (cfoDashboardState.isLoading) {
    container.innerHTML = '<div class="loading">加载中...</div>';
    return;
  }
  
  let html = `
    <div class="cfo-panel">
      <header class="cfo-header">
        <h2>📌 今日需要关注 (${cfoDashboardState.alerts.length}/3)</h2>
        <button class="btn-refresh" onclick="refreshCfoDashboard()">↻ 刷新</button>
      </header>
      
      <div class="alerts-container">
  `;
  
  if (cfoDashboardState.alerts.length === 0) {
    html += '<div class="empty-state">✅ 今日暂无提醒，继续持有</div>';
  } else {
    cfoDashboardState.alerts.forEach((alert, index) => {
      html += renderAlertCard(alert, index + 1);
    });
  }
  
  html += `
      </div>
      
      <footer class="cfo-footer">
        最后更新: ${cfoDashboardState.lastUpdateTime || '未更新'}
      </footer>
    </div>
  `;
  
  container.innerHTML = html;
}

function renderAlertCard(alert, priority) {
  const severityClass = {
    'danger': 'alert-danger',
    'warning': 'alert-warning',
    'info': 'alert-info',
  }[alert.level] || 'alert-info';
  
  return `
    <div class="alert-card ${severityClass}" data-priority="${priority}">
      <div class="alert-priority">#${priority}</div>
      <div class="alert-icon">${alert.emoji}</div>
      <div class="alert-content">
        <h3>${alert.title}</h3>
        <p>${alert.description}</p>
        ${alert.actions ? `
          <ul class="alert-actions">
            ${alert.actions.map(a => `<li>${a}</li>`).join('')}
          </ul>
        ` : ''}
        <small class="alert-detail">${alert.detail}</small>
      </div>
      <div class="alert-meta">
        <span class="urgency">${alert.urgency}</span>
        <span class="timestamp">${alert.timestamp}</span>
      </div>
    </div>
  `;
}

async function refreshCfoDashboard() {
  cfoDashboardState.isLoading = true;
  renderCfoDashboard();
  
  try {
    const alerts = await aggregateAllAlerts();
    cfoDashboardState.alerts = alerts.slice(0, CFO_DASHBOARD_CONFIG.maxAlerts);
    cfoDashboardState.lastUpdateTime = new Date().toLocaleTimeString('zh-CN');
    cfoDashboardState.errors = [];
  } catch (error) {
    cfoDashboardState.errors.push(error.message);
    console.error('[CFO_DASHBOARD]', error);
  } finally {
    cfoDashboardState.isLoading = false;
    renderCfoDashboard();
  }
}

// 定时刷新
setInterval(refreshCfoDashboard, CFO_DASHBOARD_CONFIG.refreshInterval);

// 初始加载
document.addEventListener('DOMContentLoaded', refreshCfoDashboard);
```

---

## 第三步：信号聚合引擎

### `pages/cfo-alerts.js`

```javascript
/**
 * 信号聚合引擎：从多个API获取信号 → 统一格式 → 优先级排序 → TOP 3
 */

async function aggregateAllAlerts() {
  const alerts = [];
  const errors = [];
  
  // 数据源1: 风控硬阈值 (最高优先级)
  try {
    const riskAlerts = await fetchRiskActions();
    alerts.push(...riskAlerts);
  } catch (e) {
    errors.push(`风控数据获取失败: ${e.message}`);
  }
  
  // 数据源2: 交易信号 (买卖点)
  try {
    const tradeAlerts = await fetchTradeSignals();
    alerts.push(...tradeAlerts);
  } catch (e) {
    errors.push(`交易信号获取失败: ${e.message}`);
  }
  
  // 数据源3: 个股盯盘 (具体持仓异动)
  try {
    const stockAlerts = await fetchStockAlerts();
    alerts.push(...stockAlerts);
  } catch (e) {
    errors.push(`股票异动获取失败: ${e.message}`);
  }
  
  // 数据源4: 信号侦察 (新闻/公告)
  try {
    const scoutAlerts = await fetchScoutSignals();
    alerts.push(...scoutAlerts);
  } catch (e) {
    errors.push(`信号侦察获取失败: ${e.message}`);
  }
  
  // 排序：按优先级 → 按时间戳
  alerts.sort((a, b) => {
    const levelOrder = { danger: 0, warning: 1, info: 2 };
    const aLevel = levelOrder[a.level] || 99;
    const bLevel = levelOrder[b.level] || 99;
    if (aLevel !== bLevel) return aLevel - bLevel;
    return new Date(b.timestamp) - new Date(a.timestamp);
  });
  
  // 去重：按 signalId
  const seen = new Set();
  const unique = [];
  for (const alert of alerts) {
    if (!seen.has(alert.signalId)) {
      seen.add(alert.signalId);
      unique.push(alert);
    }
  }
  
  if (errors.length > 0) {
    console.warn('[CFO_ALERTS] Partial failures:', errors);
  }
  
  return unique;
}

/**
 * API1: 获取风控硬阈值 (danger / warning)
 */
async function fetchRiskActions() {
  const userId = localStorage.getItem('userId') || 'default';
  const resp = await fetch(`/api/portfolio/risk-actions?user_id=${userId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  });
  
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  
  // 转换为统一格式
  return (data.actions || []).map((action, idx) => ({
    signalId: `risk_${idx}`,
    level: action.level,  // danger / warning
    type: 'risk_action',
    category: 'risk_control',
    emoji: action.level === 'danger' ? '🚨' : '⚠️',
    title: action.action.split('\n')[0] || action.rule,
    description: action.action,
    detail: action.detail,
    actions: [],
    urgency: action.level === 'danger' ? 'immediate' : 'today',
    timestamp: new Date().toISOString(),
    priority: action.level === 'danger' ? 10 : 5,
    source: 'risk_control',
  }));
}

/**
 * API2: 获取交易信号 (止盈止损、入场、定投)
 */
async function fetchTradeSignals() {
  const resp = await fetch('/api/signals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ holdings: liveNavData?.portfolio?.holdings || [] })
  });
  
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const signals = await resp.json();
  
  // 过滤：只取 warning 和 opportunity
  const important = signals.filter(s => ['warning', 'opportunity', 'danger'].includes(s.severity));
  
  return important.map((signal, idx) => ({
    signalId: `signal_${idx}`,
    level: signal.severity === 'opportunity' ? 'info' : (signal.severity === 'warning' ? 'warning' : 'danger'),
    type: signal.type,  // timing / take_profit / rebalance etc
    category: 'trading',
    emoji: signal.icon || '💡',
    title: signal.title,
    description: signal.message,
    detail: signal.message,
    actions: [],
    urgency: signal.severity === 'opportunity' ? 'optional' : 'today',
    timestamp: new Date().toISOString(),
    priority: signal.severity === 'opportunity' ? 2 : 5,
    source: 'signals',
  }));
}

/**
 * API3: 获取股票异动 (个股止盈/止损/集中度)
 */
async function fetchStockAlerts() {
  const userId = localStorage.getItem('userId') || 'default';
  const resp = await fetch(`/api/stock-monitor/scan?user_id=${userId}`);
  
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  
  const alerts = [];
  
  // 单只股票异动
  for (const holding of data.holdings || []) {
    for (const signal of holding.signals || []) {
      if (signal.level === 'danger' || signal.level === 'warning') {
        alerts.push({
          signalId: `stock_${holding.code}`,
          level: signal.level,
          type: signal.type,  // take_profit / stop_loss
          category: 'stock_discipline',
          emoji: signal.type === 'take_profit' ? '🎯' : '🚨',
          title: `${holding.name} ${signal.type === 'take_profit' ? '止盈' : '止损'}`,
          description: signal.msg,
          detail: `当前: ${holding.pnlPct}%`,
          actions: [],
          urgency: signal.level === 'danger' ? 'immediate' : 'today',
          timestamp: new Date().toISOString(),
          priority: 8,
          source: 'stock_monitor',
        });
      }
    }
  }
  
  // 组合级纪律检查
  for (const alert of data.discipline_alerts || []) {
    alerts.push({
      signalId: `discipline_${alert.type}`,
      level: alert.level || 'warning',
      type: alert.type,  // concentration / industry_concentration
      category: 'discipline',
      emoji: '⚠️',
      title: alert.msg.split('\n')[0],
      description: alert.msg,
      detail: alert.msg,
      actions: [],
      urgency: 'today',
      timestamp: new Date().toISOString(),
      priority: 6,
      source: 'stock_monitor',
    });
  }
  
  return alerts;
}

/**
 * API4: 获取信号侦察 (新闻、政策、增减持)
 */
async function fetchScoutSignals() {
  const userId = localStorage.getItem('userId') || 'default';
  const resp = await fetch(`/api/signal-scout/latest?user_id=${userId}`);
  
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  
  // 只取高相关性信号
  return (data.signals || [])
    .filter(s => s.relevance >= 50 && s.level === 'warning')
    .slice(0, 3)
    .map((signal, idx) => ({
      signalId: `scout_${idx}`,
      level: signal.level,
      type: signal.type,
      category: 'news',
      emoji: signal.type === 'holder_change' ? '👔' : (
        signal.type === 'unlock' ? '🔓' : (
          signal.type === 'fund_flow' ? '💹' : '📜'
        )
      ),
      title: signal.title,
      description: signal.content || signal.title,
      detail: `相关持仓: ${signal.related_holding || '全市场'}`,
      actions: [],
      urgency: 'today',
      timestamp: signal.time,
      priority: 4,
      source: 'signal_scout',
    }));
}
```

---

## 第四步：文案翻译库

### `pages/cfo-translator.js`

```javascript
/**
 * 技术信号 → "家庭CFO"人话翻译库
 */

const TRANSLATOR = {
  // 风控规则 → 人话
  risk_control: {
    'stop_loss_red_line': {
      title: '🚨 紧急：回撤已达红线',
      description: '你的组合下跌了20%，这是我们设定的"绝对底线"。为了保护本金，强烈建议立即清仓止损。',
      actions: [
        '1. 立即卖出所有股票型基金',
        '2. 保留债券和现金类资产',
        '3. 等待市场底部后重新入场'
      ],
    },
    'stop_loss_warning': {
      title: '⚠️ 警告：回撤已达18%',
      description: '组合下跌18%，触发风控黄灯。建议明天开始逐步降低股票仓位。',
      actions: [
        '1. 股票仓位从当前 → 40%',
        '2. 增配债券基金和货币基金',
        '3. 暂停所有新增定投'
      ],
    },
    'stop_loss_caution': {
      title: '⚠️ 注意：回撤已达15%',
      description: '组合下跌15%，进入警戒区域。建议暂停增加新投入，保持现有仓位观察。',
      actions: [
        '1. 暂停每月定投计划',
        '2. 股票仓位保持或小幅降低',
        '3. 观察持仓基金的基本面'
      ],
    },
  },
  
  // 止盈止损 → 人话
  stock_discipline: {
    'take_profit': {
      title: '🎯 好消息：已达止盈目标',
      description: '这只股票已经赚了20%以上。现在是个好时机，建议分批卖出一半锁定利润。',
      actions: [
        '1. 今天卖出总持仓的50%',
        '2. 剩余50%可以继续持有赚更多',
        '3. 资金可转入债券基金避险'
      ],
    },
    'stop_loss': {
      title: '🚨 止损提醒：已触发止损',
      description: '这只股票已经亏8%了。按照我们的纪律，现在应该止损退出，不要再抱了。',
      actions: [
        '1. 立即卖出所有持仓',
        '2. 不要补仓或加码',
        '3. 反思为什么选择了这个品种'
      ],
    },
  },
  
  // 配置调整 → 人话
  rebalance: {
    'stock_overweight': {
      title: '📈 配置提醒：股票超配',
      description: '你的股票占比超过目标了。建议适当减少股票，增加债券保持平衡。',
      actions: [
        '1. 卖出部分股票型基金',
        '2. 买入债券型基金',
        '3. 目标配置: 股票40% / 债券35% / 现金25%'
      ],
    },
    'bond_underweight': {
      title: '📉 配置提醒：债券不足',
      description: '你的债券配置低于目标，组合波动会比较大。建议增加债券配置。',
      actions: [
        '1. 购入长期债券基金',
        '2. 可以用定投方式分批建仓',
        '3. 债券可以帮你对冲股票风险'
      ],
    },
  },
  
  // 入场时机 → 人话
  timing: {
    'excellent': {
      title: '🟢 非常好的入场机会',
      description: '市场估值处于近3年最低点，这是历史上最佳买入时机。建议加大投入。',
      actions: [
        '1. 增加本月定投金额50%',
        '2. 如有闲钱可以一次性买入',
        '3. 别错过这样的机会，3年才来一次'
      ],
    },
    'good': {
      title: '🟡 适合定投的时机',
      description: '市场估值处于合理水平。继续按计划定投，不用急着加码。',
      actions: [
        '1. 继续按月定投计划',
        '2. 不需要特别加码',
        '3. 坚持长期纪律很重要'
      ],
    },
    'caution': {
      title: '🟠 入场要谨慎',
      description: '市场估值偏高。建议减少这个月的定投，或者等等再说。',
      actions: [
        '1. 本月定投金额减少30%',
        '2. 多留些现金，等更好的机会',
        '3. 不要追高，这很重要'
      ],
    },
  },
  
  // 定投建议 → 人话
  dca: {
    'increase': {
      title: '💰 定投加码建议',
      description: '估值现在很低，我建议你这个月多投点钱，能赚得更多。',
      actions: [
        '1. 本月投 ¥15,000（原计划 ¥10,000）',
        '2. 多投的 ¥5,000 别犹豫',
        '3. 低买是致富的秘诀'
      ],
    },
    'normal': {
      title: '📊 定投照常',
      description: '估值适中，按原计划定投就好。坚持长期纪律最重要。',
      actions: [
        '1. 本月投 ¥10,000（原计划）',
        '2. 风雨无阻地坚持定投',
        '3. 长期复利才是赚钱的王道'
      ],
    },
    'decrease': {
      title: '💡 定投减少建议',
      description: '估值现在有点高，我建议你这个月少投点，保存弹药等机会。',
      actions: [
        '1. 本月投 ¥7,000（原计划 ¥10,000）',
        '2. 少投的 ¥3,000 留作应急',
        '3. 别追高，机会总会来的'
      ],
    },
  },
  
  // 新闻信号 → 人话
  news: {
    'policy_positive': {
      title: '📜 好消息：政策利好',
      description: '央行刚出了利好政策，对你的持仓有帮助。这是个加仓的好机会。',
      actions: [
        '1. 可以增加定投或一次性买入',
        '2. 政策利好通常会维持3-6个月',
        '3. 别太激进，还是要控制风险'
      ],
    },
    'holder_increase': {
      title: '👔 信号：股东增持',
      description: '这个上市公司的大股东在增持，说明对前景看好。值得关注。',
      actions: [
        '1. 持仓大股东增持的公司',
        '2. 这通常是好信号，容易涨',
        '3. 但还是要看业绩能否支撑'
      ],
    },
  },
};

/**
 * 使用方式
 */
function translateAlert(signal) {
  const category = signal.category;  // risk_control / stock_discipline / etc
  const type = signal.type;          // stop_loss / take_profit / etc
  
  const template = TRANSLATOR[category]?.[type];
  if (template) {
    return {
      ...signal,
      title: template.title,
      description: template.description,
      actions: template.actions,
    };
  }
  
  return signal;  // fallback：原样返回
}
```

---

## 第五步：样式（CSS）

### 添加到 `styles.css`

```css
/* ===== CFO Dashboard ===== */

.cfo-panel {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  padding: 20px;
  margin: 20px 0;
  color: white;
}

.cfo-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.cfo-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.btn-refresh {
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.5);
  color: white;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s;
}

.btn-refresh:hover {
  background: rgba(255, 255, 255, 0.3);
}

.alerts-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.alert-card {
  background: white;
  color: #333;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  gap: 12px;
  border-left: 4px solid #ccc;
  transition: all 0.3s;
}

.alert-card.alert-danger {
  border-left-color: #ef4444;
  background: #fef2f2;
}

.alert-card.alert-warning {
  border-left-color: #f59e0b;
  background: #fffbeb;
}

.alert-card.alert-info {
  border-left-color: #3b82f6;
  background: #eff6ff;
}

.alert-priority {
  font-size: 12px;
  color: #999;
  font-weight: 600;
}

.alert-icon {
  font-size: 28px;
  line-height: 1;
}

.alert-content {
  flex: 1;
}

.alert-content h3 {
  margin: 0 0 8px 0;
  font-size: 16px;
  font-weight: 600;
}

.alert-content p {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #666;
  line-height: 1.4;
}

.alert-actions {
  margin: 10px 0;
  padding-left: 20px;
  font-size: 13px;
}

.alert-actions li {
  margin: 4px 0;
  color: #555;
}

.alert-detail {
  display: block;
  margin-top: 8px;
  color: #999;
  font-size: 12px;
}

.alert-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: space-between;
  min-width: 80px;
}

.urgency {
  font-size: 11px;
  padding: 4px 8px;
  background: #e5e7eb;
  border-radius: 4px;
  font-weight: 600;
}

.timestamp {
  font-size: 12px;
  color: #999;
}

.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: #888;
  font-size: 16px;
}

.loading {
  text-align: center;
  padding: 20px;
  color: #666;
}

@media (max-width: 768px) {
  .alert-card {
    flex-direction: column;
  }
  
  .alert-meta {
    flex-direction: row;
    justify-content: space-between;
  }
}
```

---

## 第六步：集成到 app.js

### 在 `index.html` 中添加

```html
<!-- 确保以下顺序加载 -->
<script src="pages/cfo-translator.js"></script>
<script src="pages/cfo-alerts.js"></script>
<script src="pages/cfo-dashboard.js"></script>
```

### 在 `app.js` 中添加路由

```javascript
const PAGES = {
  // ... 其他页面
  'cfo-dashboard': 'CFO面板',
};

// 在 navigateTo 中添加
function navigateTo(page) {
  currentPage = page;
  
  const pageMap = {
    'cfo-dashboard': renderCfoDashboard,
    // ... 其他页面映射
  };
  
  if (pageMap[page]) {
    pageMap[page]();
  }
}
```

### 在导航栏中添加入口

```javascript
// 在前端菜单中添加
const menuItems = [
  { name: 'overview', label: '📊 首页' },
  { name: 'cfo-dashboard', label: '📌 CFO面板' },  // 新增
  // ... 其他菜单项
];
```

---

## 第七步：测试检查清单

- [ ] 风控信号能正确获取并展示
- [ ] 交易信号能正确获取并展示
- [ ] 个股异动信号能正确获取并展示
- [ ] 新闻信号能正确获取并展示
- [ ] 优先级排序正确（danger > warning > info）
- [ ] 最多显示 3 条提醒
- [ ] 自动刷新每 5 分钟生效
- [ ] 手动刷新按钮正常
- [ ] 移动端适配正常
- [ ] 文案翻译自然流畅
- [ ] 没有重复提醒

---

## 常见问题

### Q1: 如何处理 API 超时？
A: 在 `cfo-alerts.js` 的 fetch 中添加超时控制

```javascript
async function fetchWithTimeout(url, options, timeout = 5000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  
  try {
    const resp = await fetch(url, { ...options, signal: controller.signal });
    return resp;
  } finally {
    clearTimeout(timeoutId);
  }
}
```

### Q2: 如何处理部分 API 失败？
A: 当前已经实现了 graceful fallback，每个 API 调用独立 try-catch

### Q3: 能否自定义刷新频率？
A: 修改 `CFO_DASHBOARD_CONFIG.refreshInterval` (毫秒)

### Q4: 历史提醒如何保存？
A: 建议加到 localStorage（Phase 2 功能）

---

## 部署检查清单

- [ ] 新增 3 个 JS 文件已提交
- [ ] styles.css 已更新
- [ ] app.js 路由已添加
- [ ] index.html 脚本顺序正确
- [ ] 所有 API 端点已测试
- [ ] 没有浏览器控制台错误
- [ ] 移动端适配已验证

---

