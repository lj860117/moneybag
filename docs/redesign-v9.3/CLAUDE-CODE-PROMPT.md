# 给 Claude Code 的开场提示词

复制下面整段，粘到 `moneybag-for-claudecode` 项目里 Claude Code 的对话框里，它会自动开干。

---

```
我要给 MoneyBag 升级到 v9.3.0 视觉重设计。设计师 Jax 已经把所有规范打包好了，
全套文档在 /Users/leijiang/WorkBuddy/2026-05-17-task-5/artifacts/handoff/ 里。

请你完成以下事情，按顺序执行：

【步骤 1】把 handoff 整个目录复制进项目
  cp -r /Users/leijiang/WorkBuddy/2026-05-17-task-5/artifacts/handoff/ ./docs/redesign-v9.3/

【步骤 2】完整阅读 docs/redesign-v9.3/ 下所有文件
  - 先读 00-README.md（总览）
  - 再读 04-implementation-plan.md（8 个 PR 拆分）
  - 然后是 01/02/03/05/06

【步骤 3】打开浏览器查看视觉对比稿
  /Users/leijiang/WorkBuddy/2026-05-17-task-5/artifacts/moneybag-mobile-redesign.html
  这是 7 页"现状 vs 重设计"的并排对比，是设计意图的最终来源。

【步骤 4】阅读完毕后，请总结向我汇报：
  - 你理解的 8 个 PR 顺序
  - 第一个 PR (PR-1 基建) 你打算做什么
  - 任何疑惑或风险点

【步骤 5】等我确认 OK 后，开始执行 PR-1
  - 严格按 04-implementation-plan.md 的 PR-1 描述
  - 完成后跑 05-acceptance-tests.md 的 PR-1 验收清单
  - 全部 ✅ 才 commit，commit message 按 04 末尾的格式

【约束】
  - 禁止引入 React / Vue / Tailwind / 任何打包工具
  - 必须保留 Vanilla JS + 原生 CSS 架构
  - 一次只做一个 PR，做完等我 review 再开下一个
  - 现有后端 API、PWA 配置不动
  - 测试必须通过：pytest backend/tests/ + 手动 smoke test

开始吧。
```

---

## 你（用户）这边要做的

1. 把这段 prompt 整个复制
2. 切到 `moneybag-for-claudecode` 项目
3. 在 Claude Code 对话框粘贴并发送
4. Claude Code 开始读文档 → 它会向你汇报理解 → 你确认 → 它开始 PR-1

## 期望的回流（每个 PR 后我可以帮你 review）

每完成一个 PR，让 Claude Code 把 commit hash + 改动文件列表 + 浏览器截图发回来。
你转给我，我帮你检查：
- 视觉是否真的跟设计稿对应
- 有没有偷懒的地方（比如 emoji 太多、卡片间距没改）
- CSS 变量用得对不对

8 个 PR 全部走完后，我们一起 review 整体效果，必要时再迭代。

---

## 如果 Claude Code 卡住了

让它运行：
```bash
ls /Users/leijiang/WorkBuddy/2026-05-17-task-5/artifacts/handoff/
```
如果看不到这个目录，说明权限问题（这是另一个 workspace）。可以让它直接读取这两个文件作为参考：
- `01-design-tokens.css` 的内容（颜色、字体的真理）
- `02-component-library.css` 的内容（组件类）

这两个文件可以直接放进项目作为 starter，其它文档作为说明。
