# Batch 5：动态阈值（M8）

> 来源：`14-m7-plus-enhancement-for-claude.md` §二④「动态阈值」+ §二⑤「极端行情保护」
>
> **本文件为独立批次文档，包含开工所需全部信息。不需要翻阅原文。**

---

## 批次概览

| 属性 | 值 |
|---|---|
| 批次编号 | Batch 5 |
| 里程碑 | M8 |
| 产出文件 | `behavior_detector.py`、`behavior_reporter.py` |
| 需读文档 | 本文件 + `07-decision-guard.md`（decision_log 格式）+ `09-advisor-features.md`（M5 复盘归因） |
| 📋 前置依赖 | 无（独立读 decision_log） |
| 可并行 | 与 Batch 3/4 独立并行 |

> **内容对照说明**：
> - 用户要求的文件名聚焦"动态阈值"，但动态阈值的核心计算逻辑（`deviation_thresholds.py`）已在 **Batch 2** 中定义并完成。
> - 本文件对应原 §十 Batch 5「行为归因基础版」的内容——因为行为归因检测是动态阈值的核心**消费方**：检测偏差时需要参考市场波动率背景。
> - 同时本文件补充说明动态阈值如何在行为检测中被集成使用。

---

## 详细设计

### Part A：动态阈值集成使用

Batch 2 已实现 `deviation_thresholds.py`，本批次在行为归因检测中集成动态阈值。

**动态阈值复习（Batch 2 产出）**：

| 波动率区间（沪深300 近20日年化） | 偏离度容忍 |
|---|---|
| <15% | 5% |
| 15%-25% | 7% |
| >25% | 10% |

**与 M3 偏离度体系的关系（需决策 3 结果）**：

- **方案 A（推荐）**：动态阈值作为"前置过滤层"——先判断偏离度是否在容忍范围内；若超出容忍范围，再按 M3 四档分级提醒。高波动率时容忍 10% → M3 的"7%-15% 明确提醒"在此条件下被静默
- **方案 B**：取 max(动态容忍值, M3 当前档位阈值) 作为实际触发阈值

**极端行情保护**（波动率 >30% 时）：
- 非再平衡的主动加仓触发强制二次确认弹窗
- 只提醒，不禁止；不是择时；再平衡不受影响

**集成方式**：行为归因检测在判断"追高"、"FOMO"等偏差时：
1. 调用 `deviation_thresholds.get_tolerance()` 获取当前波动率档位
2. 在行为报告中注明"当时市场波动率处于 X 档"的背景
3. 高波动时适当降低"追高"判定的严格度（RSI 阈值可微调）

---

### Part B：行为归因基础版（检测 + 报告）

#### 1. 问题与目标

**问题**：M5 复盘只知道"买了 6 次，5 次亏了"，不知道"为什么亏"——是追高？是集中？是 FOMO？

**目标**：帮用户看清自己的交易行为模式，从"知道结果"进化到"知道原因"。

#### 2. 与 M5 复盘归因的关系

- M5 归因侧重"你选了什么理由"（主观动机分析）
- M8 归因侧重"你在什么市场条件下操作"（客观市场数据维度）
- **合并策略**：M8 归因结论合并到 M5 已有的月度复盘报告中，新增"交易模式"章节

#### 3. 行为偏差检测规则

| 偏差类型 | 检测规则 | 输出示例 |
|---|---|---|
| 追高倾向 | 买入时点 RSI>70 或近20日涨幅>15% | "你 10 次交易里 6 次发生在 RSI>70 时，其中 4 次 30 天内浮亏" |
| 止损不一致 | 浮亏<5% 卖出次数 > 浮亏>20% 卖出次数 | "你倾向于小亏就卖、大亏装死" |
| 确认偏误 | 80% 以上交易集中在同一行业 | "你 80% 交易集中在科技板块" |
| FOMO 交易 | 大涨日（沪深300单日>2%）交易次数 / 总交易次数 > 0.5 | "你 7 次交易中有 5 次发生在市场大涨当天" |
| 过度交易 | 平均持仓周期<30 天 | "你平均持仓 18 天" |
| 高位加仓 | 加仓时点 PE 历史分位>70% 的次数占比>50% | "你 60% 的加仓发生在估值高位" |
| 锚定效应 | 买入均价作为心理锚点，3 个月未重新评估目标价 | "你对 XX 基金的买入均价作为心理锚点已 3 个月" |

#### 4. 数据需求

- 交易记录（日期、代码、方向、金额、**transaction_type**）— 来自 M3 `decision_log` 或 Batch 1 的 `Transaction`
- **交易类型过滤**：行为偏差检测**只针对 `transaction_type="manual"` 的交易**。定投（`auto_invest`）、再平衡（`rebalance`）、分红（`dividend`）不计入任何偏差统计。CSV 导入的交易默认为 `manual`，用户可在导入确认页修改
- 持仓快照（每次交易后的持仓）
- 市场数据：交易日收盘后的 RSI、PE 分位（非实时数据）
- **动态阈值背景**：调用 Batch 2 的 `deviation_thresholds.get_tolerance()` 获取波动率档位

#### 5. 用户可见产出

季度复盘报告增加"你的交易模式"章节，用具体数据说话，不带情绪、不贴标签。

#### 6. 验收标准

连续 3 个月有交易数据后，系统能生成至少 3 条有数据支撑的行为模式结论。

---

## 接口契约占位

### `BehaviorPattern` 数据结构

```python
@dataclass
class BehaviorPattern:
    """检测到的单个行为偏差"""
    pattern_type: str          # "chasing_high" | "stop_loss_inconsistent" | "confirmation_bias" | "fomo" | "over_trading" | "high_pe_adding" | "anchoring"
    severity: str              # "mild" | "moderate" | "severe"
    evidence_count: int        # 符合该模式的交易次数
    total_relevant: int        # 相关交易总次数
    ratio: float               # evidence_count / total_relevant
    description: str           # 人类可读的结论
    supporting_trades: list[str]  # 支撑证据的交易 ID 列表
    market_context: Optional[str]  # 市场背景（波动率档位、动态阈值容忍度等）
```

### `behavior_detector.py` 核心函数

```python
def detect_patterns(
    transactions: list[Transaction],
    market_data: MarketDataProvider,
    dynamic_threshold: Optional[float] = None,  # 来自 Batch 2 的 deviation_thresholds
    lookback_months: int = 3,
) -> list[BehaviorPattern]:
    """
    检测交易行为偏差模式。

    ⚠️ 前置过滤：只检测 transaction_type="manual" 的交易。
    定投（auto_invest）、再平衡（rebalance）、分红（dividend）不计入偏差统计。
    过滤在本函数入口处执行，各子检测函数收到的都是已过滤的列表。

    参数:
        transactions: 交易记录列表（含所有类型，函数内部过滤）
        market_data: 市场数据提供者（RSI、PE 分位、沪深300涨跌幅）
        dynamic_threshold: 当前动态阈值容忍度（来自 Batch 2）
        lookback_months: 回溯月数
    返回:
        BehaviorPattern 列表
    """

def _filter_manual_only(transactions: list[Transaction]) -> list[Transaction]:
    """过滤出 transaction_type='manual' 的交易，供偏差检测使用。"""

def detect_chasing_high(transactions, market_data, volatility_tier) -> Optional[BehaviorPattern]: ...
def detect_stop_loss_inconsistency(transactions) -> Optional[BehaviorPattern]: ...
def detect_confirmation_bias(transactions) -> Optional[BehaviorPattern]: ...
def detect_fomo(transactions, market_data) -> Optional[BehaviorPattern]: ...
def detect_over_trading(transactions) -> Optional[BehaviorPattern]: ...
def detect_high_pe_adding(transactions, market_data) -> Optional[BehaviorPattern]: ...
def detect_anchoring(transactions, market_data) -> Optional[BehaviorPattern]: ...
```

### `behavior_reporter.py` 核心函数

```python
def generate_quarterly_report(
    patterns: list[BehaviorPattern],
    user_id: str,
    quarter: str,
) -> BehaviorReport:
    """
    生成季度行为模式报告。
    返回: BehaviorReport（含 Markdown 正文，可直接合并到 M5 月度复盘）
    """

@dataclass
class BehaviorReport:
    user_id: str
    quarter: str
    patterns_found: int
    report_markdown: str
    volatility_context: str    # 季度波动率背景描述
    generated_at: datetime
```

---

## 文件落位

| 文件路径 | 职责 | 预估行数 |
|---|---|---|
| `domain/services/behavior_detector.py` | 7 种偏差检测逻辑 + 动态阈值集成 | <200 |
| `domain/services/behavior_reporter.py` | 报告生成（Markdown 格式） | <200 |

---

## 🔗 跨批次耦合

| 被哪个批次引用 | 引用内容 | 说明 |
|---|---|---|
| Batch 6（行为归因检测/联动版） | `detect_patterns()` + `BehaviorPattern` | 联动版基于检测结果触发执行干预 |
| Batch 8（行为干预联动） | `BehaviorPattern` | 干预规则引用偏差类型和严重程度 |

本批次引用了：
| 引用哪个批次 | 引用内容 |
|---|---|
| Batch 2 | `deviation_thresholds.get_tolerance()` — 获取波动率档位作为检测背景 |
| Batch 1（可选） | `Transaction` 数据结构 — 作为交易记录的可选来源 |

---

## 📋 前置依赖

无强依赖。本批次独立读取 `decision_log`。

可选增强：
- 若 Batch 2 已完成 → 可集成动态阈值背景
- 若 Batch 1 已完成 → 可额外使用 CSV 导入的交易记录

---

## 🚫 禁止假设

1. **不能假设 Batch 2 已完成**——动态阈值集成为可选增强，核心检测逻辑不依赖
2. **不能假设有实时市场数据**——RSI、PE 分位为收盘后数据
3. **不能输出投资建议**——只描述行为模式，不说"你应该怎么做"
4. **不能新增执行干预**——执行干预归 Batch 6/8
5. **不能假设 ≥3 个月交易数据已存在**——不足时返回空报告
6. **不能修改 Batch 2 的 `deviation_thresholds.py`**——只调用其接口

---

## ⚙️ 全局契约引用

- **defaults.py 新增**：本批次的 `BehaviorDefaults` 需遵循命名规范 → 详见 [00-interface-contracts.md](00-interface-contracts.md) §二
- **动态阈值接口**：调用 Batch 2 的 `get_tolerance()` / `apply_dynamic_filter()` → 签名详见 [00-interface-contracts.md](00-interface-contracts.md) §四
- **冲突处理**：实现过程中遇到需要修改 M1-M6 文件、接口不够用等情况时 → **停止编码，按 [README.md](README.md) 冲突处理 SOP 记录冲突，等人工确认**
