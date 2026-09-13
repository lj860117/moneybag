# Prompt 版本管理 CHANGELOG

> **用途**：追踪每次 prompt 改动的动机、前后差异、A/B 测试结果
> **规则**：改 prompt 必须走流程 → 改动机 → 存新版本 → A/B 测试 → 分数合格才允许合并到 `prompts/`
> **文件命名**：`{name}.v{N}.md`（v1/v2/v3...），`prompts/{name}.md` 是当前线上版

---

## 📁 目录说明

```
moneybag/backend/prompts/
├── system_prompt.md              ← 线上版（生产代码读这个）
├── holding_diagnose.md
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

### v9.9.26 P2 Prompt 治理 — 2026-09-13

- **动机**：消除"同一功能两套口径 / 改了不生效的死 prompt"两类漂移源。
  `api/holdings.py:_compute_ai_checkup` 的 AI 持仓体检 system prompt 一直内联在 .py 里，
  改 prompt 要翻代码；同时 `prompts/portfolio_diagnose.md` 是无人加载的孤儿文件，
  容易让人误以为它在生效。
- **内容**：
  1. **新增落盘**：`prompts/holding_diagnose.md` ← 从 `api/holdings.py` 内联串原样外移，
     **正文与原内联串逐字节一致（169 字符，末尾无换行）**，属纯搬家、零行为变更。
     归档为 `versions/holding_diagnose.v1.md`（首次落盘即 v1；为与线上正文逐字节对齐，
     该归档同样不带结尾换行，是本目录唯一的无尾换行文件）。
     代码级版本常量 `api/holdings.py:HOLDING_DIAGNOSE_PROMPT_VERSION = "v1"`，
     与归档文件名一一对应；AI 体检返回体新增 additive 字段 `prompt_version`，便于归因。
     读取走 api 层统一入口 `shared_helpers._load_named_prompt()`（同一 `backend/prompts/` 目录、
     fail-open 回退到内联默认串），不再新增第三套 loader。
  2. **删除死 prompt**：`prompts/portfolio_diagnose.md`。
     理由（证据）：全仓 `.py` **无任何**加载点；其声明的输出键
     `overall_grade` / `strengths` / `weaknesses` / `action_items` 全仓 **0 处消费**；
     其声明的宿主 `services/portfolio_doctor.enrich()` 最终实现为**纯规则**
     （stress_test + HHI 集中度 + health_score），从未接 LLM。
     即：它是 `docs/moneybag-v4-ultimate-plan.md`「Prompt工程」表里一项**从未落地**的设计稿，
     而非 holdings 体检的重复实现 —— 两处口径并不重叠（portfolio_doctor 覆盖股票+基金+HHI+压力测试，
     holdings 体检只覆盖基金净值百分位+行业集中度，输出自由文本）。
     保留归档 `versions/portfolio_diagnose.v1.md`，若日后要建 portfolio_doctor 的 LLM 层，
     请以新版本号重新落盘，而不是让一个没人读的文件继续挂在线上目录里。
     同步删除 `scripts/prompt_ab_cases.json` 中指向该已删文件的 `portfolio_diagnose` 用例块
     （否则 `prompt_ab_test.py --prompt portfolio_diagnose` 会 FileNotFoundError）。
  3. **约束可测化**：新增 `tests/test_prompt_governance.py`（离线，只读文件+扫源码）：
     落盘一致性 / 孤儿 md 反向断言（白名单显式豁免）/ 防编造硬约束存在性 / 数值禁令。
- **A/B 结果**：N/A（本次为 prompt 搬家与死文件清理，未改动任何 prompt 正文）
- **状态**：线上运行中
- **仍未解决**：`prompts/signal_extract.md`、`prompts/weekly_report.md` 同为**无代码加载**的孤儿 md
  （在测试里以白名单显式豁免并注明原因），需另派任务决定「接线 or 删除」。

---

### v9.9.26 close_review 防编造约束 — 2026-09-13

- **动机**：`prompts/close_review.md` 是**活 prompt**（`scripts/stock_monitor_cron.py:1175`
  在 `run_close_review()` 里加载，生成每日收盘复盘推送正文），但正文**没有任何防编造约束句**：
  既没禁止编造涨跌幅/收益率，也没要求"数据不足就直说"，反而在
  `# 输出结构` 的示例里写 `（如"震荡小涨，你的组合跑赢大盘 0.3%"）`——
  **用带具体数字的示范诱导模型编造数字**。这与项目铁律
  「算不出/没数据必须是 None + 原因，不得用占位数值；『没数据』不得伪装成『中性/看多』」直接冲突。
- **内容**：
  1. 新增 `# 数据诚信（铁律）` 段，4 条约束：**不得编造任何数据点**（涨跌幅/收益率/胜率/概率/点位
     只能引用输入里给出的数字）；数据缺失时直接说「数据不足」而非模糊话术蒙混；
     **不得把"没有数据"包装成结论**（禁止「表现平稳/整体中性/波动不大」等伪装性说法，须写明缺哪项）；
     不得为凑满 200-400 字补充任何输入中未出现的数字。
  2. 删除诱导性示例中的具体数字：`跑赢大盘 0.3%` → `跑赢大盘，不要写具体数字`。
  3. **保留全部既有约束**（200-400 字、纯中文、不输出 JSON / 英文术语 / 代码变量名、通俗易懂），未动骨架。
- **归档**：`versions/close_review.v2.md`（与改后线上正文逐字节一致）。
  上一版 `close_review.v1.md` 保留为改动前基线。
- **A/B 结果**：❌ **未跑通（未产出有效分数）**。
  本机无任何 LLM key（`DEEPSEEK_API_KEY`/`DOUBAO_API_KEY` 等一律 UNSET，仓库内无 `.env`），
  `LLMGateway` 对两版模型均打印「跳过 deepseek-v4-flash：未配置 key」并返回 fallback，
  落盘详情里 old/new 文本**同为 `[FALLBACK: api_error]`（21 字符）**。
  脚本因此输出「数据诚信率 100%、免责 0%、平均字数 21」——**两个版本跑的是同一个空占位串，
  该「✅ 允许合并」判决无任何证据价值，不构成合并依据**。v2 的合并依据是人工审阅 + 治理测试，
  不是 A/B。待有 key 的环境需补跑：
  `python backend/scripts/prompt_ab_test.py --prompt close_review --old v1 --new v2`
  （注意：需 `PYTHONPATH` 同时包含仓库根与 `backend/`，否则脚本在
  `backend/infra/llm/__init__.py:5` 处报 `ModuleNotFoundError: No module named 'infra'`）。
- **治理同步**：`tests/test_prompt_governance.py` 把 `close_review.md` 从
  `ANTI_FABRICATION_KNOWN_GAPS` 移入 `ANTI_FABRICATION_REQUIRED`
  （断言词取自本次实际写入的原文：`不得编造任何数据点` / `数据不足` / `伪装成判断`），
  缺口表随之清空并显式化「空即通过」；A/B 场景集新增 `close_review` 块（4 个 case：
  持仓行情缺失 / 脏空字段 / 凑字数诱导 / 非交易日铁律）。
- **状态**：线上运行中（v1 → v2，正文已生效）。

---

## 🎯 A/B 评分维度（`scripts/prompt_ab_test.py` 使用）

1. **数据诚信率**（硬指标）：不包含"保本保息/稳赚不赔"等禁用词的场景比例，**必须 = 100%**
2. **免责声明率**：包含"仅供参考/不构成投资建议"的场景比例，**应 ≥ 80%**
3. **结论明确度**：是否给出明确的"买/持/卖"或"加/减/守"方向
4. **字数控制**：平均字数在合理区间（系统 prompt 设定目标 300-800 字）
5. **非交易日铁律遵守**：给定"今天周末"场景时，是否拒绝编造当日涨幅
6. **人工抽查打分**（1-5 分）：给每个场景随机抽 3 条人工打分

> 🔴 **红线**：数据诚信率 < 100% 或 非交易日铁律违反 → 新版直接拒绝合并
