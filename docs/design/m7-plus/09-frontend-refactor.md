# 09 — 前端拆分改造（M6+ 补丁，M7 开工前完成）

> **定位**：独立于 M7+ 后端设计的前端债务清理。和后端 M1 重构思路一致——拆文件、不换框架。
>
> **时机**：M1-M6 完成后、M7 开工前执行。M7+ 的前端新增页面依赖此拆分完成后的 `pages/` 目录结构。

---

## 一、现状分析

### `app.js` 5148 行结构拆解

| 类别 | 行数 | 占比 | 内容 |
|---|---|---|---|
| 核心层 | 474 | 9% | 全局状态、路由 `navigateTo()`、API 客户端、工具函数、主题切换、Profile 系统 |
| 页面模块 | 2546 | 49% | 12 个 `render*()` 函数群（落地页、持仓、资讯、聊天、记账、资产、盯盘、问卷、高级分析等） |
| V6 patches | 2094 | 41% | `frontend-patches/v6/` 的 00-08 共 9 个 patch 文件，通过 `build.js` 合并到 app.js 末尾 |

### 核心问题

- **和后端 M1 重构前的 `main.py` 4044 行是同一个问题**——所有功能挤在一个文件里
- M7+ 还要新增 ~7 个前端页面模块（CSV 导入、雷达图、候选池、行为报告、事件卡片、风控指示器、迷你行情），不拆的话会膨胀到 8000+ 行
- 12 个页面的 `render*()` 函数之间**无直接依赖**——它们只依赖核心层的全局变量和工具函数，天然适合拆分

### 当前加载方式

```html
<!-- index.html -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="app.js?v=7.7.5"></script>
<!-- Service Worker 注册 -->
```

单文件加载，无构建步骤，无模块系统。

---

## 二、目标结构

```
app.js                          ~500 行（核心层，只留全局状态/路由/API/工具/主题）
pages/
├── landing.js                  ~340 行（落地页 + 首页子模块：管家简报/净资产Hero/风控预警等）
├── portfolio.js                ~260 行（持仓盈亏页 + 交易弹窗）
├── insight.js                  ~640 行（资讯页 + 信号侦察兵/成绩单/体检/管家/周报 子Tab）
├── chat.js                     ~180 行（AI 聊天页 + 白话解释弹窗 + 术语库）
├── ledger.js                   ~230 行（记账页 + 收入源管理）
├── assets.js                   ~196 行（资产管理页 + 添加/编辑资产弹窗）
├── stocks.js                   ~162 行（持仓盯盘页：股票+基金统一）
├── quiz.js                     ~139 行（问卷 + 金额输入 + 结果页）
├── analysis.js                 ~391 行（高级分析 Tabs：因子IC/蒙特卡洛/AI预测/遗传/优化/另类/RL/LLM）
├── history.js                  ~830 行（历史记录 + 行业热度/券商视图/情景分析/推荐/决策）
└── alloc.js                    ~200 行（配置资产弹窗 + 配比历史 + 回测可视化）
```

**拆分后 app.js 保留内容**：

```
行 1-56      文件头 + 自毁清理 IIFE
行 57-71     API 配置
行 72-81     FUND_DETAILS 常量
行 82-105    问卷配置常量
行 106-171   全局状态 + 主题 + renderCard
行 172-247   多用户 Profile 系统
行 248-307   V4 交易流水 + 资产管理数据层
行 308-348   收入源管理（数据层部分）
行 349-367   工具函数 + API 客户端
行 368-441   底部导航 + 设置 + navigateTo
行 1892-1939 弹窗/图表/事件（通用 UI）
行 2995-3054 B3修复 + 启动入口
```

---

## 三、加载方式

```html
<!-- index.html 改造后 -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>

<!-- 核心层（必须先加载） -->
<script src="app.js?v=8.0.0"></script>

<!-- 页面模块（顺序无关，都依赖 app.js 的全局变量） -->
<script src="pages/landing.js?v=8.0.0"></script>
<script src="pages/portfolio.js?v=8.0.0"></script>
<script src="pages/insight.js?v=8.0.0"></script>
<script src="pages/chat.js?v=8.0.0"></script>
<script src="pages/ledger.js?v=8.0.0"></script>
<script src="pages/assets.js?v=8.0.0"></script>
<script src="pages/stocks.js?v=8.0.0"></script>
<script src="pages/quiz.js?v=8.0.0"></script>
<script src="pages/analysis.js?v=8.0.0"></script>
<script src="pages/history.js?v=8.0.0"></script>
<script src="pages/alloc.js?v=8.0.0"></script>
```

### 设计决策

| 决策 | 选项 | 选择 | 理由 |
|---|---|---|---|
| 模块系统 | ES Module `import/export` vs 全局函数 | **全局函数** | 不改调用方式，`navigateTo()` 照常调 `renderLanding()`，零重构风险 |
| 构建工具 | Webpack/Vite vs 无 | **无** | 个人项目，`<script>` 标签够用，不增加维护负担 |
| V6 patches | 保留合并机制 vs 拆入页面 | **拆入页面** | patch 内容按功能归属拆入对应 `pages/*.js`，`build.js` 废弃，消除合并步骤 |
| 加载优化 | 懒加载 vs 全量加载 | **全量加载** | 总量 ~5000 行 JS，gzip 后 <50KB，不值得做懒加载 |

---

## 四、V6 Patches 归属映射

V6 的 9 个 patch 文件按功能拆入对应页面：

| Patch | 内容 | 归入 |
|---|---|---|
| `00-common.js` | 公共工具函数 | `app.js`（核心层） |
| `01-empty-landing.js` | 落地页 UI | `pages/landing.js` |
| `02-insight-protabs.js` | 资讯 Tabs | `pages/insight.js` |
| `03-holdings-ai.js` | AI 持仓分析 | `pages/portfolio.js` |
| `04-signal-interpret.js` | 信号解读 | `pages/insight.js` |
| `05-timing-dca.js` | 定投时机 | `pages/insight.js` |
| `06-household-hero.js` | 家庭组合模块 | `pages/landing.js` |
| `07-token-budget.js` | AI Token 预算 | `pages/chat.js` |
| `08-watchlist-poll.js` | 自选股轮询 | `pages/stocks.js` |

拆分完成后 `frontend-patches/` 目录可归档（不删除，保留 git 历史）。

---

## 五、全局状态共享方案

**不做任何改变**。`app.js` 先于所有 `pages/*.js` 加载，其中声明的变量和函数自动成为全局作用域，所有页面模块直接访问：

```javascript
// app.js 中声明（全局）
let currentPage = 'landing';
let liveNavData = {};
function $(s) { return document.querySelector(s); }
function fmtMoney(n) { ... }
function navigateTo(p) { ... }

// pages/landing.js 中直接使用（无需 import）
function renderLanding() {
    currentPage = 'landing';        // 直接访问全局变量
    const el = $('#app');            // 直接调用全局工具函数
    el.innerHTML = `...${fmtMoney(amount)}...`;
}
```

---

## 六、Service Worker 缓存更新

`sw.js` 的缓存策略需要感知新增的 `pages/*.js` 文件。

**修改点**：sw.js 的预缓存文件列表从只有 `app.js` 扩展为包含所有 `pages/*.js`。

```javascript
// sw.js 缓存列表更新
const CACHE_FILES = [
    '/',
    '/index.html',
    '/styles.css',
    '/app.js',
    '/pages/landing.js',
    '/pages/portfolio.js',
    // ... 所有页面文件
];
```

---

## 七、M7+ 前端新增页面的落位

拆分完成后，M7+ 的前端新增直接放 `pages/` 目录：

| M7+ Batch | 前端文件 | 内容 |
|---|---|---|
| Batch 1 | `pages/broker-import.js` | CSV 上传 + 预览表格 + 校验结果 + 确认流程 |
| Batch 2 | `pages/glide-path.js` | 多层雷达图 + 偏离度标黄/红 |
| Batch 3 | `pages/fund-filter.js` | 候选池列表 ✓/⚠️/已排除 + "为什么排除"弹窗 |
| Batch 5 | — | 复盘报告新增章节，合入 `pages/history.js` |
| Batch 6 | — | 风控指示器 🟢🟡🔴，合入 `pages/portfolio.js` |
| Batch 7 | — | 首页"市场动态"卡片，合入 `pages/landing.js` |
| Batch 8 | `pages/mini-chart.js` | TradingView Lightweight Charts 迷你行情页 |

### 前后端同步规则

> **每个 Batch 的交付物 = 后端文件 + 对应前端文件。后端 API 完成但前端未跟进的 Batch，不算完成。**

各 Batch 文档中的"产出文件"清单只列了后端文件。前端文件归属见上表。Claude 实现每个 Batch 时，需同时交付：

1. 后端文件（按 Batch 文档的文件落位表）
2. 对应的前端文件（按上表，有新建的建新文件，合入已有的改已有文件）
3. `index.html` 中新增 `<script>` 标签（如有新建前端文件）
4. `sw.js` 缓存列表更新（如有新建前端文件）

**Batch prompt 模板补充**（追加到原 §十 prompt 模板末尾）：

```
前端同步：
- 查阅 09-frontend-refactor.md §七 的前端落位表
- 后端 API 完成后，实现对应的前端页面文件
- 新建前端文件需同步更新 index.html 和 sw.js
```

---

## 八、执行步骤

**纯机械操作，不改任何业务逻辑，约 1 天。**

| 步骤 | 操作 | 验证 |
|---|---|---|
| 1 | 创建 `pages/` 目录 | — |
| 2 | 从 `app.js` 中剪切 12 个页面函数群到对应 `pages/*.js` | 每拆一个文件，运行 `node --check pages/xxx.js` |
| 3 | 将 V6 patches 内容按归属表拆入对应 `pages/*.js` | — |
| 4 | `app.js` 只留核心层 ~500 行 | `node --check app.js` |
| 5 | 修改 `index.html`，添加 `<script>` 标签 | 浏览器打开，检查所有页面 |
| 6 | 修改 `sw.js` 缓存列表 | PWA 离线测试 |
| 7 | 冒烟测试：依次点击 8 个导航页面 | 无 JS 报错、页面正常渲染 |
| 8 | 归档 `frontend-patches/`，提交 git | `git diff --stat` 确认行数 |

---

## 九、验收标准

| 检查项 | 标准 |
|---|---|
| `app.js` 行数 | ≤600 行 |
| 页面文件数 | 11 个 `pages/*.js` |
| 功能回归 | 8 个导航页面全部正常渲染，无 JS 控制台报错 |
| PWA | 离线模式下页面可访问（Service Worker 缓存更新） |
| 数据完整性 | localStorage 数据不丢失，云端同步不受影响 |
| 构建步骤 | 无（不依赖 `build.js`、Webpack、Vite） |

---

## 十、禁止事项

1. **不换技术栈**——不引入 React/Vue/Svelte/TypeScript
2. **不引入构建工具**——不引入 Webpack/Vite/Rollup
3. **不引入 ES Module**——保持全局函数模式，不用 `import/export`
4. **不改业务逻辑**——纯文件拆分，剪切粘贴，不重写任何函数
5. **不改 API 接口**——后端不受影响
6. **不改 CSS**——`styles.css` 不动
