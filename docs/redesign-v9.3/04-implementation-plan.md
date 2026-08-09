# 04 · 实施计划（8 个 PR）

> 严格按顺序执行，不跳跃。每个 PR 独立可发布、独立可回滚。

---

## PR-1 · 基建：注入 Design System

**目标**：让新 CSS 生效，但**不改任何现有页面**。

**改动**：
- 复制 `01-design-tokens.css` → `~/moneybag-for-claudecode/styles/design-tokens.css`
- 复制 `02-component-library.css` → `~/moneybag-for-claudecode/styles/components.css`
- 编辑 `index.html`：在现有 `<link rel="stylesheet" href="styles.css?v=...">` **之前**新增：
  ```html
  <link rel="stylesheet" href="styles/design-tokens.css?v=9.3.0">
  <link rel="stylesheet" href="styles/components.css?v=9.3.0">
  ```
- 引入 Google Fonts（如果还没引入）：
  ```html
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=DM+Serif+Display&display=swap" rel="stylesheet">
  ```

**验收**：浏览器 DevTools 能看到 `:root` 下注入的所有 `--color-brand-500` 等变量；现有页面**视觉无变化**。

**预计工作量**：30 分钟

---

## PR-2 · 共享组件 + 主题切换稳定化

**目标**：建立 `pages/_components.js`，承载所有可复用的渲染函数。

**改动**：
- 新建 `pages/_components.js`，实现 03-page-specs.md 末尾列出的 9 个 `renderXxx` 函数
- `app.js` 顶部 `import` 或全局挂载这些函数（保持 Vanilla JS 风格，挂在 `window.MB.components`）
- 主题切换：旧的逻辑保留，但新版本用 `document.documentElement.setAttribute('data-theme', 'light' | 'dark')`，CSS 变量自动响应
- LocalStorage 记忆主题选择：`localStorage.setItem('mb-theme', theme)`

**验收**：
- 在 console 调用 `MB.components.renderHeroNetWorth({netWorth: 100, ...})` 返回正确 HTML
- 切换主题刷新页面后保持

**预计工作量**：2 小时

---

## PR-3 · 底 Tab 7 → 5

**目标**：导航简化。

**改动**：
- 修改 `app.js` 中渲染底部导航的逻辑（搜索关键字：`tabbar` / `nav` / `bottom`）
- 5 Tab 顺序：`首页 / 持仓 / 资讯 / AI / 资产`
- 历史 + 小课从 nav 移除（页面文件保留）
- 资产页底部新增"我的记录"模块入口（链接到 `#history`）
- 个人设置页（如无则新建 `pages/settings.js`）加入"📚 财商小课"入口（链接到 `#quiz`）

**验收**：
- 底部只显示 5 个图标
- 点击 `#history` 仍可访问历史页（顶部带返回按钮）
- 点击 `#quiz` 仍可访问小课页

**预计工作量**：1.5 小时

---

## PR-4 · 首页 (landing.js)

**目标**：首页视觉完全切到新设计。

**改动**：
- 重写 `pages/landing.js` 的 HTML 模板字符串
- 严格按 `03-page-specs.md` 页面 1 的信息架构
- 数据绑定保留现有 fetch / state 逻辑，只改 `innerHTML` 部分

**关键点**：
- Hero 净资产用 `.mb-money mb-money--xl`
- 双 Avatar：L = `.mb-avatar--leijiang` / B = `.mb-avatar--buluogeli`
- AI 提醒卡用 `.mb-card--ai-tip`
- 顶部欢迎语根据当前时间动态生成（早上好 / 下午好 / 晚上好）

**验收**：
- 与 `moneybag-mobile-redesign.html` 第 01 章节 NEW 列视觉一致
- 移动端（iPhone DevTools 模式）无横向溢出
- 浅色主题切换正常

**预计工作量**：3 小时

---

## PR-5 · 持仓 (portfolio.js) + 资讯 (insight.js)

**目标**：核心数据页升级。

**改动**：
- `portfolio.js` 重写，按 03-page-specs 页面 2
- `insight.js` 重写，包含恐慌贪婪 SVG 仪表盘（计算公式见 page-specs）

**重点**：
- 半圆仪表盘的指针位置必须正确（小心三角函数角度）
- 估值水平的渐变色条标记线位置 = `${pctRank}%`

**验收**：
- 仪表盘指针角度与数值匹配（value=50 → 中位 / value=100 → 最右）
- 切换股票/基金 Tab 视觉稳定不闪烁

**预计工作量**：4 小时

---

## PR-6 · AI 分析 (chat.js)

**目标**：AI 模块视觉差异化。

**改动**：
- `chat.js` 重写
- 新增 4 大师切换组件（默认选中"巴菲特"）
- 后端 chat API 增加 `master` 参数（如不便改后端，前端拼接到 user message 开头）

**验收**：
- 切换大师后，下次对话明显能感受到风格变化
- 输入框固定底部 80px 处不被键盘遮挡（mobile）

**预计工作量**：3 小时

---

## PR-7 · 资产 (assets.js) + 我的记录

**目标**：

- `assets.js` 加入 6 类资产网格
- 底部"我的记录"承接砍掉的「历史」入口
- 顶部 Hero（统一净资产）样式与首页 Hero 一致但配色略有区分

**验收**：
- 6 个分类卡能正确路由到「添加资产」流程
- "我的记录" 拉取最近 3 条 AI 分析记录

**预计工作量**：2.5 小时

---

## PR-8 · 收尾打磨

**目标**：把 PR-2 ~ PR-7 留下的小坑补完。

**改动**：
- `quiz.js` 顶部加返回箭头
- `history.js` 顶部加返回箭头 + 标题
- 检查所有页面的滚动到底是否被底栏遮挡（加 `padding-bottom: calc(var(--tabbar-height) + 16px)`）
- 检查浅色主题下所有页面文字对比度
- 升级版本号 `v9.2.4 → v9.3.0`（index.html / sw.js / pyproject.toml / package.json 如有）

**验收**：
- 跑一遍 manual smoke test（见 05-acceptance-tests.md）
- 跑 `pytest backend/tests/` 全绿
- 浏览器 Lighthouse 移动端分数 ≥ 90

**预计工作量**：2 小时

---

## 总工作量估计

| 阶段 | 时间 |
|---|---|
| PR-1 ~ PR-3 基建 | ~ 4 小时 |
| PR-4 ~ PR-7 页面 | ~ 12 小时 |
| PR-8 收尾 | ~ 2 小时 |
| **总计** | **~ 18 小时** |

按每天 4 小时投入，约 4-5 天完成全部 8 个 PR。

---

## 给 Claude Code 的执行原则

1. **一次只做一个 PR**，做完后等用户人工 review + 测试通过再开下一个
2. **不要预测后面 PR 的需求改前面 PR 的代码**（容易越界）
3. **遇到设计模糊的地方，先开浏览器看 `moneybag-mobile-redesign.html`**，再做不下来才问用户
4. **每次修改后必须本地起 `python -m http.server 8000` 看实际效果**（不要相信代码看上去 OK）
5. **commit message 格式**：
   ```
   feat(redesign): PR-X · <一句话描述>

   - 改动文件 1: <说明>
   - 改动文件 2: <说明>

   验收: <对应 05-acceptance-tests.md 的检查项编号>
   截图: docs/redesign-v9.3/screenshots/PR-X.png
   ```
