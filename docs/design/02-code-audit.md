# 02 - 现有代码诊断 + Agent Memory 拆分

> **何时读**：做代码改造前、迁移旧服务前、清理 Agent Memory 时。
> 对应主文档章节：§二、§5.4

---

## 🔗 模块契约

**本文件是迁移执行手册**，不定义运行时接口。

**下游（谁执行本模块的迁移计划）**：
- `10-roadmap.md` M1-M2 具体任务引用本文件
- `12-framework-refactor.md` §13.5 迁移对照表引用本文件

**改动本模块前必须评估**：
- 改 Agent Memory 拆分方案 → 同步 `10-roadmap.md` M2 W1 任务
- 发现新的踩红线模块 → 加到 §一 + 更新 roadmap

---

## 一、踩红线模块

### 1.1 `ai_predictor.py` — AI/ML 做涨跌预测 🔴

- **问题**：MLPRegressor + GradientBoostingRegressor，40+ 技术特征，预测未来 N 天收益率
- **本质**：让机器学习模型做金融时序预测 → 必死
- **改法**：
  - 删除预测功能（看涨/看跌/置信度）
  - 保留特征展示（当前 RSI、MACD 状态、波动率等）→ 只陈述事实
  - 或整模块删除，将特征展示并入 `signal` 或 `recommend_engine` 技术维度
- **当前进度**：`ai_predictor_v2.py`（136 行重写版）已写好，但 `main.py` 仍 `from services.ai_predictor import predict_stock/predict_portfolio/batch_predict`，**切换未生效**。

### 1.2 `decision_maker.py` — AI 直接输出买卖指令 🔴

- **问题**：把北向、融资、SHIBOR、情绪、信号、持仓全塞给 R1，输出 `buy/sell/hold/reduce/add` + `position_pct`
- **本质**：让 AI 做择时选股决策 → 无意义且危险
- **改法**：
  - R1 prompt 不再要求输出 action/position_pct
  - R1 只输出三类内容：市场环境总结、持仓健康度提醒、风险清单
  - 具体买卖动作由规则引擎或人决定
  - 保留 `_rule_based_decision` 作为快速参考（但明确标注"仅供参考"）
- **当前进度**：`decision_maker_v2.py`（212 行）已写好，但 `main.py` / `night_worker.py` 仍调用旧 `generate_decisions`，**切换未生效**。

---

## 二、正确模块（保留）

| 模块 | 为什么对 | 动作 |
|------|---------|------|
| `recommend_engine`（5 维评分） | 人定规则，程序执行 | 保留，加免责声明 |
| `signal`（13 维信号） | 纯数学计算 | 保留 |
| `valuation_engine` | 估值百分位，纯统计 | 保留 |
| `factor_data` | 北向/融资/SHIBOR 采集 | 保留 |
| `backtest_engine` | 程序验证，不预测 | 保留 |
| `stock_monitor` / `fund_monitor` | 持仓跟踪，纯记录 | 保留 |
| `decision_log` / `analysis_history` | 记录归档 | 保留 |

---

## 三、Prompt Engineering 反思

| 做法 | 效果 | 结论 |
|------|------|------|
| MODULE_META 底座 | 对 R1 是噪音 | 删除 |
| 大量 Skill/规则塞上下文 | 稀释注意力、反效果 | 精简 |
| Agent Memory 让 AI "记住"历史 | 过拟合温床 | **拆分：LLM 侧删，人类档案留**（见下文 §四） |
| 复杂 prompt 工程 | 打断 R1 推理链 | 极简化 |
| Prompt 版本管理 | 对人有用（A/B 对比） | 保留 |
| JSON 格式约束 | 降低解析失败率 | 保留 |
| 降级规则引擎 | 工程兜底 | 保留 |

**核心结论**：Prompt engineering 在金融量化上边际收益 ≈ 0。对 DeepSeek V4 同样适用。

---

## 四、Agent Memory 拆分方案（替代"一刀删"）

### 4.1 现状问题

`agent_memory.py` 1277 行，承担了 5 类职责，被 9+ 处 import：

| 调用方 | 位置 |
|--------|------|
| `main.py` 4 处 | build_memory_summary / record_emotion / auto_extract_insight / get_context |
| `main.py` 批量 import | 第 3495 行 `from services.agent_memory import (...)` |
| `main.py` | 3679 行 check_rules |
| `routers/wxwork.py` 2 处 | build_memory_summary / record_emotion / save_context |
| `services/agent_engine.py` | save_context |
| `services/pipeline_runner.py` | build_memory_summary / get_preferences |
| `services/steward.py` 2 处 | save_context |
| `scripts/auto_extract_cron.py` | 批量 |
| `scripts/daily_reflection_cron.py` | 批量 |
| `scripts/memory_archive_cron.py` | 批量 |

**一刀删会打穿 9+ 处代码**，也会伤及复盘能力。

### 4.2 拆分原则

按"**谁读**"分成两堆：

| 类别 | 谁读 | 处置 | 迁移目的地 |
|------|------|------|-----------|
| **LLM 记忆上下文**（build_memory_summary、get_context 的 llm_inject 路径、历史决策摘要注入 prompt）| LLM | **删除**（过拟合温床）| — |
| **情绪/偏好记录**（record_emotion、get_preferences）| LLM 读会诱导，人看有意义 | **降级**：只写入磁盘，不再注入 prompt | `domain/models/user_profile.py` |
| **历史决策档案**（save_context、auto_extract_insight）| 规则引擎 + 人 | **保留并迁移** | `domain/services/decision_log_service.py` |
| **规则检查**（check_rules）| 规则引擎 | **保留并迁移** | `domain/rule_engine/` |
| **夜间反思/归档 cron** | 定时任务 | **保留**，但不再往 LLM 回灌 | `scripts/daily_reflection_cron.py` |

### 4.3 切分动作清单

1. 盘点 9+ 处 `from services.agent_memory import ...`，按上表重定向 import。
2. `agent_memory.py` 中**"给 LLM 喂历史"的函数**（`build_memory_summary`、`get_context` 的 `llm_inject` 路径）直接删除。
3. 剩余函数按职责迁移到 `decision_log_service` / `user_profile` / `rule_engine`。
4. 最后 `agent_memory.py` 自然清空，可删。

### 4.4 原则

> **人可以记住自己犯过的错，但别让 LLM 读它再犯一次。**

---

## 五、当前代码健康度（体检数据）

| 指标 | 当前值 | 健康线 | 诊断 |
|------|--------|--------|------|
| `main.py` 行数 | 4044 | <500 | 🔴 |
| `main.py` API 路由 | 199 个 | 分布到 10+ router | 🔴 |
| `services/` 文件数 | 75 | — | ⚪ |
| `services/` 互相 import | 179 次 | <50 次 | 🔴 |
| 模块级 `_cache = {}` | 46 个文件 | 统一缓存层 | 🔴 |
| 直接读 `DATA_DIR` | 16 个文件 | 统一持久化层 | 🟠 |
| `routers/` 目录 | 只 2 个文件 | 按业务拆分 | 🟠 |
| `routes/` 目录 | 空壳 | — | 🟠 |
| `config.py` | 258 行单文件 | 分层配置 | 🟡 |

**结论**：到了必须一次重构的临界点。详见 `12-framework-refactor.md`。

---

## 📎 相关文件

- **四层架构怎么搭** → `12-framework-refactor.md`
- **规则引擎接管决策** → `03-rule-engine.md`
- **AI 边界原则** → `01-core-principles.md` + `04-ai-interface.md`
- **路线图中的切换时机** → `10-roadmap.md`
