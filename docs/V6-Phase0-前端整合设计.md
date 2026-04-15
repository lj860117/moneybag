# MoneyBag V6 Phase 0：前端整合设计

> 📅 创建时间：2026-04-16  
> 📝 版本：v2.3（技术栈校准终版）  
> 🎯 目标：在 V6 功能开发前，先整合前端展示，让后续新功能有"家"可归  
> 👥 用户：厉害了哥（LeiJiang）、部落格里（BuLuoGeLi）

---

## 〇、实际技术栈（已确认）

```
┌─────────────────────────────────────────────────┐
│  后端：FastAPI 0.110+ / Python 3.11 / Pydantic 2│
│  前端：原生 JS SPA (app.js 309KB) + Chart.js 4  │
│  数据：JSON 文件 (data/users/sha256.json)        │
│  缓存：Python dict 内存缓存                      │
│  AI  ：DeepSeek V3 + R1 (llm_gateway.py)        │
│  数据源：AKShare + Tushare (5000积分)             │
│  推送：企业微信 (wxwork_push.py)                  │
│                                                  │
│  部署：                                           │
│  ├── 前端：GitHub Pages (Actions 自动部署)        │
│  ├── 后端：腾讯云 150.158.47.189:8000            │
│  │         Ubuntu 22.04 / systemd / uvicorn ×2   │
│  └── 备用：Railway (美国，回退方案)               │
│                                                  │
│  认证：邀请码制 + SHA256(userId) 路径隔离          │
│  GitHub：https://github.com/lj860117/moneybag    │
│  服务器代码路径：/opt/moneybag/                    │
└─────────────────────────────────────────────────┘
```

### Phase 0 原则：不动底层，只加功能

| 维度 | Phase 0 做法 | 理由 |
|------|-------------|------|
| 前端框架 | **继续原生 JS** | 309KB app.js 已上线可用，迁移 Vue 需 2-3 周 |
| 数据库 | **继续 JSON 文件** | 2 个用户够用，新增字段加 JSON 字段即可 |
| 认证 | **保持 SHA256 隔离** | 有效且已在用 |
| 缓存 | **继续内存 dict** | 重启丢缓存 R1 凌晨会重跑 |
| 推送 | **继续企业微信** | 已有 wxwork_push.py，改造而非重写 |
| 进程管理 | **继续 systemd** | 已配置好 moneybag.service |

---

## 一、问题背景

### 1.1 后端已有但前端未展示的 API

> 以下 API 已在 `backend/main.py` (3262行) 中定义，但 `app.js` 未调用

| # | 后端路由 | 功能 | 优先级 | Phase 0 处理 |
|---|----------|------|--------|--------------|
| 1 | `/news/deep-impact` | DeepSeek 新闻深度分析 | P0 | ✅ Day 2 接入 |
| 2 | `/news/risk-assess` | DeepSeek 新闻风控评估 | P0 | ✅ Day 2 接入 |
| 3 | `/stock-holdings/analyze` | 股票持仓 7-Skill 分析 | P0 | ✅ Day 2 接入 |
| 4 | `/fund-holdings/analyze` | 基金持仓 7-Skill 分析 | P0 | ✅ Day 2 接入 |
| 5 | `/daily-signal/interpret` | DeepSeek 信号解读 | P1 | ✅ Day 3 接入 |
| 6 | `/timing` | 入场时机建议 | P1 | ✅ Day 3 接入 |
| 7 | `/smart-dca` | 智能定投建议 | P1 | ✅ Day 3 接入 |
| 8 | `/rl-position/portfolio/{uid}` | RL 仓位建议 | P2 | ⏸️ V6 阶段 |
| 9 | `/stock/financials/{code}` | 个股财务数据 | P2 | ⏸️ V6 阶段 |
| 10 | `/agent/memory/{uid}` | Agent 记忆查看 | P2 | ⏸️ V6 阶段 |

### 1.2 五个孤岛模块 (`backend/services/`)

| 模块 | 大小 | Phase 0 处理 | 说明 |
|------|------|--------------|------|
| `ai_predictor.py` | 20.6KB | ⏸️ V6 | 需要与 signal.py 整合 |
| `genetic_factor.py` | 16.0KB | ⏸️ V6 | 复杂度高 |
| `rl_position.py` | 16.2KB | ⏸️ V6 | 对应 P2 API |
| `monte_carlo.py` | 18.8KB | ⏸️ V6 | 需要与 risk.py 整合 |
| `portfolio_optimizer.py` | 14.3KB | ✅ **Day 3 激活** | 资产配置建议依赖 |

### 1.3 必修 BUG

| BUG | 位置 | Day | 修复方式 | 验证方法 |
|-----|------|-----|----------|----------|
| `get_memory_summary` 拼错 | `agent_memory.py` 或 `agent_engine.py` 中调用处 | Day 1 | `grep -rn "get_memory_summary" backend/` → 全局替换为 `build_memory_summary` | 企微聊天问"上次分析了什么" |
| `save_context()` 未调用 | `agent_memory.py` 中定义，`main.py` chat 路由未调用 | Day 1 | 在 `/api/chat` 路由的 AI 响应返回后调用 | 连续两轮对话验证上下文串联 |
| `chat.py` 死代码 | `backend/services/` 目录下（如存在） | Day 1 | `grep -rn "import.*chat" backend/` 确认零引用后删除 | grep 结果为空 |

---

## 二、多账号数据隔离

### 2.1 现有用户体系

> 当前认证：`routers/profiles.py` 邀请码制 + `persistence.py` SHA256 路径隔离

```python
# 现有代码 (persistence.py)
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"
```

### 2.2 Phase 0 用户配置扩展

> 在现有 JSON 用户数据中新增字段，不引入新表

```python
# 在 load_user() 返回的 dict 中新增以下字段（兼容旧数据用 .get() 读取）
USER_DEFAULTS = {
    'display_mode': 'simple',        # 'simple' | 'pro'
    'risk_profile': 'balanced',      # 保守/平衡/成长/激进
    'push_preferences': {
        'morning_brief': True,
        'closing_review': True,
        'risk_alert': True,
        'trade_signal': True,
        'breaking_news': True,
    },
    'watchlist_config': {
        'stop_loss_pct': -0.08,
        'take_profit_pct': 0.20,
        'price_alert_range': 0.05,
    },
}

# 两个用户的个性化配置
USER_OVERRIDES = {
    'LeiJiang': {
        'display_mode': 'pro',
        'risk_profile': 'growth',
        'push_preferences': {
            'morning_brief': True, 'closing_review': True,
            'risk_alert': True, 'trade_signal': True, 'breaking_news': True,
        },
        'watchlist_config': {
            'stop_loss_pct': -0.10, 'take_profit_pct': 0.25, 'price_alert_range': 0.05,
        },
    },
    'BuLuoGeLi': {
        'display_mode': 'simple',
        'risk_profile': 'balanced',
        'push_preferences': {
            'morning_brief': True, 'closing_review': False,
            'risk_alert': True, 'trade_signal': False, 'breaking_news': False,
        },
        'watchlist_config': {
            'stop_loss_pct': -0.05, 'take_profit_pct': 0.15, 'price_alert_range': 0.03,
        },
    },
}
```

### 2.3 隔离矩阵

| 功能模块 | 隔离方式 | 说明 |
|----------|----------|------|
| 持仓数据 | SHA256(userId).json | 已有，各自独立文件 |
| 交易流水 | 同上（portfolio.transactions） | V4 交易流水制已实现 |
| 聊天记忆 | `data/{userId}/memory/` | agent_memory.py 已实现 |
| 分析结果 | 按 userId 参数隔离 | API 调用时传 userId |
| 风险画像 | JSON 用户文件新增字段 | Phase 0 新增 |
| 盯盘阈值 | JSON 用户文件新增字段 | Phase 0 新增 |
| 推送偏好 | JSON 用户文件新增字段 | Phase 0 新增 |
| 模式偏好 | JSON 用户文件新增字段 | Phase 0 新增 |
| 市场数据 | 共享（内存 dict 缓存） | 不需要隔离 |
| R1 分析缓存 | 共享（内存 dict 缓存） | 宏观分析对所有人一样 |

---

## 三、Simple/Pro 双模式

### 3.1 后端：用户偏好 API

> 新增 2 个端点到 `main.py`

```python
# === 新增 API：用户偏好 ===

@app.get("/api/user/preference")
async def get_user_preference(userId: str):
    """获取用户偏好（Simple/Pro模式、推送、盯盘阈值）"""
    user = load_user(userId)
    defaults = USER_DEFAULTS.copy()
    overrides = USER_OVERRIDES.get(userId, {})
    
    return {
        "display_mode": user.get("display_mode", overrides.get("display_mode", defaults["display_mode"])),
        "risk_profile": user.get("risk_profile", overrides.get("risk_profile", defaults["risk_profile"])),
        "push_preferences": user.get("push_preferences", overrides.get("push_preferences", defaults["push_preferences"])),
        "watchlist_config": user.get("watchlist_config", overrides.get("watchlist_config", defaults["watchlist_config"])),
    }

@app.put("/api/user/preference")
async def update_user_preference(userId: str, body: dict):
    """更新用户偏好"""
    user = load_user(userId)
    
    for key in ["display_mode", "risk_profile", "push_preferences", "watchlist_config"]:
        if key in body:
            user[key] = body[key]
    
    save_user(user)
    return {"success": True}
```

### 3.2 前端：模式切换 (app.js)

> 在 `app.js` 中新增渲染逻辑

```javascript
// === Simple/Pro 模式切换 ===

let currentMode = localStorage.getItem('display_mode') || 'simple';

function renderModeSwitch() {
    return `
    <div class="mode-switch">
        <button class="${currentMode === 'simple' ? 'active' : ''}" 
                onclick="setMode('simple')">🌸 简洁</button>
        <button class="${currentMode === 'pro' ? 'active' : ''}" 
                onclick="setMode('pro')">🔧 专业</button>
    </div>`;
}

async function setMode(mode) {
    const oldMode = currentMode;
    currentMode = mode;
    localStorage.setItem('display_mode', mode);
    
    try {
        await fetch(`${ENGINE}/api/user/preference?userId=${USER_ID}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ display_mode: mode })
        });
        renderCurrentTab();  // 重新渲染当前页面
    } catch (e) {
        currentMode = oldMode;  // 回滚
        localStorage.setItem('display_mode', oldMode);
        showToast('切换失败，请重试');
    }
}

// 启动时从后端同步
async function syncPreferences() {
    try {
        const res = await fetch(`${ENGINE}/api/user/preference?userId=${USER_ID}`);
        const prefs = await res.json();
        currentMode = prefs.display_mode || 'simple';
        localStorage.setItem('display_mode', currentMode);
    } catch (e) {
        // 降级：用 localStorage 缓存
    }
}
```

### 3.3 Simple 模式首页 (renderLanding 改造)

```
┌─────────────────────────────────────┐
│  🏠 MoneyBag          [🌸简洁|🔧专业] │
├─────────────────────────────────────┤
│                                     │
│  💰 家庭净资产                       │
│  ¥ 520,000                         │
│  📈 今日 +0.23% (+¥1,200)          │
│  厉害了哥 ¥320,000 / 部落格里 ¥200,000│
│                                     │
├─────────────────────────────────────┤
│  📡 今日信号                         │
│  🟢 沪深300 信号良好                 │
│  🟡 消费行业 观望                    │
│  🔴 招商银行 止盈提醒               │
├─────────────────────────────────────┤
│  🏥 持仓健康度                       │
│  整体：🟢 良好 (85/100)             │
│  ├─ 分散度：🟢 优秀                 │
│  ├─ 风险敞口：🟡 适中               │
│  └─ 盈亏状态：🟢 盈利               │
├─────────────────────────────────────┤
│  🎯 AI 管家准确率                    │
│  近7日：65% │ 近30日：62%           │
├─────────────────────────────────────┤
│  💬 问问管家                         │
│  [沪深300现在能买吗？    ] [发送]    │
└─────────────────────────────────────┘
```

### 3.4 Pro 模式额外展示

Simple 基础上追加：
- 技术指标详情（MACD、RSI、布林带）— 已有 `technical.py`
- 因子分析结果（30+ 因子）— 已有 `factor_data.py`
- 风控数据（VaR、回撤）— 已有 `risk.py`
- 模块执行状态 — 已有 `module_registry.py`
- 判断追踪 — 已有 `judgment_tracker.py`

---

## 四、前端 API 接入方案

### 4.1 通用封装

> 在 `app.js` 中新增统一请求函数

```javascript
// === API 封装 ===
const ENGINE = localStorage.getItem('moneybag_engine') || 'http://150.158.47.189:8000';

async function apiCall(path, options = {}) {
    const url = `${ENGINE}${path}`;
    const timeout = options.timeout || 10000;
    
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeout);
        
        const res = await fetch(url, {
            ...options,
            signal: controller.signal,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
        });
        clearTimeout(timer);
        
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (e) {
        if (e.name === 'AbortError') {
            return { error: true, message: '请求超时，请稍后再试' };
        }
        return { error: true, message: e.message || '网络异常' };
    }
}

// R1 深度分析专用（180s 超时 + 思考动画）
async function apiCallR1(path, options = {}) {
    showThinkingAnimation('AI 正在深度分析...');
    try {
        return await apiCall(path, { ...options, timeout: 180000 });
    } finally {
        hideThinkingAnimation();
    }
}
```

### 4.2 超时策略

| API 类型 | 超时 | 前端表现 |
|----------|------|----------|
| 普通查询（持仓/净资产/信号） | 10s | 骨架屏 → 数据/错误卡片 |
| V3 AI 响应（聊天/简单问答） | 15s | "管家正在查询..." |
| R1 深度分析（买卖决策/诊断） | **180s** | **思考动画 + "预计30-60秒"** |
| 新闻深度分析（deep-impact） | 30s | "AI 正在分析新闻影响..." |
| 7-Skill 持仓分析 | 60s | "正在进行多维度分析..." |

### 4.3 前端组件-API 映射表

> 每个组件 = app.js 中的一个 `render*()` 函数

| 前端函数 | 后端 API | 模式 | 刷新策略 |
|----------|----------|------|----------|
| `renderNetWorth()` | `GET /api/portfolio/networth` × 2人 | Both | 手动下拉 |
| `renderSignalCard()` | `GET /api/daily-signal?userId=` | Both | 5min 缓存 |
| `renderHealthScore()` | `GET /api/allocation-advice?userId=` | Both | 首次加载 |
| `renderAccuracy()` | `GET /api/accuracy?userId=` (新增) | Both | 首次加载 |
| `renderChatInput()` | `POST /api/chat` | Both | 实时 |
| `renderHoldingAnalysis()` | `POST /api/stock-holdings/analyze` | **Pro** | 手动触发 |
| `renderFundAnalysis()` | `POST /api/fund-holdings/analyze` | **Pro** | 手动触发 |
| `renderDeepImpact()` | `POST /api/news/deep-impact` | **Pro** | 手动触发 |
| `renderRiskAssess()` | `POST /api/news/risk-assess` | **Pro** | 手动触发 |
| `renderSignalInterpret()` | `POST /api/daily-signal/interpret` | **Pro** | 手动触发 |
| `renderTimingAdvice()` | `GET /api/timing?userId=` | **Pro** | 首次加载 |
| `renderSmartDCA()` | `GET /api/smart-dca?userId=` | **Pro** | 首次加载 |
| `renderAlerts()` | `GET /api/watchlist/alerts?userId=` (新增) | Both | 15s 轮询 |

### 4.4 前端三态模式（所有组件统一）

```javascript
// 通用三态渲染
function renderCard(title, state, content) {
    if (state === 'loading') {
        return `<div class="card skeleton"><h3>${title}</h3><div class="pulse-bar"></div></div>`;
    }
    if (state === 'error') {
        return `<div class="card error"><h3>${title}</h3><p>加载失败</p>
                <button onclick="retry('${title}')">重试</button></div>`;
    }
    if (state === 'empty') {
        return `<div class="card empty"><h3>${title}</h3><p>暂无数据</p></div>`;
    }
    return `<div class="card">${content}</div>`;
}
```

---

## 五、四大增强功能

### 5.1 资产配置建议（首页增强）

**定位**：`renderLanding()` 的「持仓健康度」卡片深化

#### 配置模板（7大资产类别 × 4种风险画像）

```python
# 放在 config.py 或 main.py 顶部
ALLOCATION_TEMPLATES = {
    'conservative': {  # 保守型
        '股票': 0.10, '债券': 0.40, '现金': 0.20, '黄金': 0.10,
        '海外': 0.05, '房产': 0.10, '另类': 0.05,
    },
    'balanced': {      # 平衡型（部落格里）
        '股票': 0.30, '债券': 0.25, '现金': 0.15, '黄金': 0.10,
        '海外': 0.10, '房产': 0.05, '另类': 0.05,
    },
    'growth': {        # 成长型（厉害了哥）
        '股票': 0.50, '债券': 0.15, '现金': 0.10, '黄金': 0.05,
        '海外': 0.10, '房产': 0.05, '另类': 0.05,
    },
    'aggressive': {    # 激进型
        '股票': 0.70, '债券': 0.05, '现金': 0.05, '黄金': 0.03,
        '海外': 0.10, '房产': 0.02, '另类': 0.05,
    },
}
```

#### 后端：激活 `portfolio_optimizer.py` + 新 API

```python
# main.py 新增
from services.portfolio_optimizer import PortfolioOptimizer

@app.get("/api/asset-allocation")
async def asset_allocation(userId: str, mode: str = "simple"):
    """资产配置建议"""
    user = load_user(userId)
    profile = user.get("risk_profile", "balanced")
    optimizer = PortfolioOptimizer()
    
    # 获取当前持仓分布
    portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
    holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
    
    # 计算建议
    try:
        result = optimizer.get_allocation_advice(holdings, profile)
        
        if mode == "simple":
            return {
                "score": result.get("score", 70),
                "level": "good" if result["score"] >= 70 else "warning" if result["score"] >= 40 else "danger",
                "summary": result.get("summary", "配置均衡"),
            }
        else:
            return result  # Pro 返回完整数据
    except Exception as e:
        return {"error": True, "message": str(e)}
```

#### 前端：健康度卡片

```javascript
// Simple: 评分 + 一句话
// Pro: 评分 + 饼图 + 偏离度 + 调整建议
async function renderHealthScore() {
    const data = await apiCall(`/api/asset-allocation?userId=${USER_ID}&mode=${currentMode}`);
    if (data.error) return renderCard('持仓健康度', 'error', '');
    
    if (currentMode === 'simple') {
        const emoji = data.level === 'good' ? '🟢' : data.level === 'warning' ? '🟡' : '🔴';
        return `<div class="card">
            <h3>🏥 持仓健康度</h3>
            <p class="big-score">${emoji} ${data.score}/100</p>
            <p>${data.summary}</p>
        </div>`;
    } else {
        // Pro: 用 Chart.js 画饼图
        return `<div class="card">
            <h3>🏥 持仓健康度 (${data.score}/100)</h3>
            <canvas id="allocationChart"></canvas>
            <div class="deviation-list">${renderDeviationTable(data.deviation)}</div>
        </div>`;
    }
}
```

### 5.2 实时盯盘（智能休眠）

#### 后端：新增盯盘 API + 定时检查

```python
# main.py 新增
import asyncio
from datetime import time as dtime

# 盯盘运行时间：9:25 - 15:05（交易时段 ± 5分钟）
MARKET_OPEN = dtime(9, 25)
MARKET_CLOSE = dtime(15, 5)

# 预警冷却：同一条预警 30 分钟内不重复
_alert_cooldown = {}  # key: f"{userId}_{symbol}_{type}" → last_sent_time

@app.get("/api/watchlist/alerts")
async def watchlist_alerts(userId: str, since: float = 0):
    """获取盯盘预警"""
    user = load_user(userId)
    portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
    holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
    
    # 空仓 → 返回空列表（盯盘休眠）
    if not holdings.get("active"):
        return {"alerts": [], "status": "idle", "message": "空仓观望中"}
    
    # 非交易时间 → 返回空
    now = datetime.now().time()
    if not (MARKET_OPEN <= now <= MARKET_CLOSE):
        return {"alerts": [], "status": "closed", "message": "非交易时间"}
```

#### 空仓时任务调度矩阵

| 任务类型 | 有持仓 | 空仓 | 原因 |
|----------|--------|------|------|
| **实时盯盘** | ✅ 运行 | ❌ idle | 没东西盯 |
| **数据缓存** | ✅ 运行 | ✅ 运行 | 投资建议需要 |
| **R1 凌晨分析** | ✅ 运行 | ✅ 运行 | 找买入时机 |
| **早安简报** | ✅ 持仓导向 | ✅ **机会导向** | 内容切换 |
| **收盘复盘** | ✅ 完整版 | ⚠️ 简化版 | 只说大盘 |

```python
    
    # 检查各持仓预警
    config = user.get("watchlist_config", USER_DEFAULTS["watchlist_config"])
    alerts = []
    
    for h in holdings["active"]:
        pnl_pct = (h["currentNav"] - h["avgNav"]) / h["avgNav"] if h["avgNav"] > 0 else 0
        
        # 止损
        if pnl_pct <= config["stop_loss_pct"]:
            alert = _make_alert(userId, h, "stop_loss", "danger", 
                f"{h['name']} 已亏损 {pnl_pct:.1%}，触及止损线")
            if alert: alerts.append(alert)
        
        # 止盈
        if pnl_pct >= config["take_profit_pct"]:
            alert = _make_alert(userId, h, "take_profit", "warning",
                f"{h['name']} 已盈利 {pnl_pct:.1%}，考虑止盈")
            if alert: alerts.append(alert)
    
    return {"alerts": alerts, "status": "active"}

def _make_alert(userId, holding, alert_type, level, message):
    """生成预警（带 30 分钟冷却）"""
    key = f"{userId}_{holding['code']}_{alert_type}"
    now = time.time()
    
    if key in _alert_cooldown and (now - _alert_cooldown[key]) < 1800:
        return None  # 30 分钟冷却中
    
    _alert_cooldown[key] = now
    return {
        "type": alert_type,
        "symbol": holding["code"],
        "name": holding["name"],
        "level": level,
        "message": message,
        "timestamp": datetime.now().isoformat(),
    }
```

#### 前端：15 秒轮询

```javascript
let alertPolling = null;

function startAlertPolling() {
    if (alertPolling) return;
    alertPolling = setInterval(async () => {
        const data = await apiCall(`/api/watchlist/alerts?userId=${USER_ID}`);
        if (data.alerts && data.alerts.length > 0) {
            showAlertBadge(data.alerts.length);
            // 高危预警弹 toast
            data.alerts.filter(a => a.level === 'danger').forEach(a => {
                showToast(`⚠️ ${a.message}`, 'danger');
            });
        }
    }, 15000);
}

function stopAlertPolling() {
    if (alertPolling) { clearInterval(alertPolling); alertPolling = null; }
}
```

### 5.3 AI 聊天增强

#### 后端：意图分类 + 记忆修复

> 改造 `main.py` 中现有的 `/api/chat` 路由

```python
# 意图分类（嵌入现有 chat 路由）
INTENT_KEYWORDS = {
    # R1 深度思考（优先匹配）
    'buy_decision':  (['能买吗', '可以买', '要不要买', '值得买', '入场', '抄底'], 'llm_heavy'),
    'sell_decision': (['要卖吗', '该卖', '止盈', '止损', '出场', '清仓'], 'llm_heavy'),
    'portfolio':     (['优化持仓', '调仓', '配置建议', '怎么调整'], 'llm_heavy'),
    'risk':          (['风险', '危险', '会跌吗', '安全吗', '回撤'], 'llm_heavy'),
    # V3 快速响应（后匹配）
    'price':         (['多少钱', '什么价', '现价', '股价', '行情'], 'llm_light'),
    'holding':       (['我的持仓', '持有', '仓位', '有多少'], 'llm_light'),
    'qa':            (['什么是', '解释', '定义', '含义'], 'llm_light'),
    'news':          (['新闻', '消息', '动态'], 'llm_light'),
}

def classify_intent(message: str) -> tuple:
    """返回 (intent_name, model_tier)"""
    msg = message.lower()
    # 先匹配 R1 意图（复杂优先）
    for intent, (keywords, tier) in INTENT_KEYWORDS.items():
        if tier == 'llm_heavy' and any(kw in msg for kw in keywords):
            return intent, tier
    # 再匹配 V3 意图
    for intent, (keywords, tier) in INTENT_KEYWORDS.items():
        if tier == 'llm_light' and any(kw in msg for kw in keywords):
            return intent, tier
    return 'unknown', 'llm_light'  # Fallback 用 V3

# 在现有 /api/chat 路由中：
# 1. intent, tier = classify_intent(message)
# 2. 用 llm_gateway.call(prompt, tier) 替代硬编码模型
# 3. 修复：调用 build_memory_summary(userId) 而非 get_memory_summary
# 4. 修复：响应后调用 save_context(userId, message, result)
```

#### 前端：模式适配

```javascript
// /api/chat 响应后，根据模式格式化展示
function renderChatResponse(result) {
    if (currentMode === 'simple') {
        return `<div class="chat-bubble ai">
            <p class="conclusion">${result.emoji || '💡'} ${result.conclusion}</p>
            <p class="reason">${result.one_line_reason || ''}</p>
        </div>`;
    } else {
        return `<div class="chat-bubble ai pro">
            <h4>${result.title || '分析结果'}</h4>
            <p><strong>结论：</strong>${result.conclusion}</p>
            <p><strong>分析：</strong>${result.detailed_analysis || ''}</p>
            <p class="warning"><strong>风险提示：</strong>${result.risk_warning || ''}</p>
            <p class="meta">引擎: ${result.engine} | 耗时: ${result.thinking_time || '-'}s</p>
        </div>`;
    }
}
```

### 5.4 推送内容适配（空仓切换）

#### 后端：改造 `wxwork_push.py`

```python
# 在现有 wxwork_push.py 的推送函数中增加空仓判断
def build_morning_brief(userId: str) -> str:
    """08:30 早安简报 - 根据持仓状态切换内容"""
    user = load_user(userId)
    mode = user.get("display_mode", "simple")
    portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
    holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
    
    has_holdings = bool(holdings.get("active"))
    
    if has_holdings:
        summary = calc_portfolio_summary(holdings)
        if mode == 'simple':
            return f"🌅 早安！\n💰 总资产 ¥{summary['total']:,.0f}\n📈 昨日 {summary['change']:+,.0f}"
        else:
            return f"🌅 早安！持仓回顾\n\n💰 总资产：¥{summary['total']:,.0f}\n📈 昨日：{summary['change']:+,.0f} ({summary['change_pct']:+.2%})\n\n📡 今日关注：\n{get_focus_items(userId)}"
    else:
        # 空仓 → 机会导向
        if mode == 'simple':
            return f"🌅 早安！\n📊 大盘：{get_market_signal_text()}\n💡 建议关注：{get_top_recommendation()}\n空仓观望中 👀"
        else:
            return f"🌅 早安！今日市场机会\n\n📊 大盘信号：{get_market_signal_text()}\n🔥 热门行业：{get_hot_sectors_text()}\n💡 建议关注：{get_recommendations_text()}\n\n空仓观望中，管家持续监控入场时机 👀"
```

---

## 六、家庭资产汇总

### 6.1 后端 API

```python
# main.py 新增
FAMILY_MEMBERS = ['LeiJiang', 'BuLuoGeLi']
NICKNAMES = {'LeiJiang': '厉害了哥', 'BuLuoGeLi': '部落格里'}

@app.get("/api/household/summary")
async def household_summary():
    """家庭资产汇总"""
    members = []
    total_value = 0
    total_change = 0
    
    for uid in FAMILY_MEMBERS:
        user = load_user(uid)
        portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
        holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
        assets = portfolio.get("assets", [])
        
        # 用现有 unified_networth 计算
        from services.unified_networth import calc_net_worth
        nw = calc_net_worth(holdings, assets)
        
        members.append({
            "userId": uid,
            "nickname": NICKNAMES.get(uid, uid),
            "value": nw.get("total", 0),
            "change": nw.get("daily_change", 0),
        })
        total_value += nw.get("total", 0)
        total_change += nw.get("daily_change", 0)
    
    yesterday = total_value - total_change
    change_pct = (total_change / yesterday) if yesterday > 0 else 0.0
    
    return {
        "total_value": total_value,
        "daily_change": total_change,
        "daily_change_pct": change_pct,
        "members": members,
    }
```

---

## 七、补充设计：资产快照 + 聊天记忆清理 + 交易流水分页

### 7.1 资产历史快照（每日 16:00）

> 在 `scripts/cache_warmer.py` 或新建定时脚本中添加

```python
# 每日 16:00（收盘后）记录资产快照到 JSON
def save_daily_snapshot():
    """写入 data/snapshots/{userId}/{date}.json"""
    for uid in FAMILY_MEMBERS:
        user = load_user(uid)
        portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
        holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
        
        snapshot = {
            "date": date.today().isoformat(),
            "total_value": sum(h.get("marketValue", 0) for h in holdings.get("active", [])),
            "stock_value": sum(h.get("marketValue", 0) for h in holdings.get("active", []) if h.get("type") == "stock"),
            "fund_value": sum(h.get("marketValue", 0) for h in holdings.get("active", []) if h.get("type") == "fund"),
        }
        
        snapshot_dir = DATA_DIR / "snapshots" / uid
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / f"{date.today()}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
        )
    
    logger.info("📸 每日资产快照完成")

# 注册到定时任务（systemd timer 或 cron）
# 0 16 * * 1-5 cd /opt/moneybag && /opt/moneybag/venv/bin/python -c "from scripts.snapshot import save_daily_snapshot; save_daily_snapshot()"
```

### 7.2 资产历史 API（用于曲线图）

```python
@app.get("/api/asset-history")
async def asset_history(userId: str, days: int = 90):
    """获取资产历史数据（用于 Chart.js 曲线）"""
    snapshot_dir = DATA_DIR / "snapshots" / userId
    if not snapshot_dir.exists():
        return {"data": [], "message": "暂无历史数据"}
    
    files = sorted(snapshot_dir.glob("*.json"), reverse=True)[:days]
    data = []
    for f in reversed(files):
        try:
            data.append(json.loads(f.read_text(encoding="utf-8")))
        except: pass
    
    return {"data": data}
```

### 7.3 聊天记忆清理策略

```python
# 在 agent_memory.py 中增加
MAX_MEMORY_ROUNDS = 50  # 最多保留 50 轮对话
SUMMARY_WINDOW = 20     # build_memory_summary 取最近 20 条

def cleanup_old_memory(userId: str):
    """清理超过 50 轮的旧记忆"""
    memory_dir = DATA_DIR / userId / "memory"
    if not memory_dir.exists():
        return
    
    files = sorted(memory_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
    if len(files) > MAX_MEMORY_ROUNDS:
        for f in files[:-MAX_MEMORY_ROUNDS]:
            f.unlink()

# 在 save_context() 末尾调用 cleanup_old_memory(userId)
```

### 7.4 交易流水分页 API

```python
@app.get("/api/transactions")
async def get_transactions(userId: str, page: int = 1, pageSize: int = 20, symbol: str = None):
    """分页查询交易流水"""
    user = load_user(userId)
    portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
    transactions = portfolio.get("transactions", [])
    
    # 按日期倒序
    transactions.sort(key=lambda t: t.get("date", ""), reverse=True)
    
    # 按 symbol 筛选
    if symbol:
        transactions = [t for t in transactions if t.get("code") == symbol]
    
    total = len(transactions)
    start = (page - 1) * pageSize
    end = start + pageSize
    
    return {
        "data": transactions[start:end],
        "pagination": {
            "page": page,
            "pageSize": pageSize,
            "total": total,
            "totalPages": (total + pageSize - 1) // pageSize,
        }
    }
```

---

## 七、实施计划（3 天）

### Day 1：框架搭建 + BUG 修复（7h）

| # | 任务 | 时间 | 改动文件 | 验证方法 |
|---|------|------|----------|----------|
| 1.1 | 用户偏好 API (GET/PUT) | 1h | `main.py` + `persistence.py` | curl 读写偏好 |
| 1.2 | Simple/Pro 模式切换 UI | 1.5h | `app.js` + `styles.css` | 切换后刷新保持 |
| 1.3 | 首页 renderLanding() 改造 | 2h | `app.js` | Simple 版首页截图 |
| 1.4 | 修复 `build_memory_summary` | 0.5h | `agent_engine.py` 或调用处 | 聊天问"上次分析了什么" |
| 1.5 | 修复 `save_context()` 未调用 | 0.5h | `main.py` chat 路由 | 连续对话上下文串联 |
| 1.6 | 删除死代码 chat.py | 0.5h | 删除文件 | grep 零引用 |
| 1.7 | 前端三态组件 + apiCall 封装 | 1h | `app.js` | 断网显示错误卡片 |

**Day 1 结束验证**：
```bash
# 后端
curl http://150.158.47.189:8000/api/health
curl http://150.158.47.189:8000/api/user/preference?userId=LeiJiang
# 前端：两个账号分别打开，确认 Simple/Pro 切换正常
```

### Day 2：P0 API 接入（7h）

| # | 任务 | 时间 | 改动文件 | 验证方法 |
|---|------|------|----------|----------|
| 2.1 | 家庭资产汇总 API + 前端 | 1.5h | `main.py` + `app.js` | 首页显示两人合计 |
| 2.2 | `/news/deep-impact` 前端接入 | 1.5h | `app.js` | 资讯页点击新闻展示分析 |
| 2.3 | `/news/risk-assess` 前端接入 | 1h | `app.js` | 风控评估展示 |
| 2.4 | `/stock-holdings/analyze` 前端接入 | 1.5h | `app.js` | Pro 模式可触发分析 |
| 2.5 | `/fund-holdings/analyze` 前端接入 | 0.5h | `app.js` | 复用 2.4 |
| 2.6 | Day 2 自测 | 1h | - | 两账号登录确认隔离 |

### Day 3：P1 API + 增强功能（7h）

| # | 任务 | 时间 | 改动文件 | 验证方法 |
|---|------|------|----------|----------|
| 3.1 | `/daily-signal/interpret` 接入 | 1h | `app.js` | Pro 模式信号解读 |
| 3.2 | `/timing` + `/smart-dca` 接入 | 1h | `app.js` | Pro 模式展示建议 |
| 3.3 | 激活 portfolio_optimizer.py | 1h | `main.py` | 资产配置 Simple/Pro |
| 3.4 | 盯盘 API + 前端轮询 | 1h | `main.py` + `app.js` | 空仓返回 idle |
| 3.5 | 推送内容空仓适配 | 1h | `wxwork_push.py` | 空仓推送"机会导向" |
| 3.6 | 聊天意图分类 + 记忆修复验证 | 0.5h | `main.py` | R1 问题走 R1 引擎 |
| 3.7 | 全量联调 | 1.5h | - | 健康检查脚本 |

---

## 八、API 健康检查脚本

```python
#!/usr/bin/env python3
"""Phase 0 API 验证 - 每天结束运行"""
import requests

BASE = "http://150.158.47.189:8000"

def check(name, method, url, **kw):
    try:
        r = getattr(requests, method)(f"{BASE}{url}", timeout=10, **kw)
        ok = r.status_code == 200
        print(f"  {'✅' if ok else '❌'} {name} [{r.status_code}] {r.elapsed.total_seconds():.1f}s")
        return ok
    except Exception as e:
        print(f"  ❌ {name} ERROR: {e}")
        return False

print("=== Day 1 ===")
check("健康检查", "get", "/api/health")
check("偏好-读", "get", "/api/user/preference?userId=LeiJiang")
check("偏好-写", "put", "/api/user/preference?userId=LeiJiang",
      json={"display_mode": "pro"})

print("\n=== Day 2 ===")
check("家庭汇总", "get", "/api/household/summary")
check("新闻分析", "post", "/api/news/deep-impact",
      json={"newsId": "test", "userId": "LeiJiang"})
check("持仓分析", "post", "/api/stock-holdings/analyze",
      json={"userId": "LeiJiang"})

print("\n=== Day 3 ===")
check("资产配置-Simple", "get", "/api/asset-allocation?userId=LeiJiang&mode=simple")
check("资产配置-Pro", "get", "/api/asset-allocation?userId=LeiJiang&mode=pro")
check("盯盘预警", "get", "/api/watchlist/alerts?userId=LeiJiang")
check("智能定投", "get", "/api/smart-dca?userId=LeiJiang")
check("入场时机", "get", "/api/timing?userId=LeiJiang")

print("\n=== 多账号 ===")
check("部落格里-偏好", "get", "/api/user/preference?userId=BuLuoGeLi")
check("部落格里-配置", "get", "/api/asset-allocation?userId=BuLuoGeLi&mode=simple")
check("AI聊天", "post", "/api/chat",
      json={"message": "沪深300能买吗", "userId": "LeiJiang"})
```

---

## 九、测试用例

### 9.1 模式切换

| ID | 操作 | 预期 |
|----|------|------|
| TC-1.1 | 厉害了哥切到 Simple | 界面简化 + 后端 JSON 更新 |
| TC-1.2 | 部落格里切到 Pro | 界面展开 + 后端 JSON 更新 |
| TC-1.3 | 刷新页面 | 模式保持（后端恢复） |
| TC-1.4 | 断网切换 | localStorage 回滚 + toast |
| TC-1.5 | Pro 额外数据 | 只在 Pro 下请求 |

### 9.2 多账号隔离

| ID | 操作 | 预期 |
|----|------|------|
| TC-2.1 | 厉害了哥看持仓 | 只看到自己的 |
| TC-2.2 | 首页家庭资产 | 两人合计 + 各自明细 |
| TC-2.3 | 各自聊天 | 记忆互不干扰 |
| TC-2.4 | 推送偏好 | 部落格里不收复盘 |

### 9.3 空仓场景

| ID | 场景 | 预期 |
|----|------|------|
| TC-3.1 | 一人有仓一人空仓 | 有仓的盯盘，空仓的 idle |
| TC-3.2 | 都空仓 | 盯盘 idle，后台分析继续 |
| TC-3.3 | 空仓早安简报 | "机会导向"内容 |
| TC-3.4 | 首页空仓 | 不报错，"暂无持仓" |

### 9.4 可靠性

| ID | 场景 | 预期 |
|----|------|------|
| TC-4.1 | 后端挂了 | 错误卡片，不白屏 |
| TC-4.2 | API 超时 | 超时提示 + 重试 |
| TC-4.3 | R1 分析中 | 思考动画 |

---

## 十、验收标准

### 基础功能（Day 1-2）
- [ ] Simple/Pro 模式可切换，刷新保持
- [ ] Simple 模式：老婆 5 秒内获取关键信息
- [ ] 7 个 P0+P1 API 前端可访问
- [ ] 3 个记忆 BUG 已修复
- [ ] 死代码已清理
- [ ] 前端三态覆盖（Loading/Error/Empty）

### 增强功能（Day 3）
- [ ] 资产配置建议：健康度卡片 + portfolio_optimizer 激活
- [ ] 实时盯盘：空仓休眠 + 交易时间外不跑 + 30分钟冷却
- [ ] AI 聊天：意图分类 + 记忆生效 + 模式适配
- [ ] 家庭资产汇总：首页两人合计
- [ ] 推送内容：空仓切换为"机会导向"

### 多账号隔离
- [ ] 持仓/记忆/分析按 userId 隔离
- [ ] 推送偏好各人独立
- [ ] 盯盘阈值各人独立

### 质量保证
- [ ] 健康检查脚本全部通过
- [ ] 无控制台报错

---

## 十一、部署流程

```bash
# 1. SSH 登录
ssh ubuntu@150.158.47.189

# 2. 拉代码
cd /opt/moneybag && git pull origin main

# 3. 重启后端
sudo systemctl restart moneybag

# 4. 验证
curl http://150.158.47.189:8000/api/health

# 5. 前端自动部署（push main → GitHub Actions → GitHub Pages）
# 无需手动操作

# 回滚
cd /opt/moneybag && git revert HEAD
sudo systemctl restart moneybag
```

---

**下一步**：确认后开始 Day 1 实施。

---

## 附录 A：功能定位总结

| 功能 | 定位 | 增强点 | 影响范围 |
|------|------|--------|----------|
| **资产配置建议** | 首页增强 | 健康度 → 资产分布 + 调仓建议 | renderLanding() + 新 API |
| **实时盯盘** | 已有功能 | **空仓智能休眠** + 个性化阈值 | 新增 API + 前端轮询 |
| **AI 聊天** | 已有功能增强 | 意图路由 + 记忆修复 + 模式适配 | main.py chat 路由改造 |
| **资产功能** | 已有功能增强 | 家庭合并 + 交易流水分页 + 历史曲线 | 新增 API + app.js |

## 附录 B：Pro 模式完整信息架构

```
首页 (renderLanding)
├── 家庭净资产卡片（两人合计 + 各自明细）
├── 今日信号（Simple 简化 / Pro 详细）
├── 持仓健康度（Simple 评分 / Pro 饼图+偏离度）
├── AI 准确率
├── [Pro] 早安简报卡片
└── 快捷问管家

持仓页 (renderStocks)
├── 股票持仓列表
│   └── [Pro] 7-Skill 深度分析入口
├── 基金持仓列表
│   └── [Pro] 7-Skill 深度分析入口
├── 交易流水（分页）
├── 资产历史曲线（Chart.js）
└── [Pro] 组合优化建议

资讯页 (renderInsight)
├── 新闻列表（16 个子 Tab 已有）
├── [Pro] Deep-Impact 深度分析
└── [Pro] 风控评估

AI分析页 (renderChat)
├── 聊天界面（意图路由 + 模式适配）
├── [Pro] 信号解读
├── [Pro] 入场时机
├── [Pro] 智能定投
└── [Pro] Agent 记忆查看

资产页 (renderAssets)
├── 全资产管理（已有）
├── 记账（已有）
└── 收入源（已有）
```

## 附录 C：V6 后续衔接

Phase 0 完成后，V6 新功能自动有位置放：

| V6 新功能 | 放在哪 | 对应前端 |
|-----------|--------|----------|
| 地缘政治分析 | 资讯页 [Pro] | renderInsight() 新增子 Tab |
| 原油联动分析 | 资讯页 [Pro] | 同上 |
| 北向资金修复 | 首页信号卡片 | renderSignalCard() 增加数据 |
| 行业轮动 | 持仓页推荐 | renderStocks() 新增推荐区 |
| 分析历史 | AI 分析页 | renderChat() 新增历史时间线 |

## 附录 D：设计参考来源

| 来源 | 吸收内容 |
|------|----------|
| 幻方量化 | 信号侦察、风险预警思路 |
| Monarch Money | 净资产仪表盘、交易流水制 |
| Ghostfolio | Activity/Transaction 数据模型（加权平均成本法） |
| 6 位朋友方案 | 风控参数、门控、成本控制等 |

## 附录 E：R1 深度分析取消机制

```
用户点击「取消」时的行为：

前端：
├── 停止等待响应（AbortController.abort()）
├── 隐藏思考动画
└── 显示"已取消"

后端：
├── R1 调用继续完成（不中断）
├── 结果写入缓存（下次可复用）
└── 不浪费已消耗的 token

原因：R1 一次调用 ¥0.06，中断了也收费，不如跑完缓存
```
