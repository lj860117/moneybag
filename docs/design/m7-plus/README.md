# M7+ 长期增强设计 — 分批实施索引

> 本目录由 `14-m7-plus-enhancement-for-claude.md` §十「实施分批指南」拆分而来。
>
> **原文件不做修改**，本目录为实施阶段的工作文档。

---

## 批次清单与状态追踪

| 批次 | 文件 | 里程碑 | 状态 | 负责人 | 完成日期 |
|---|---|---|---|---|---|
| **全局契约** | [00-interface-contracts.md](00-interface-contracts.md) | — | 📌 常驻参考 | — | — |
| Batch 1：外部数据同步 | [01-batch-m7-external-sync.md](01-batch-m7-external-sync.md) | M7 | ⬜ 未开始 | — | — |
| Batch 2：Glide Path + 四档年龄下滑 | [02-batch-m7-glide-path.md](02-batch-m7-glide-path.md) | M7 | ⬜ 未开始 | — | — |
| Batch 3：10 维评分 | [03-batch-m8-10d-scoring.md](03-batch-m8-10d-scoring.md) | M8 | ⬜ 未开始 | — | — |
| Batch 4：行业偏离度 | [04-batch-m8-industry-deviation.md](04-batch-m8-industry-deviation.md) | M8 | ⬜ 未开始 | — | — |
| Batch 5：动态阈值 | [05-batch-m8-dynamic-threshold.md](05-batch-m8-dynamic-threshold.md) | M8 | ⬜ 未开始 | — | — |
| Batch 6：行为归因检测 | [06-batch-m9-behavior-detection.md](06-batch-m9-behavior-detection.md) | M9 | ⬜ 未开始 | — | — |
| Batch 7：事件解读 | [07-batch-m9-event-interpretation.md](07-batch-m9-event-interpretation.md) | M9 | ⬜ 未开始 | — | — |
| Batch 8：行为干预联动 | [08-batch-m9-intervention.md](08-batch-m9-intervention.md) | M9 | ⬜ 未开始 | — | — |
| **前端拆分改造** | [09-frontend-refactor.md](09-frontend-refactor.md) | M6+ 补丁 | ⬜ 未开始 | — | — |

> 状态标记：⬜ 未开始 | 🔄 进行中 | ✅ 已完成 | ⏸️ 阻塞中

---

## 全局耦合关系图

```
                              ┌────────────────────┐
                              │   §九 M7 开工检查表  │
                              │ (决策1/2/3 + 验证)  │
                              └─────────┬──────────┘
                                        │ 所有批次开工前必须填完
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
          ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
          │ Batch 1 (M7)    │ │ Batch 2 (M7)    │ │ Batch 5 (M8)    │
          │ 外部数据同步     │ │ Glide Path +    │ │ 行为归因检测     │
          │                 │ │ 四档年龄下滑     │ │ (基础版)         │
          │ 产出:           │ │                 │ │                 │
          │ ·broker parser  │ │ 产出:           │ │ 产出:           │
          │ ·sync use case  │ │ ·glide_path_    │ │ ·behavior_      │
          │                 │ │  rules.py       │ │  detector.py    │
          │ 🔗被引用:       │ │ ·deviation_     │ │ ·behavior_      │
          │ ·Batch 5 读交易 │ │  thresholds.py  │ │  reporter.py    │
          │  记录           │ │                 │ │                 │
          └────────┬────────┘ │ 🔗被引用:       │ │ 🔗被引用:       │
                   │          │ ·Batch 3 偏离度  │ │ ·Batch 6 联动版 │
                   │          │ ·Batch 4 行业偏离│ │ ·Batch 8 干预   │
                   │          │ ·Batch 5 动态阈值│ └────────┬────────┘
                   │          └────────┬────────┘          │
                   │                   │                    │
                   │          ┌────────▼────────┐          │
                   │          │ Batch 3 (M8)    │          │
                   │          │ 10 维评分        │          │
                   │          │                 │          │
                   │          │ 📋前置:Batch 2  │          │
                   │          │ (偏离度逻辑)     │          │
                   │          │                 │          │
                   │          │ 产出:           │          │
                   │          │ ·fund_filter_   │          │
                   │          │  rules.py       │          │
                   │          │                 │          │
                   │          │ 🔗被引用:       │          │
                   │          │ ·Batch 4 验证    │          │
                   │          └────────┬────────┘          │
                   │                   │                    │
                   │          ┌────────▼────────┐          │
                   │          │ Batch 4 (M8)    │          │
                   │          │ 行业偏离度(验证) │          │
                   │          │                 │          │
                   │          │ 📋前置:Batch 3  │          │
                   │          │                 │          │
                   │          │ 产出:           │          │
                   │          │ ·fund_filter_   │          │
                   │          │  validation.py  │          │
                   │          └─────────────────┘          │
                   │                                       │
          ┌────────▼────────┐                     ┌────────▼────────┐
          │ Batch 7 (M9)    │                     │ Batch 6 (M9)    │
          │ 事件解读         │                     │ 行为归因检测     │
          │                 │                     │ (联动→Batch 8)   │
          │ 📋前置:无       │                     │                 │
          │ (独立模块)       │                     │ 📋前置:Batch 5  │
          │                 │                     │ + §九验证1       │
          │ 产出:           │                     │                 │
          │ ·event_template │                     │ 产出:           │
          │  _library.py    │                     │ ·behavior_      │
          │ ·event_matcher  │                     │  intervention_  │
          │  .py            │                     │  rules.py       │
          └─────────────────┘                     └────────┬────────┘
                                                           │
                                                  ┌────────▼────────┐
                                                  │ Batch 8 (M9)    │
                                                  │ 行为干预联动     │
                                                  │                 │
                                                  │ 📋前置:Batch 6  │
                                                  │ + §九验证1       │
                                                  │                 │
                                                  │ 产出:           │
                                                  │ ·chart.py       │
                                                  │ ·tushare_chart  │
                                                  │  .py            │
                                                  └─────────────────┘
```

### 可并行组

```
M7 阶段: Batch 1 + Batch 2               ← 可并行
M8 阶段: (Batch 3 → Batch 4) + Batch 5   ← 两条线可并行
M9 阶段: Batch 6 + Batch 7 + Batch 8     ← 三条线可并行（Batch 6 依赖 Batch 5）
```

---

## 全局接口契约索引

> **完整接口定义**见 [00-interface-contracts.md](00-interface-contracts.md)。以下为快速参照表。

以下是跨批次共享的核心接口，具体定义见各批次文档：

| 接口/数据 | 定义方 | 消费方 | 定义位置 |
|---|---|---|---|
| `NightWorkerStep` Protocol | **全局** | Batch 1, Batch 7 | [00-interface-contracts.md](00-interface-contracts.md) §一 |
| `PipelineContext` / `StepResult` | **全局** | 所有凌晨工厂步骤 | [00-interface-contracts.md](00-interface-contracts.md) §一 |
| `*Defaults` dataclass 命名规范 | **全局** | 所有批次 | [00-interface-contracts.md](00-interface-contracts.md) §二 |
| `parse_broker_csv(broker_name, file_path) → List[Transaction]` | Batch 1 | Batch 5（读交易记录） | 01-batch-m7-external-sync.md |
| `Transaction` 数据结构 | Batch 1 | Batch 5, Batch 6 | 01-batch-m7-external-sync.md |
| `glide_path_rules.calculate_target_allocation(age) → AllocationTarget` | Batch 2 | Batch 3, Batch 4, Batch 5 | 02-batch-m7-glide-path.md |
| `deviation_thresholds.get_tolerance(volatility) → float` | Batch 2 | Batch 3, Batch 5 | 02-batch-m7-glide-path.md |
| `fund_filter_rules.run_10d_filter(candidates) → FilterResult` | Batch 3 | Batch 4（验证） | 03-batch-m8-10d-scoring.md |
| `behavior_detector.detect_patterns(transactions) → List[BehaviorPattern]` | Batch 5 | Batch 6, Batch 8 | 06-batch-m9-behavior-detection.md |
| `event_matcher.match_events(news_list) → List[MatchedEvent]` | Batch 7 | 凌晨工厂 | 07-batch-m9-event-interpretation.md |

---

## M7 开工检查表状态（镜像自原文 §九）

> 完整检查表见原文 `14-m7-plus-enhancement-for-claude.md` §九。此处仅追踪填写状态。

- [ ] 决策 1：Glide Path 与 M3 矩阵关系 → **未填写**
- [ ] 决策 2：行业集中度阈值关系 → **未填写**
- [ ] 决策 3：动态阈值与 M3 四档提示关系 → **未填写**
- [ ] 验证 1：`position_pct_max` 运行时临时覆盖 → **未验证**
- [ ] 验证 2：`night_worker_pipeline.py` 阶段扩展 → **未验证**
- [ ] 验证 3：`DataSourceProtocol` 半年报字段覆盖 → **未验证**
- [ ] 补丁 1：M3 股票侧 `tradability_filter.py` → **未完成**
- [ ] 补丁 2：`infra/data_source` 统一缓存层增加过期降级提示 → **未完成**
  - 当第三方数据源不可用时，自动切换本地缓存数据运行
  - 使用过期缓存时页面顶部显示黄色提示："部分数据源暂时不可用，当前数据更新至 XXXX-XX-XX"
  - 关键功能（资产负债表、偏离度检测）在离线状态下仍可正常使用
  - 属于 `infra/data_source` 基础设施层，M7+ 所有批次共享，不在各批次内重复实现
- [ ] 补丁 3：前端 `app.js` 拆分改造 → **未完成**
  - 详见 [09-frontend-refactor.md](09-frontend-refactor.md)
  - app.js 从 5148 行单文件拆分为核心层（~500 行）+ 11 个页面模块（`pages/*.js`）
  - M7+ 前端新增页面依赖此拆分完成后的 `pages/` 目录结构

**全部勾完 → M7 开工。任何一项空白 → 对应模块暂缓。**

---

## 全量文件落位汇总（15 个新增文件）

| 文件路径 | 归属批次 | 职责 | 预估行数 |
|---|---|---|---|
| `infra/data_source/import/brokers/base_broker_parser.py` | Batch 1 | Strategy 接口 + 编码检测 + 金额方向统一 | <100 |
| `infra/data_source/import/brokers/huatai_parser.py` | Batch 1 | 华泰证券 CSV 解析（首家） | <150 |
| `infra/data_source/import/bookkeeping_csv_parser.py` | Batch 1 | 记账软件 CSV 解析 | <150 |
| `use_cases/sync_broker_statement.py` | Batch 1 | 同步券商流水用例 | <150 |
| `domain/rule_engine/glide_path_rules.py` | Batch 2 | 年龄下滑 + 偏离度计算 + 风格偏离检测 | <250 |
| `domain/rule_engine/deviation_thresholds.py` | Batch 2 | 动态阈值（波动率分档）+ 极端行情保护 | <100 |
| `domain/rule_engine/fund_filter_rules.py` | Batch 3 | 前置过滤 + 10 维硬指标筛选 + 防同质化 | <300 |
| `tests/validation/fund_filter_validation.py` | Batch 4 | 规则逻辑验证 + 行业偏离专项 | <200 |
| `domain/services/behavior_detector.py` | Batch 5 | 7 种偏差检测逻辑 + 动态阈值集成 | <200 |
| `domain/services/behavior_reporter.py` | Batch 5 | 报告生成（Markdown 格式） | <200 |
| `domain/rule_engine/behavior_intervention_rules.py` | Batch 6 | 偏差→执行干预映射 + 白名单 + 降级 | <200 |
| `infra/knowledge/events/event_template_library.py` | Batch 7 | 20-30 类事件模板定义 | <300 |
| `infra/knowledge/events/event_matcher.py` | Batch 7 | 事件匹配 + 模板填充 | <150 |
| `api/chart.py` | Batch 8 | 迷你行情接口端点 | <50 |
| `infra/data_source/providers/tushare_chart.py` | Batch 8 | 日线数据拉取 + RSI 计算 | <100 |

**总计：15 个新增文件，全部独立，不碰已有文件。**

---

## 冲突处理 SOP（摘自原文附录 A）

实现过程中遇到以下情况时，**停止编码，记录冲突，等人工确认**：

| 冲突场景 | 处理方式 |
|---|---|
| 需要改 M1-M6 核心文件 | 停止，记录：文件路径 + 需要改什么 + 为什么 |
| `position_pct_max`/`action` 字段不够用 | 停止，记录：新增字段名 + 用途 + 是否可替代 |
| Protocol 接口需要扩展 | 停止，记录：哪个 Protocol + 新增方法签名 + 影响面 |
| 设计文档和现有代码不一致 | 停止，记录：文档哪里 + 代码实际怎样 + 建议按哪个来 |

**记录格式**（代码文件顶部注释块）：
```python
"""
⚠️ CONFLICT: [简述冲突]
- 设计文档要求: [xxx]
- 现有代码实际: [yyy]
- 建议: [zzz]
- 需要人工确认后才能继续
"""
```

---

## Batch 开工 prompt 模板（完整版）

> 覆盖原文 §十 的模板。每个 Batch 开工时复制使用。

```
请实现 M7+ 的 Batch N：[批次名称]

实现前必读（按顺序）：
1. 先读 m7-plus/README.md 的「M7 开工检查表状态」，确认决策/验证/补丁全部勾完。有空白项就停止
2. 读 0N-batch-xxx.md（本批次详细设计）
3. 读 00-interface-contracts.md（跨批次共享接口）
4. 读 09-frontend-refactor.md §七（前端落位表，确认本批次对应的前端文件）

只实现本批的文件：
- 后端：[从 Batch 文档的"文件落位"表复制]
- 前端：[从 09-frontend-refactor.md §七 的落位表复制]

前置依赖（如有）：
- 请先读以下已完成的文件确认接口：[前一批产出的文件路径，无则删除此行]

约束：
- 不修改 M1-M6 已有文件（defaults.py 可新增独立 dataclass）
- 遇到冲突按 README.md 冲突处理 SOP 处理
- 前后端必须同步交付：后端 API + 对应前端页面 + index.html/sw.js 更新（如有新建前端文件）
- 本批完成后，列出产出文件清单（后端+前端）供人工验收
```

---

## M10+ 远期方向（M9 跑稳后再评估）

> 以下方向均不在 M7-M9 范围内，仅作为远期探索记录。所有方向严格遵循核心原则：不预测、不推荐、不控制交易、不做收益归因。

### 1. 家庭联合资产负债表 + 跨账户全局纪律监控

**痛点**：每个券商 App 只能看到自己账户的情况，家庭整体的真实风险暴露无人监控。一方在华泰买了 30% 科技，另一方在中信也买了 30% 科技，单账户合规但家庭整体 60%，严重超标。

**方案**：
- 导入所有家庭成员、所有券商账户的 CSV 流水，聚合为家庭级资产负债表
- 在家庭聚合视图上运行偏离度检测：家庭整体股债比、行业集中度、风格偏离
- 新增家庭级纪律规则（如家庭整体单行业 ≤25%），与个人级规则并行

**技术基础**：Batch 1 CSV 导入 + M2 家庭画像 + Batch 2 偏离度计算的自然延伸，只是数据聚合层，不需要交易控制能力。

### 2. 纪律执行评分卡（纯过程评估，不看盈亏）

**痛点**：M5 复盘报告列出违规事实，但没有量化评分和趋势追踪。用户无法直观感知"我的纪律执行力这个月有没有进步"。

**方案**：
- 评分只看"你的操作是否符合你自己定的规则"，和盈亏**完全脱钩**
- 示例：规则"PE 分位 >70% 不买入"，在 60% 买入得 100 分，在 80% 买入得 0 分——不管后来赚了还是亏了
- 示例：规则"单行业 ≤25%"，主动调仓从 30% 降到 20% 得 100 分——不管该行业后来涨了还是跌了
- 生成月度纪律执行评分卡，长期追踪得分趋势

**核心约束**：评分公式里**禁止出现任何收益/亏损/涨跌相关变量**。一旦引入收益数据，就会滑向收益归因，违反核心原则。

---

## 相关文件

- 原始设计文档：`../14-m7-plus-enhancement-for-claude.md`（不修改）
- 全局接口契约：[00-interface-contracts.md](00-interface-contracts.md)
- 自查报告：[拆分合理性检查.md](拆分合理性检查.md)
