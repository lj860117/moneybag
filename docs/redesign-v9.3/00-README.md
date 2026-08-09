# MoneyBag v9.3.0 视觉重设计 · Claude Code 实施包

> 给 Claude Code 看的实施手册。整个目录扔进 `~/WorkBuddy/moneybag-for-claudecode/docs/redesign-v9.3/`，然后让 Claude Code 按顺序读完执行即可。

---

## 这个文件夹里有什么

| 文件 | 作用 | 谁写谁读 |
|---|---|---|
| `00-README.md` | 总览 + 实施顺序（**先读这个**） | 设计师写 / Claude Code 读 |
| `01-design-tokens.css` | 颜色/字体/间距/阴影/动效 CSS 变量（**核心**） | 设计师写 / 直接放进项目 |
| `02-component-library.css` | 卡片/按钮/输入框等组件类（基于 tokens） | 设计师写 / 直接放进项目 |
| `03-page-specs.md` | 7 个页面的逐页改造说明（每个组件改哪里） | 设计师写 / Claude Code 读后改代码 |
| `04-implementation-plan.md` | 8 个 PR 的拆分计划（不要一次性改完） | 设计师写 / Claude Code 按 PR 推进 |
| `05-acceptance-tests.md` | 每个 PR 的验收标准（截图对比 + 自动化检查） | 设计师写 / Claude Code 跑测试 |
| `06-migration-guide.md` | 旧 CSS 类 → 新 CSS 类的映射表 | 设计师写 / Claude Code 替换用 |

视觉参考（不放在这个文件夹，但 Claude Code 要能看到）：
- `/Users/leijiang/WorkBuddy/2026-05-17-task-5/artifacts/moneybag-mobile-redesign.html` — 7 页完整视觉对比稿
- `/Users/leijiang/WorkBuddy/2026-05-17-task-5/tab-*.png` `mb-home-*.png` — 旧版真实截图

---

## 给 Claude Code 的最高指令（System Prompt）

```
你正在为 MoneyBag (lj860117/moneybag) 实施 v9.3.0 视觉重设计。

技术栈约束（不可违反）：
- 保留 Vanilla JS + 原生 CSS 架构，禁止引入 React / Vue / Tailwind / 任何打包工具
- 现有 16 个页面 .js 文件结构保留，只改 HTML 模板字符串
- 不破坏现有 PWA / SW / 后端 API 调用
- 测试必须通过（`pytest backend/tests/` + 手动 manual smoke test）

设计依据（必须读，不要凭印象）：
1. /docs/redesign-v9.3/01-design-tokens.css — 所有颜色/尺寸的唯一真理
2. /docs/redesign-v9.3/02-component-library.css — 复用这些组件类
3. /docs/redesign-v9.3/03-page-specs.md — 每页要改什么
4. /Users/leijiang/.../moneybag-mobile-redesign.html — 视觉对比稿（开浏览器看）

工作方式：
- 严格按 04-implementation-plan.md 的 8 个 PR 顺序执行
- 每个 PR 完成后运行 05-acceptance-tests.md 的检查项
- 每个 PR commit 前手动跑一次 `python -m http.server 8000` 在浏览器看实际效果
- 不要一次性改完所有页面，按 PR 拆分

报告格式：
- 每完成一个 PR，简短回报「PR-X 已完成，改动 N 个文件，截图 OK」
- 卡住时立即说，不要假设设计意图
```

---

## 实施顺序（速查）

```
PR-1: 注入 design-tokens.css + component-library.css（基建）
       └─ 不改任何页面，只新增 2 个 CSS 文件 + index.html link

PR-2: 全局 reset + 字体引入 + 主题切换稳定化
       └─ 改 styles.css 顶部 + index.html 的 <head>

PR-3: 底 Tab 7 → 5（合并历史/小课）
       └─ app.js 的路由 + 导航组件

PR-4: 首页 (landing.js) 重写模板
       └─ 家庭账户卡 + 双 Avatar + 提醒卡

PR-5: 持仓 (portfolio.js) + 资讯 (insight.js) 重写
       └─ 持仓 Hero + 恐慌贪婪仪表盘

PR-6: AI 分析 (chat.js) + 4 大师切换卡

PR-7: 资产 (assets.js) 6 类网格 + 我的记录（合并历史）

PR-8: 小课 (quiz.js) 移到设置页 + 收尾 polish
```

每个 PR 独立可发布、独立可回滚。

---

## 失败处理

如果 Claude Code 卡住：
1. 先读 `06-migration-guide.md` 看是否能找到旧类名 → 新类名映射
2. 还卡 → 直接对比 `moneybag-mobile-redesign.html` 看视觉效果
3. 还卡 → 在 commit message 里写「BLOCKED: <原因>」并告诉用户

---

设计师：Jax (Workbuddy)
设计稿日期：2026-05-17
项目：lj860117/moneybag → v9.3.0
