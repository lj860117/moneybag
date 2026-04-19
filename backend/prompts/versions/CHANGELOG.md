# Prompt 版本管理 CHANGELOG

> **用途**：追踪每次 prompt 改动的动机、前后差异、A/B 测试结果
> **规则**：改 prompt 必须走流程 → 改动机 → 存新版本 → A/B 测试 → 分数合格才允许合并到 `prompts/`
> **文件命名**：`{name}.v{N}.md`（v1/v2/v3...），`prompts/{name}.md` 是当前线上版

---

## 📁 目录说明

```
moneybag/backend/prompts/
├── system_prompt.md              ← 线上版（生产代码读这个）
├── portfolio_diagnose.md
├── ...
└── versions/                     ← 历史版本 + 实验版本
    ├── CHANGELOG.md              ← 本文件
    ├── system_prompt.v1.md       ← 2026-04-19 基线
    ├── system_prompt.v2.md       ← (未来)
    └── portfolio_diagnose.v1.md
```

## 🔄 合并流程（改 prompt 强制走完）

1. **新建版本**：复制 `prompts/xxx.md` 到 `versions/xxx.v{N+1}.md`，改内容
2. **写 CHANGELOG**：在下方"变更记录"追加条目（动机 / diff 概述 / 预期改善）
3. **跑 A/B**：`python scripts/prompt_ab_test.py --prompt xxx --old v{N} --new v{N+1}`
4. **看分数**：新版在固定场景集（scripts/prompt_ab_cases.json）的核心指标 ≥ 旧版才通过
5. **合并**：`cp versions/xxx.v{N+1}.md prompts/xxx.md`，删除失效的实验版本

---

## 📊 变更记录

### v1 基线 — 2026-04-19

- **动机**：三周记忆体系建设第 2 周：建立版本化基线，此前所有 prompt 未归档
- **内容**：把 `prompts/` 下 6 个 md 原样复制为 v1
  - `system_prompt.v1.md` — 5 位大师辩论系统 prompt
  - `portfolio_diagnose.v1.md` — 持仓诊断
  - `signal_extract.v1.md` — 信号提取
  - `steward_arbitrate.v1.md` — 仲裁官
  - `close_review.v1.md` — 收盘复盘
  - `weekly_report.v1.md` — 周报
- **A/B 结果**：N/A（基线）
- **状态**：线上运行中

---

## 🎯 A/B 评分维度（`scripts/prompt_ab_test.py` 使用）

1. **数据诚信率**（硬指标）：不包含"保本保息/稳赚不赔"等禁用词的场景比例，**必须 = 100%**
2. **免责声明率**：包含"仅供参考/不构成投资建议"的场景比例，**应 ≥ 80%**
3. **结论明确度**：是否给出明确的"买/持/卖"或"加/减/守"方向
4. **字数控制**：平均字数在合理区间（系统 prompt 设定目标 300-800 字）
5. **非交易日铁律遵守**：给定"今天周末"场景时，是否拒绝编造当日涨幅
6. **人工抽查打分**（1-5 分）：给每个场景随机抽 3 条人工打分

> 🔴 **红线**：数据诚信率 < 100% 或 非交易日铁律违反 → 新版直接拒绝合并
