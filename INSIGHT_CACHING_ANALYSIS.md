# MoneyBag 「资讯」(Insight) 页面缓存问题分析报告

## 问题概述
用户反映「资讯」页面在切换标签时，每次都显示加载动画并从 API 重新获取数据，即使数据并未发生变化。这造成用户体验差（加载缓慢）且浪费网络资源。

---

## 1. 前端渲染结构

### 主函数：`renderInsight()` (pages/insight.js, 行 8-42)

**调用流程：**
```
renderInsight()
  ↓ 检查 insightTab 全局变量
  ├─→ 独立 tab（秒开）
  │   ├─ sector     → renderSectorHot()
  │   ├─ broker     → renderBrokerView()
  │   ├─ scenario   → renderScenarioView()
  │   ├─ recommend  → renderRecommendTab()
  │   └─ ... (共 10 个独立 tab)
  │
  └─→ dashboard 依赖 tab（需调 fetchDashboard()）
      ├─ overview  → renderInsightOverview()
      ├─ tech      → renderInsightTech()
      ├─ macro     → renderInsightMacro()
      └─ global    → renderInsightGlobal()
```

**关键 tab 对应的 API 端点：**

| Tab 名称 | 函数 | API 端点 | 超时(ms) | 备注 |
|---------|------|--------|--------|------|
| news | renderInsightNews | `{API_BASE}/news` | 15000 | ❌ **每次调用** |
| policy | renderInsightPolicy | `/policy/all-topics` + `/policy/impact` | 45000 | ❌ **每次调用** |
| macro | renderInsightMacro | `/macro` (如需) | 15000 | ❌ **每次调用** |
| global | renderInsightGlobal | `/global/snapshot` + `/global/impact` | 无 | ❌ **每次调用** |
| tech/overview/macro | fetchDashboard | `/dashboard` | 30000 | ❌ **每次调用** |
| fundpick | renderFundPickResult | `/fund-screen?fund_type=...` | 30000 | ❌ **每次调用** |
| stockpick | renderStockPick | `/stock-screen?top_n=50` | 60000 | ❌ **每次调用** |
| signals | renderSignalScout | - | - | (需查 signal 页面) |
| scorecard | renderScorecard | - | - | (需查其他文件) |

---

## 2. API 端点及其数据源

### 主要 API 调用（app.js）

```javascript
// 行 361
async function fetchDashboard() {
  if (!API_AVAILABLE) return null;
  try {
    const r = await fetch(API_BASE + '/dashboard', {
      signal: AbortSignal.timeout(30000)  // ⚠️ 30秒超时
    });
    if (r.ok) return await r.json();
  } catch(e) { ... }
  return null;
}
```

**关键观察：**
- ✅ 有 30 秒超时设置（不会无限等待）
- ❌ **没有任何客户端缓存逻辑**（localStorage/sessionStorage/变量缓存都没有）
- ❌ 每次调用 `renderInsight()` 都会重新 fetch

---

## 3. 后端缓存机制

### 3.1 预计算缓存 (backend/services/precomputed_cache.py)

**缓存 TTL 配置（行 20-31）：**

```python
_PRECOMPUTED_TTL = {
    "factors": 7200,            # ⏱️  2小时（盘中cache_warmer每30分刷新）
    "fear_greed": 7200,         # ⏱️  2小时
    "valuation": 7200,          # ⏱️  2小时
    "recommendations": 14400,   # 4小时
    "decisions": 14400,         # 4小时
    "macro": 14400,             # 4小时
    "broker_consensus": 14400,  # 4小时
    "scenarios": 28800,         # 8小时
    # ... 其他
}
```

**工作机制：**
1. 凌晨 night_worker 预计算数据并保存到 `DATA_DIR/precomputed/{key}_{date}.json`
2. 白天 API 调用时优先读缓存（秒出）
3. **缓存过期后才实时计算**（费时）
4. 非交易日（周末）TTL 延长到 72 小时

### 3.2 Dashboard API 三级降级机制 (backend/api/dashboard.py, 行 117-215)

```
请求 /api/dashboard
  ↓
【第1级】新鲜缓存（凌晨预算结果）
  ├─ get_precomputed("factors")     ✅ 秒出
  ├─ get_precomputed("fear_greed")  ✅ 秒出
  └─ get_precomputed("valuation")   ✅ 秒出
  ↓ [失败 or 缓存过期]
【第2级】过期缓存（最多找4天前）
  ├─ 扫描 precomputed/ 目录
  └─ 用 3-4 天前的数据（不检查 TTL）
  ↓ [失败]
【第3级】实时拉取（5秒超时，NOT 30秒！）
  ├─ get_valuation_percentile()
  ├─ get_fear_greed_index()
  ├─ get_technical_indicators()
  ├─ get_market_news()
  └─ ... 其他 11 种数据源
  ↓ [超时]
【第4级】返回过期缓存
【第5级】空壳响应（return {}）
```

**时间成本估算：**
- 新鲜缓存：**< 500ms**（秒出）
- 过期缓存重算：**3-15s**（取决于数据源稳定性）
- 实时拉取超时：**5s**（总超时 5s）

---

## 4. 现有缓存情况

### ✅ 后端缓存
- 预计算缓存：2-4小时 TTL
- 盘中 cache_warmer 每 30 分刷新（保证 factors 和 fear_greed 新鲜）

### ❌ 客户端缓存
**完全没有！** 没有找到任何：
- localStorage 缓存
- sessionStorage 缓存
- 内存变量缓存（如 `let _dashboardCache = null`）
- IndexedDB 缓存

**app.js 里仅有的缓存相关代码：**
```javascript
// 行 40-42：清理旧账号时清空的缓存 keys
['moneybag_current_profile', 'moneybag_profiles',
 'moneybag_market_cache', 'moneybag_ai_cache'].forEach(k => {
  localStorage.removeItem(k);
});
```
注：`moneybag_market_cache` 这个 key 定义了但从未被使用！

---

## 5. 所有 fetch() 调用位置

| 文件 | 行号 | 函数 | 端点 | 缓存 |
|-----|------|------|-----|------|
| pages/insight.js | 34 | renderInsightNews | `/news` | ❌ |
| pages/insight.js | 120 | (异步) | `/news/impact` | ❌ |
| pages/insight.js | 135-136 | renderInsightPolicy | `/policy/all-topics`, `/policy/impact` | ❌ |
| pages/insight.js | 194 | renderInsightMacro | `/macro` | ❌ |
| pages/insight.js | 210-211 | renderInsightGlobal | `/global/snapshot`, `/global/impact` | ❌ |
| pages/insight.js | 258 | renderFundPickResult | `/fund-screen` | ❌ |
| pages/insight.js | 299 | renderStockPick | `/stock-screen` | ❌ |
| app.js | 361 | fetchDashboard | `/dashboard` | ❌ |
| app.js | 362 | fetchFundNews | `/news/{code}` | ❌ |
| app.js | 363 | fetchPortfolioNews | `/news/portfolio` | ❌ |
| app.js | 365 | fetchPolicyNews | `/news/policy` | ❌ |

---

## 6. 问题诊断

### 根本原因
1. **前端完全无缓存** → 每次切换 tab 都重新 fetch
2. **后端缓存 2-4 小时** → 盘中如果缓存过期，需要实时计算（3-15s）
3. **用户体验差** → 加载动画 + 等待时间 + 网络消耗

### 加载流程耗时分析

**用户每次切换到「总览」tab 时：**
```
时间轴：
0ms   - 点击「总览」tab
50ms  - renderInsight() 调用
50-60ms - UI 显示加载动画
60ms  - fetch(API_BASE + '/dashboard') 发送
60-80ms - 网络往返时间（RTT）
80-3500ms - 后端处理（取决于缓存新鲜度）
          - 新鲜：< 200ms（读磁盘）
          - 过期：3-15s（重算）
3500-3600ms - 网络往返时间（RTT）
3600ms - 前端接收 JSON，渲染 DOM
4000ms - 用户看到数据（~4秒等待！）
```

---

## 7. 数据实时性需求分析

| Tab | 数据更新频率 | 推荐缓存时间 | 备注 |
|-----|-----------|----------|------|
| 📊 总览 | 每 30 分 | 30 分 | 盘中 cache_warmer 刷新 |
| 📈 技术 | 每 1 分 | 10-15 分 | 技术指标变化快但趋势相对稳定 |
| 📊 宏观 | 每天 1-2 次 | 2-4 小时 | 宏观数据一天内不变 |
| 🌐 全球 | 每 5-10 分 | 5-10 分 | 美股盘中实时更新 |
| 📰 新闻 | 实时 | 10-15 分 | 新闻可能有延迟，但不必完全实时 |
| 🏛️ 政策 | 每天 1-2 次 | 3-6 小时 | 政策变化不频繁 |
| 🔍 选基 | 每天 1 次 | 1-2 小时 | 基金评分每天更新 |
| 🧠 选股 | 每天 1-2 次 | 2-4 小时 | 股票打分每天更新 |

---

## 8. 建议方案

### 方案 A：轻量级客户端缓存（推荐）✅
**实现难度：低 | 效果：立竿见影 | 成本：极低**

```javascript
// 在 app.js 中添加
const INSIGHT_CACHE = {
  data: {},
  timestamps: {},
  TTL: {
    'overview': 30 * 60 * 1000,    // 30 分钟
    'tech': 15 * 60 * 1000,        // 15 分钟
    'macro': 2 * 60 * 60 * 1000,   // 2 小时
    'global': 10 * 60 * 1000,      // 10 分钟
    'news': 15 * 60 * 1000,        // 15 分钟
    'policy': 3 * 60 * 60 * 1000,  // 3 小时
    'fundpick': 2 * 60 * 60 * 1000, // 2 小时
    'stockpick': 4 * 60 * 60 * 1000, // 4 小时
  },

  get(key) {
    if (!this.data[key]) return null;
    const now = Date.now();
    const age = now - this.timestamps[key];
    if (age > this.TTL[key]) {
      delete this.data[key];
      delete this.timestamps[key];
      return null;
    }
    return this.data[key];
  },

  set(key, value) {
    this.data[key] = value;
    this.timestamps[key] = Date.now();
  },

  clear(key) {
    delete this.data[key];
    delete this.timestamps[key];
  }
};

// 修改 fetchDashboard()
async function fetchDashboard() {
  if (!API_AVAILABLE) return null;
  
  // 先看缓存
  const cached = INSIGHT_CACHE.get('overview');
  if (cached) return cached;
  
  try {
    const r = await fetch(API_BASE + '/dashboard', {
      signal: AbortSignal.timeout(30000)
    });
    if (r.ok) {
      const data = await r.json();
      INSIGHT_CACHE.set('overview', data);  // 保存缓存
      return data;
    }
  } catch(e) { console.warn('Dashboard fetch failed:', e); }
  
  return null;
}
```

**效果：**
- 首次加载：4 秒（等待后端）
- 30 分钟内再次切换到该 tab：**< 100ms**（秒开！）
- 30 分钟后自动过期，再次刷新

---

### 方案 B：localStorage 持久化缓存（更稳）✅
**实现难度：中 | 效果：优秀 | 成本：低**

```javascript
function fetchDashboardWithPersist() {
  const cacheKey = 'insight_dashboard_cache';
  const timestampKey = 'insight_dashboard_ts';
  const ttl = 30 * 60 * 1000; // 30 分钟
  
  try {
    const cached = localStorage.getItem(cacheKey);
    const cachedTs = localStorage.getItem(timestampKey);
    if (cached && cachedTs && Date.now() - parseInt(cachedTs) < ttl) {
      return Promise.resolve(JSON.parse(cached));
    }
  } catch(e) {}
  
  return fetch(API_BASE + '/dashboard', {
    signal: AbortSignal.timeout(30000)
  }).then(r => {
    if (r.ok) {
      return r.json().then(data => {
        try {
          localStorage.setItem(cacheKey, JSON.stringify(data));
          localStorage.setItem(timestampKey, Date.now().toString());
        } catch(e) {}
        return data;
      });
    }
    return null;
  });
}
```

**优势：**
- 页面刷新后还有缓存
- 用户切换标签签后回来，缓存仍有效

---

### 方案 C：Service Worker 缓存（企业级）⭐
**实现难度：高 | 效果：完美 | 成本：中**

项目已有 `sw.js`，可增强为：
```javascript
// sw.js 中
const INSIGHT_CACHE_URLS = [
  '/api/dashboard',
  '/api/news',
  '/api/macro',
  '/api/global/snapshot',
  '/api/fund-screen',
  '/api/stock-screen'
];

self.addEventListener('fetch', (event) => {
  if (INSIGHT_CACHE_URLS.some(url => event.request.url.includes(url))) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        // 返回缓存，同时后台刷新
        if (cached) {
          // 检查是否过期，决定是否需要后台刷新
          fetch(event.request).then(r => {
            caches.open('insight-v1').then(c => c.put(event.request, r.clone()));
          });
          return cached;
        }
        // 没缓存就等待网络
        return fetch(event.request).then(r => {
          if (r.ok) {
            caches.open('insight-v1').then(c => c.put(event.request, r.clone()));
          }
          return r;
        });
      })
    );
  }
});
```

---

## 9. 建议优先级

| 方案 | 优先级 | 理由 |
|-----|------|------|
| **方案 A（内存缓存）** | 🔴 P0 | 快速修复，立竿见影，0 代价 |
| 方案 B（localStorage）| 🟠 P1 | 增强持久性，配合 A 更优 |
| 方案 C（Service Worker）| 🟡 P2 | 有 sw.js 就顺便加上 |
| 后端优化（cache_warmer） | 🟡 P2 | 已有，继续维护 30 分刷新 |

---

## 10. 快速修复清单

### 立即可做：
- [ ] 在 `app.js` 中添加 `INSIGHT_CACHE` 对象（方案 A）
- [ ] 修改 `fetchDashboard()` 加入缓存逻辑
- [ ] 修改其他 fetch 函数（`fetchFundNews`, `fetchMacro` 等）
- [ ] 测试缓存过期后是否自动刷新

### 后续可做：
- [ ] 添加「手动刷新」按钮，让用户主动清缓存
- [ ] localStorage 持久化（方案 B）
- [ ] 增强 sw.js（方案 C）
- [ ] 添加缓存命中率监控（Google Analytics 或本地日志）

---

## 总结

**问题：** 「资讯」页每次切换 tab 都重新 fetch，导致加载慢

**根因：** 前端完全无缓存，后端缓存 2-4 小时，盘中缓存过期需重算

**方案：** 添加客户端内存缓存 30 分钟，秒速加载

**代码量：** ~50 行 JavaScript，改动最小化

**收益：** 用户体验 ⬆️⬆️⬆️，网络消耗 ⬇️⬇️⬇️
