# 🚀 v9.3.0 视觉重设计 · 从这里开始

## 一句话现状

设计师 Jax 已经把 v9.3.0 的全套视觉重设计文档放进 `docs/redesign-v9.3/`，
并把 2 个核心 CSS 文件放在了 `styles/` 下，**等待 Claude Code 按 PR 顺序实施**。

## 文件已就位

```
moneybag-for-claudecode/
├── styles/                              ← 新增
│   ├── design-tokens.css                  ← CSS 变量（颜色/字号/间距）
│   └── components.css                     ← 组件类（卡片/按钮/Avatar 等）
└── docs/redesign-v9.3/                  ← 新增
    ├── START-HERE.md                      ← 你正在看的文件
    ├── 00-README.md                       ← 总览
    ├── 01-design-tokens.css               ← styles/design-tokens.css 的源副本
    ├── 02-component-library.css           ← styles/components.css 的源副本
    ├── 03-page-specs.md                   ← 7 页改造规范
    ├── 04-implementation-plan.md          ← 8 个 PR 拆分计划
    ├── 05-acceptance-tests.md             ← 验收清单
    ├── 06-migration-guide.md              ← 旧值→新变量映射
    ├── CLAUDE-CODE-PROMPT.md              ← 给 Claude Code 的开场提示词
    └── visual-reference.html              ← 7 页"现状 vs 重设计"完整视觉对比稿
                                              （用浏览器打开看）
```

## 给 Claude Code 的指令

**复制下面这段，粘到 Claude Code 对话框：**

```
我要给 MoneyBag 升级到 v9.3.0 视觉重设计。设计师 Jax 已经把所有规范打包好放进
docs/redesign-v9.3/，CSS 文件放进 styles/。请你完成以下事情：

【步骤 1】完整阅读 docs/redesign-v9.3/ 下所有文件
  - 先读 00-README.md（总览）
  - 再读 04-implementation-plan.md（8 个 PR 拆分）
  - 然后是 03-page-specs.md / 05-acceptance-tests.md / 06-migration-guide.md
  - 01/02 是 CSS 文件，已经放进 styles/，浏览即可

【步骤 2】用浏览器打开 docs/redesign-v9.3/visual-reference.html
  这是 7 页"现状 vs 重设计"的并排对比，是设计意图的最终来源。

【步骤 3】阅读完毕后，向我汇报：
  - 你理解的 8 个 PR 顺序
  - 第一个 PR (PR-1 基建) 你打算做什么
  - 任何疑惑或风险点

【步骤 4】等我确认 OK 后，开始执行 PR-1
  - 严格按 04-implementation-plan.md 的 PR-1 描述
  - 完成后跑 05-acceptance-tests.md 的 PR-1 验收清单
  - 全部 ✅ 才 commit

【约束】
  - 禁止引入 React / Vue / Tailwind / 任何打包工具
  - 必须保留 Vanilla JS + 原生 CSS 架构
  - 一次只做一个 PR，做完等我 review 再开下一个
  - 现有后端 API、PWA 配置不动
  - 测试必须通过：pytest backend/tests/ + 手动 smoke test

开始吧。
```

## 推进流程

1. ✅ 文档已就位（Jax 完成）
2. ⏳ Claude Code 读完文档 → 向你汇报理解
3. ⏳ 你确认 OK → Claude Code 开 PR-1（基建：注入 CSS）
4. ⏳ PR-1 完成 → 你 review（可发截图给 Jax 复核）
5. ⏳ 重复 PR-2 … PR-8
6. ⏳ 全部完成 → 升级版本号到 v9.3.0 → 发布

预计总工作量：~ 18 小时（约 4-5 天）

## 当前应用现状提醒

- 版本：v9.2.4
- 部署：http://150.158.47.189:8000/
- 后端有未提交改动：profiles.py（与本次重设计无关，Claude Code 不要动）
- Git 分支：main

---

**Jax** · 2026-05-17 · 给 BuLuoGeLi 也看看，听听她的家庭视角再决定要不要全部上线
