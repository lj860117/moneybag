# 03 - 规则引擎详细设计

> **何时读**：做资产配置、再平衡、7 点清单、风险画像、任何涉及"阈值/判断"的开发时。
> 对应主文档章节：§5.5

---

## 🔗 模块契约（M1 W1 前：文档约束 / M1 W1 后：见 Protocol）

**上游（本模块消费谁的输出）**：
- `06-family-profile.md` → `FamilyProfile.risk_preference / family_stage / age / members / sub_accounts`
- `06-family-profile.md` → 资产负债表字段（现金 / 房贷 / 保险额度 / 应急金月数）
- `06-family-profile.md` → 字段过期标识（规则引擎据此决定是否跳过相关判断）
- `05-scheduling.md` → A 层实时事实（持仓金额、集中度、浮亏）

**下游（谁消费本模块的输出）**：
- `07-decision-guard.md` → 7 点清单每条规则的判断依据来自本模块阈值
- `04-ai-interface.md` → AI 解读层的"事实"输入全部来自本模块 `compute_facts()`
- `05-scheduling.md` → 凌晨工厂 `night_worker_pipeline` 调用 `rule_engine.compute_all()`
- 前端 UI → 偏离度展示、风险画像卡片

**改动本模块前必须评估**（见 ANCHOR 改动传播表）：
- 改**阈值** → 同步 `07`（清单判断标准）、`11-risk-metrics.md`（验收指标）
- 改**接口签名** → 同步 `07` + `04` + `05` 三方调用
- 改 **`defaults.py` 常量名** → 全局 grep 引用点
- 新增**规则** → 评估是否加入 7 点清单、是否需要新 RAG 常识篇

**M1 W1 后**：所有上下游接口变成 `domain/protocols/rule_engine.py` 里的 Protocol 签名，本头部瘦身为"见 RuleEngineProtocol"。

---

**规则引擎是整个系统的命门**。不把规则引擎设计死，就是把 LLM 的黑箱换成阈值的黑箱。本文件把所有规则、阈值、矩阵一次性钉死。

---

## 一、位置与代码结构

规则引擎在 `domain/rule_engine/` 下，由以下文件组成：

```
domain/rule_engine/
├── allocation_rules.py    # 目标配比 / 偏离度阈值
├── checklist_rules.py     # 7 点决策检查清单（详见 07-decision-guard.md）
├── risk_rules.py          # 集中度 / 止损 / 回撤
├── scoring_rules.py       # 5 维评分阈值
└── defaults.py            # ⭐ 所有硬阈值常量，集中管理
```

**所有阈值**写在 `defaults.py`，业务代码 import 使用。用户可在 `user_profile` 中覆盖，但不得突破 hard limits。

---

## 二、目标资产配比矩阵

### 2.1 输入

```python
{
    "risk_preference": "conservative" | "balanced" | "aggressive",  # 风险偏好
    "family_stage": "single" | "married_mortgage" | "with_children" | "near_retirement",
    "age": int,
}
```

### 2.2 输出

```python
{
    "stock_pct": int,   # 股票
    "bond_pct": int,    # 债券
    "cash_pct": int,    # 现金
    "gold_pct": int,    # 黄金
}
```

### 2.3 默认矩阵（可用户覆盖）

| 风险偏好 \ 阶段 | 单身/无负担 | 已婚/有房贷 | 有孩家庭 | 临退休(<5y) |
|---|---|---|---|---|
| 保守 | 股30/债50/现15/金5 | 股20/债55/现20/金5 | 股15/债60/现20/金5 | 股10/债60/现25/金5 |
| **平衡（默认）** | 股50/债30/现15/金5 | 股40/债35/现20/金5 | 股35/债40/现20/金5 | 股25/债50/现20/金5 |
| 进取 | 股70/债15/现10/金5 | 股60/债20/现15/金5 | 股50/债25/现20/金5 | 股35/债40/现20/金5 |

### 2.4 年龄微调

```python
# 对股票% 做线性微调，不影响其他
stock_pct = base_stock - max(0, age - 30) * 0.5

# 封顶不低于"保守档"的股票%
stock_pct = max(stock_pct, conservative_row_stock_pct)
```

**例**：45 岁、平衡、有孩 → 基准 35%，微调后 `35 - (45-30)*0.5 = 27.5%`，保守档是 15%，所以最终 27.5%。

---

## 三、偏离度阈值

### 3.1 四档提示

| 偏离度 | 提示级别 | 文案模板 |
|---|---|---|
| < 3% | 正常 | 不提示 |
| 3%–7% | 温和提醒 | "股票仓位略高于目标，可关注" |
| 7%–15% | 明确提醒 | "股票超配 X%，建议再平衡" |
| > 15% | 强烈提醒 | "配置严重偏离，建议立即再平衡" |

### 3.2 用户可调范围

| 参数 | 默认 | 可调下限 | 可调上限 |
|------|------|---------|---------|
| 温和提醒阈值 | 3% | 2% | 5% |
| 明确提醒阈值 | 7% | 5% | 10% |
| 强烈提醒阈值 | 15% | 10% | 20% |

**hard limits**：下限 2%（避免过于敏感）、上限 20%（避免永不触发）。

---

## 四、再平衡周期

- **默认**：**季度检查 + 半年执行**
  - 每季度出报告
  - 偏离度 >7% 才建议动手
- **强制事件触发**（即时重算，不等周期）：
  - 单资产涨跌 >20%
  - 集中度 >40%
  - 单票亏损 >25%

---

## 五、风险画像规则

| 指标 | 阈值 | 触发动作 |
|------|------|---------|
| 单票集中度 | >25% | 分散建议 |
| 单行业集中度 | >40% | 行业敞口警告 |
| Top3 持仓占比 | >60% | 集中度警告 |
| 单票浮亏 | <-25% | 止损复盘 |
| 组合最大回撤(1y) | <-30% | 风险画像降级 |

---

## 六、5 维评分阈值

沿用 `recommend_engine` 现有 5 维（基本面 / 估值 / 技术 / 资金 / 情绪），新增硬门槛：

| 评分 | 分级 | 处置 |
|------|------|------|
| <60 | 淘汰 | **不进候选池** |
| 60–75 | 观察池 | 日常跟踪，不推荐 |
| >75 | 候选池 | 出现在推荐列表 |
| >85 + 估值分位 <30% | 重点关注 | 置顶 + 加"高评分低估值" 标签 |

---

## 七、7 点决策检查清单（详见 07-decision-guard.md）

| # | 检查项 | 红灯标准 |
|---|-------|---------|
| 1 | 应急金是否 ≥ 6 个月 | 不足 |
| 2 | 重疾 / 寿险 / 医疗 / 意外 四大险是否齐全 | 缺 ≥ 1 项 |
| 3 | 加仓后单资产是否 > 25% 或单行业 > 40% | 超 |
| 4 | 这笔钱未来 3 年内是否要用 | 是 |
| 5 | 买入理由是否含"最近涨得好/热搜/朋友推荐" | 是（反向指标）|
| 6 | 距上次调仓是否 < 30 天 | 是 |
| 7 | 调整后与目标配比偏离是否 > 10% | 是 |

**详细的事前/事后双模式设计、买入理由多选交互** → 见 `07-decision-guard.md`。

---

## 八、Graceful Fallback 规则

**原则**：**不猜、不补**。没数据或数据过期 → 对应规则不触发，而不是用旧数据算出错误结论。

| 缺失数据 | 跳过的规则 |
|----------|-----------|
| 月收入/月支出未填 | 应急金月数判断（规则 1）|
| 保险额度过期 >365 天 | 四大险齐全判断（规则 2）|
| 画像未完成 | 目标配比相关所有规则 |
| 房产估值过期 >365 天 | 总资产相关规则用近似估值 + UI 标灰 |

**UI 提示**："应急金判断已跳过（收支数据已过期，请更新）" + 一键跳转更新。

详见 `06-family-profile.md` §12.2.2。

---

## 九、defaults.py 示例

```python
# domain/rule_engine/defaults.py

from dataclasses import dataclass

@dataclass(frozen=True)
class AllocationDefaults:
    # 风险偏好 × 家庭阶段 矩阵（见 §2.3）
    MATRIX = {
        ("conservative", "single"):         (30, 50, 15, 5),
        ("conservative", "married_mortgage"): (20, 55, 20, 5),
        # ... 完整 12 行
        ("balanced", "single"):             (50, 30, 15, 5),
        # ...
    }
    # 偏离度阈值
    DEVIATION_MILD = 0.03
    DEVIATION_MODERATE = 0.07
    DEVIATION_HIGH = 0.15
    # Hard limits
    DEVIATION_MILD_MIN = 0.02
    DEVIATION_HIGH_MAX = 0.20

@dataclass(frozen=True)
class RiskDefaults:
    SINGLE_STOCK_MAX = 0.25
    SINGLE_INDUSTRY_MAX = 0.40
    TOP3_MAX = 0.60
    SINGLE_STOCK_LOSS_TRIGGER = -0.25
    DRAWDOWN_1Y_TRIGGER = -0.30

@dataclass(frozen=True)
class ScoringDefaults:
    CUT_OFF = 60
    WATCH_THRESHOLD = 75
    FOCUS_THRESHOLD = 85
    VALUATION_PCT_FOR_HIGHLIGHT = 0.30

@dataclass(frozen=True)
class RebalanceDefaults:
    CHECK_INTERVAL_DAYS = 90      # 季度检查
    EXECUTE_INTERVAL_DAYS = 180   # 半年执行
    URGENT_MOVE_PCT = 0.20        # 单资产涨跌触发
```

---

## 📎 相关文件

- **家庭画像输入** → `06-family-profile.md`
- **资产负债表字段过期规则** → `06-family-profile.md` §12.2.2
- **7 点清单详细交互** → `07-decision-guard.md`
- **AI 解读规则引擎输出** → `04-ai-interface.md`
