# 11 - 关键风险 + 验收指标 + 一次到位风险兜底

> **何时读**：开工前、M1 W2 止损点、评估重构进度、年度复盘时。
> 对应主文档章节：§九、§十、§14.3

---

## 🔗 模块契约

**本文件是风险与验收指南**，不定义接口。

**上游（本文件关联谁的风险）**：
- 整套设计的每个文件都贡献了一部分风险

**下游（谁引用本文件）**：
- `10-roadmap.md` milestone 验收 → 引用本文件验收指标
- `00-ANCHOR.md` → 开工前 Checklist 引用本文件 §七
- `13-governance.md` 季度体检 → 验收指标表

**改动本模块前必须评估**：
- **验收指标** → 同步 `10-roadmap.md` milestone
- **风险项** → 和各业务文件的"关键不变式"一致
- **开工 Checklist** → 6 条硬性前置，任一不做不开工

---

## 一、关键风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 数据源不稳定 | 主源（Tushare）+ 降级（AKShare）+ 本地缓存 |
| 用户把评分当买入信号 | 所有输出加免责声明："仅为数据筛选，不构成投资建议" |
| 规则引擎阈值不合理 | `03-rule-engine.md` 给默认矩阵 + 用户可覆盖 + hard limits 兜底 |
| AI 解读偶尔胡说 | `04-ai-interface.md` §四 字段级硬边界 + red_team_audit 拦截 |
| **"翻译事实"滑回"主观判断"** | 禁用词表 + 正则校验 + 必须引用规则引擎已产出的结论 |
| **一刀删 agent_memory 伤到复盘** | `02-code-audit.md` §四 拆两半：LLM 记忆删，人类档案留 |
| **Phase 切换打穿前端** | schema 兼容层 + 两周并行期 + Deprecation header 过渡 |
| **对话场景 LLM 越界** | `04-ai-interface.md` §五 受限追问 + 锚点绑定 + 禁用词 + 回落话术 |
| **资产负债表维护成本黑洞** | `06-family-profile.md` §四 MVP + 过期标识 + 低摩擦填写 |
| **事前拦截的形式主义** | `07-decision-guard.md` §一 改为事后复盘为主 |

---

## 二、验收指标

半年后用这几条衡量重构是否真有用：

| 指标 | 目标 | 衡量方式 |
|------|------|---------|
| red_team_audit 拦截率 | >99% | 对 1000 条历史 LLM 输出回放 |
| 对话场景禁用词出现率 | <3% | 日常对话回放扫描 |
| LLM 单次调用 token | <1000 | `llm_gateway.py` 打点统计 P50 |
| 资产配置偏离度响应率 | >80% | 触发提醒后 30 天内用户动手比例 |
| 用户"别犯蠢"事件数 | 同比 -50% | 集中度 >60% / 浮亏 >25% 事件次数 |
| LLM 输出包含禁用词比例 | <1% | 正则扫描 `market_environment` / `direction_notes` |
| 冲动交易次数（模式 B 视角）| -15% ~ -30%（半年后）| 7 点清单红灯 ≥3 盏的交易数同比 |
| 白天首屏响应 | <3s | API 采样 |
| 凌晨工厂成功率 | >95% | 连续 30 天统计 |

---

## 三、一次到位的风险盘点与兜底（开工前必读）

M1 完整四层改造乍看激进，实际**风险可控且总成本低于分批迭代**。前提是满足四个条件：

### 3.1 前置条件

| 前提 | 是否满足 |
|------|---------|
| 一个人开发（无协作冲突）| ✅ |
| 项目仍在迭代期（未固化）| ✅ |
| 无团队协作压力 / 外部 deadline | ✅ |
| 用户规模小（自用 + 家人）| ✅ |

**任一条反过来 → 风险翻倍，必须改分批策略**。

### 3.2 为什么"一次到位"反而比"小步迭代"总风险低

小步迭代的**隐性成本**：
- 每拆一块都需要"新老适配层"：老代码调新接口、新代码调老接口，桥越多 bug 越多
- 半迁移状态持续数月：三分之一老代码 + 三分之一迁移中 + 三分之一新代码，**比现在还乱**
- 每个新功能都要纠结"是写老架构还是新架构"

一次到位的**收益**：
- 新老并存只持续 1-2 周（M1 之内）
- M1 结束后代码只有一种结构，心智负担低
- 后续新增功能时无需架构选择困扰

**类比**：搬家一次搬完两天累但干净，每天搬一点持续一个月，整个月都是纸箱。

---

## 四、六类具体风险 × 兜底矩阵

| # | 风险 | 概率 | 后果 | 兜底措施 |
|---|------|------|------|---------|
| 1 | 拆 `main.py` 时路由漏迁 | 中 | 中（前端 404）| 迁移前录 199 个路由的响应 snapshot；CI 加"路由总数必须 = 199"断言；每拆一批对比 snapshot |
| 2 | 服务间 import 绕成循环依赖 | 中 | **高**（启动失败）| W1 开工即装 `import-linter`；CI 跑导入分析；规则写死：`api/` 只依赖 `use_cases/`+`domain/`，`domain/services/` 互相不 import |
| 3 | 缓存统一时丢数据 / TTL 错算 | 低 | 中 | 新 `infra/cache` 与旧 `_cache={}` **并存双写 1 周**，命中率一致后再删老的 |
| 4 | 持久化抽象导致历史数据读不出来 | 低 | 🔴**极高**（你家的持仓/交易记录）| 见 §五 数据安全专项 |
| 5 | 中途卡住、半成品比现在更烂 | 中 | 高 | 见 §六 心理/流程兜底 |
| 6 | 重构期间继续加新业务功能 | **高** | 中 | M1 四周**功能冻结**；必须做的需求挂老代码 TODO，M2 再迁 |

---

## 五、数据安全专项（最怕的那条）

DATA_DIR 里装着你家的**真实持仓、交易流水、画像、决策日志**——这些**不能丢**。三道防线：

### 5.1 开工前（硬性前置）

- 完整备份 `DATA_DIR` → **3 份异地**（本地硬盘 + U 盘 + 云盘）
- Git 打 tag `pre-refactor-2026-04-23` + 云端推送
- 写一份"**数据回滚 SOP**"：一张纸讲清楚如果 M1 崩了怎么 5 分钟内回到现在的状态

### 5.2 迁移期（接口层原则）

- `infra/store` 的接口层**只做读写 wrap，不改文件结构**
- 新老代码读**同一份物理文件**，保证能随时切回
- 每次跑新代码后用 `diff` 对比输出 JSON 和旧代码一致

### 5.3 M1 结束后（暂不迁 DB）

- 只有 M5 完成、稳定运行 1 个月后，才考虑把 DATA_DIR 迁到 SQLite
- **M1 期间绝不改数据格式**

---

## 六、"中途放弃"的心理/流程兜底

这是唯一一个工程手段兜不住的风险。一个人重构最容易卡在 **W2-W3**，那时：
- 代码看起来更乱了（新老并存）
- 看不到立竿见影的业务价值
- 想回去做"有意思的新功能"

### 6.1 兜底措施

| 措施 | 说明 |
|------|------|
| W 粒度拆分 | M1 切成 W1/W2/W3/W4 四周，每周末必须有**可展示成果**（如"main.py 从 4044 减到 2800"、"Protocol 落地"）|
| W2 末止损点 | 如果 `main.py` 没拆下来、缓存没统一 → **停下冷静**，是方案错了还是估计错了，别硬上 |
| 功能冻结 | M1 四周内**只改架构不加业务** |
| 进度可视化 | 在仓库放一个 `REFACTOR_STATUS.md`，每周更新，给自己正反馈 |

---

## 七、开工前硬性 3 道保险（Checklist）

M1 W1 Day 1 之前必须完成：

- [ ] `DATA_DIR` 完整备份 3 份（本地 + U 盘 + 云盘）
- [ ] Git 打 tag `pre-refactor-2026-04-23` 并推送到远端
- [ ] 写好"数据回滚 SOP"（一页纸，5 分钟能回到现在状态）
- [ ] 装好 `import-linter`，CI 跑导入分析
- [ ] 跑一遍 199 个路由 smoke test，存 snapshot 到 `tests/snapshots/`
- [ ] 在仓库根目录建 `REFACTOR_STATUS.md`

**这 6 条做完再开工**。任一条没做完，**M1 启动时间推迟**。

---

## 八、三条情况不适合一次到位（反向判断）

如果未来出现以下情况，改回分批策略：

1. 项目有多个并发用户，重构期间不能停 → 你没有 ✅
2. 下个月有硬 deadline（年底必须上线某功能）→ 你没有 ✅
3. 精力只能保证每周 <5 小时 → **自己诚实评估**

第 3 条特别重要：一个人重构的最低投入是**每周 10-15 小时**。低于这个门槛，一次到位会拖成半成品，反而比分批糟糕。

**如果每周可投入 <10 小时**：
- M1 拉长到 8 周
- 或改成"只拆 main.py（方案 A）+ 延后 infra 层"的半重构

---

## 九、开源项目借鉴清单（设计参考池）

> 📌 **状态**：仅做文档沉淀，**开工时（M2 W3、M3 W1）再来查阅此节**。现在不落地任何代码。

### 9.1 只借这 2 个，其他拒绝

| 项目 | 借鉴什么 | 时机 | 对应设计文件 |
|------|---------|------|-------------|
| [Ghostfolio](https://github.com/ghostfolio/ghostfolio)（AGPL-3.0）| `prisma/schema.prisma` 的 `Activity` / `Account` 数据模型 | M2 W3 做资产负债表 MVP 时 | `06-family-profile.md` |
| [behavioral-drift-analytics](https://github.com/yocase11/behavioral-drift-analytics) | 6 大认知偏差的**数学定义与阈值经验** | M3 W1 做事后复盘时 | `07-decision-guard.md` + `03-rule-engine.md` |

### 9.2 明确拒绝的项目（看了浪费时间）

| 项目 | 为什么拒绝 |
|------|----------|
| ai-hedge-fund (virattt) | 13 大师 Agent + LLM 全决策，**违反不变式 1/2/3** |
| Backtrader | 量化回测框架，和"家庭教练"定位无关 |
| FinRL / Qlib | AI 预测选股，**违反不变式 1** |
| 阿布量化 | 同上 |
| Maybe | 2025.7 已归档，无维护 |

### 9.3 借鉴红线（AGPL + 红线不变式）

- ❌ **不 copy 代码**：Ghostfolio 是 AGPL-3.0，传染性强。只读 schema 定义，自己手写实现
- ❌ **不用他们的 ML 模型**：behavioral-drift 的 XGBoost / LSTM 违反不变式 1（AI 不预测）
- ❌ **不抄 UI**：Ghostfolio Angular / behavioral-drift Dash，和你 Streamlit 技术栈不同
- ✅ **只抄设计思路、字段定义、阈值经验**

### 9.4 Ghostfolio Activity 模型参考（M2 W3 查）

```
Activity {
  type: "BUY" | "SELL" | "DIVIDEND" | "FEE" | "INTEREST" | "LIABILITY"
  date / symbol / quantity / unitPrice / fee / account / currency / comment
}
```

- MoneyBag 对应：`domain/models/portfolio.py` 的 Transaction / Holding
- **启发点**：
  - `DIVIDEND` / `FEE` / `INTEREST` / `LIABILITY` 的细分值得加进你的 Transaction model
  - `Account` 直接对标 `06-family-profile.md` §2.3 的 sub_accounts
  - `comment` 字段可以塞 7 点清单结果（买入理由多选 + 决策质量分）JSON

### 9.5 behavioral-drift 6 大偏差参考（M3 W1 查）

| 偏差名 | 定义思路 | 可能对应的规则触发 |
|--------|---------|-------------------|
| 损失追逐指数 | 连亏后追加仓位同品种 | 近 30 天亏损 ≥3 笔 + 本次仍加仓同品种 |
| 风险升级分数 | 连续调整杠杆 / 仓位 | 30 天内同方向加仓 >3 次 |
| 交易冲动指数 | 短时高频交易 | 单周交易次数 >5 |
| 波动率反应分数 | 大跌后恐慌 / 大涨后 FOMO | 当周市场跌 >5% 本次仍加仓 |
| 行为熵 | 交易模式混乱度 | M6 再考虑，需要滚动窗口 |
| 行为漂移分数 | 近期偏离历史稳定模式 | 复盘归因时参考 |

**M3 W1 决定**：是否把 7 点清单扩为 8 点，加一条"行为风险触发"。届时再查此节。

### 9.6 开源差异化定位（M5 旧版下线后再用）

MoneyBag 在开源市场的**独家卖点**（空白位）：

> **AI organizes. Rules decide. You pick.**
>
> - 🚫 No price predictions
> - 🚫 No buy/sell signals
> - 🚫 No position size recommendations
> - ✅ Target allocation tracking
> - ✅ Behavioral drift detection（借鉴 behavioral-drift-analytics）
> - ✅ Explainable rule engine
> - ✅ Decision quality retrospective

**定位差异**：

| 现有 | MoneyBag |
|------|----------|
| 券商 App | 不下单，教你做 CFO |
| Backtrader / Qlib | 不回测，不做 alpha |
| ai-hedge-fund / FinRL | 拒绝预测，无 Agent 决策 |
| Ghostfolio | 加了目标配比 + 行为漂移 + 再平衡 |
| behavioral-drift-analytics | 把研究变成家庭日常工作流 |

**README 口号候选**（M5 后正式用）：
1. "Your Family CFO Assistant, Not Another Stock-Picking AI"
2. "We explicitly refuse to predict."
3. "Every AI output has a rule-engine source."

---

## 📎 相关文件

- **框架改造细节（M1）** → `12-framework-refactor.md`
- **路线图与时间表** → `10-roadmap.md`
- **长期治理指标（季度体检）** → `13-governance.md`
- **Agent Memory 拆分（不伤复盘）** → `02-code-audit.md` §四
