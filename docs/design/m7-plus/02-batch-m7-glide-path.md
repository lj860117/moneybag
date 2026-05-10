# Batch 2：Glide Path + 四档年龄下滑（M7）

> 来源：`14-m7-plus-enhancement-for-claude.md` §二「动态 Glide Path」中的 ①年龄 Glide Path 部分
>
> **本文件为独立批次文档，包含开工所需全部信息。不需要翻阅原文。**

---

## 批次概览

| 属性 | 值 |
|---|---|
| 批次编号 | Batch 2 |
| 里程碑 | M7 |
| 产出文件 | `glide_path_rules.py`、`deviation_thresholds.py` |
| 需读文档 | 本文件 + `03-rule-engine.md`（现有 allocation_rules.py）+ §九检查表（决策 1/2/3 结果） |
| 📋 前置依赖 | 无（与 Batch 1 独立） |
| 可并行 | 与 Batch 1（外部数据同步）独立并行 |

---

## 详细设计

### 1. 问题与目标

**问题**：M2 的固定配比矩阵（股/债/现金/黄金）太粗，不监控风格、市值、行业层面的偏离。

**目标**：从"大类配置纪律"进化到"全维度配置纪律"。

### 2. 与 M3 的关系（⚠️ 重要 — 需决策 1 结果）

M3 `03-rule-engine.md` §2.4 已有基于风险偏好×家庭阶段矩阵的年龄微调公式（`stock_pct = base_stock - max(0, age - 30) * 0.5`）。

M7+ Glide Path **替换**该矩阵+微调体系，改用 `100-age` 一刀切公式。

**三选一方案（§九 决策 1，M7 开工前必须确定）**：

- **方案 A（推荐）**：Glide Path 作为默认，M3 矩阵保留为"高级自定义模式"（用户可切换）。M7 上线时 `allocation_rules.py` 的年龄相关逻辑迁移到 `glide_path_rules.py`
  - Tradeoff：Glide Path 只用年龄一个维度，M3 矩阵用年龄 × 风险偏好 × 家庭阶段三个维度。选方案 A 意味着默认模式下丢失了"风险偏好"和"家庭阶段"两个个性化维度——有意为之，因为"降低维护成本"优先级高于"个性化精度"
- **方案 B**：Glide Path 仅作为"参考线"展示，实际配比仍由 M3 矩阵决定（不改 allocation_rules.py，价值大打折扣）
- **方案 C**：废弃 M3 矩阵，完全用 Glide Path（简单直接，但失去风险偏好个性化能力）

**冲突标注**：⚠️ 本方向会修改 `allocation_rules.py` 的配比逻辑——这是 M7+ 唯一需要改已有文件的例外

### 3. 年龄 Glide Path 公式（唯一标准）

```python
# equity_plus_gold_pct: 股票+黄金合计目标占比
equity_plus_gold_pct = max(100 - age, 20)  # 向下取整到 5% 倍数
# 黄金固定 5%，从合计中划出
gold_pct = 5
stock_pct = equity_plus_gold_pct - gold_pct
```

> **命名说明**：公式计算的是 **股票+黄金的合计占比**（`equity_plus_gold_pct`），不是纯股票。

**参考表格（公式近似值）**：

| 年龄段 | 公式计算值 | 默认股票目标 | 默认债券目标 | 默认现金目标 | 默认黄金目标 | 用户可覆盖范围 |
|---|---|---|---|---|---|---|
| 25 岁 | 75% → 70% | 70% | 15% | 10% | 5% | ±10% |
| 30 岁 | 70% → 65% | 65% | 20% | 10% | 5% | ±10% |
| 35 岁 | 65% → 60% | 60% | 25% | 10% | 5% | ±10% |
| 40 岁 | 60% → 55% | 55% | 30% | 10% | 5% | ±10% |
| 45 岁 | 55% → 50% | 50% | 30% | 15% | 5% | ±10% |
| 50 岁 | 50% → 45% | 45% | 35% | 15% | 5% | ±10% |
| 55 岁 | 45% → 40% | 40% | 40% | 15% | 5% | ±10% |
| 60 岁 | 40% → 35% | 35% | 45% | 15% | 5% | ±10% |
| 65 岁 | 35% → 30% | 30% | 45% | 20% | 5% | ±10% |

**规则**：
- **年龄取值**：向下取整到最近的 5 岁档位查表（如 27 岁按 25 岁档位、33 岁按 30 岁档位）。不采用线性插值。
- **黄金配置**：默认固定占比 **5%**，不受年龄下滑影响，用户可在 0-10% 范围内覆盖
- **债券与现金分配**：无独立公式，由查表确定。设计原则：股票下降部分优先分配给债券，现金仅做流动性补充
- 用户可在默认 ±10% 范围内覆盖，超出范围需二次确认
- **家庭年龄取值**：若家庭有多个成员，取**主要决策者年龄**（默认户主，可配置）

### 4. 风格偏离检测

- 价值/成长偏离：目标价值 50%，实际 <40% 或 >60% → 标黄
- 大盘/小盘偏离：目标大盘 70%，实际 <55% 或 >85% → 标黄
- **目标值来源**：50%/70% 为**硬编码默认值**，放在 `glide_path_rules.py` 文件顶部常量区，用户可在配置页覆盖
- **数据来源**：基金持仓风格分类采用天天基金/晨星分类体系；数据更新频率为季度

### 5. 动态阈值（`deviation_thresholds.py`）

市场波动率 proxy（用沪深 300 近 20 日年化波动率）：

| 波动率区间 | 偏离度容忍 |
|---|---|
| <15% | 5% |
| 15%-25% | 7% |
| >25% | 10% |

**与 M3 偏离度体系的关系（需决策 3 结果）**：

M3 定义四档提示（3%-7% 温和提醒 / 7%-15% 明确提醒 / >15% 强烈提醒）。

- **方案 A（推荐）**：动态阈值作为"前置过滤层"——先判断偏离度是否在容忍范围内；若超出容忍范围，再按 M3 四档分级提醒
- **方案 B**：取 max(动态容忍值, M3 当前档位阈值) 作为实际触发阈值

### 6. 极端行情保护（高波动二次确认）

- 全市场波动率 >30% 时：非再平衡的主动加仓触发强制二次确认弹窗
- **只提醒，不禁止**：用户确认后可正常操作，再平衡不受影响
- **不是择时**：不判断涨跌方向、不限制操作方向

### 7. 用户可见产出

资产配置页从"4 个饼图"升级为"大类 + 风格 + 市值 + 行业"多层雷达图；偏离项按严重程度标黄/红。

### 8. 验收标准

用户持仓触发 3 类偏离时，系统能在 24h 内生成偏离度报告。

### 9. 依赖 M1-M6

M2 配比矩阵 + M3 规则引擎阈值体系。

---

## 接口契约占位

### `AllocationTarget` 数据结构

```python
@dataclass
class AllocationTarget:
    """Glide Path 计算出的目标配比"""
    stock_pct: float       # 股票目标占比（已扣除黄金）
    bond_pct: float        # 债券目标占比
    cash_pct: float        # 现金目标占比
    gold_pct: float        # 黄金目标占比（默认 5%）
    age_bucket: int        # 使用的年龄档位（5 的倍数）
    user_override: bool    # 是否有用户覆盖
```

### `glide_path_rules.py` 核心函数

```python
def calculate_target_allocation(age: int, user_overrides: Optional[dict] = None) -> AllocationTarget:
    """
    根据年龄计算目标资产配比。
    参数:
        age: 主要决策者年龄
        user_overrides: 用户自定义覆盖 {"stock_pct": 0.6, ...}，±10% 范围内
    返回:
        AllocationTarget
    异常:
        OverrideOutOfRangeError — 覆盖超出 ±10% 范围（需二次确认）
    """

def check_style_deviation(
    actual_value_pct: float,
    actual_large_cap_pct: float,
    target_value_pct: float = 0.5,
    target_large_cap_pct: float = 0.7,
) -> list[DeviationAlert]:
    """
    检测风格偏离。
    返回: DeviationAlert 列表（level="yellow"/"red", dimension, actual, target, threshold）
    """
```

### `deviation_thresholds.py` 核心函数

```python
def get_tolerance(volatility: float) -> float:
    """
    根据市场波动率返回偏离度容忍值。
    参数:
        volatility: 沪深 300 近 20 日年化波动率
    返回:
        float — 偏离度容忍阈值（如 0.05, 0.07, 0.10）
    """

def should_trigger_extreme_confirmation(volatility: float) -> bool:
    """
    判断是否需要极端行情二次确认（波动率 >30%）。
    """

def apply_dynamic_filter(
    deviation: float,
    volatility: float,
    m3_thresholds: dict,
) -> Optional[str]:
    """
    动态阈值前置过滤 + M3 四档分级。
    参数:
        deviation: 实际偏离度
        volatility: 当前波动率
        m3_thresholds: M3 四档阈值配置
    返回:
        None（在容忍范围内）| "mild" | "clear" | "strong"（M3 提醒级别）
    """
```

---

## 文件落位

| 文件路径 | 职责 | 预估行数 |
|---|---|---|
| `domain/rule_engine/glide_path_rules.py` | 年龄下滑 + 偏离度计算 + 风格偏离检测 | <250 |
| `domain/rule_engine/deviation_thresholds.py` | 动态阈值（波动率分档）+ 极端行情保护 | <100 |

---

## 🔗 跨批次耦合

| 被哪个批次引用 | 引用内容 | 说明 |
|---|---|---|
| Batch 3（10 维评分） | `AllocationTarget` 偏离度逻辑 | 10 维筛选中的行业偏离检测可能引用 Glide Path 的目标配比 |
| Batch 4（行业偏离度验证） | `deviation_thresholds.get_tolerance()` | 验证脚本需要调用动态阈值来校验容忍度 |
| Batch 5（动态阈值） | `deviation_thresholds.py` 整体 | 动态阈值在偏离度报告生成时被调用 |

---

## 📋 前置依赖

无。但开工前需确认 §九 检查表中：
- **决策 1**：Glide Path 与 M3 矩阵的关系（A/B/C）
- **决策 2**：行业集中度阈值与 M3/07 的关系（A/B/C）
- **决策 3**：动态阈值与 M3 四档提示的关系（A/B）

三个决策未填写 → 本批次不得开工。

---

## 🚫 禁止假设

1. **不能假设决策 1/2/3 的结果**——必须从 §九 检查表读取确认值
2. **不能假设 M3 矩阵已废弃**——即使选方案 A，M3 矩阵仍保留为高级模式
3. **不能在 `glide_path_rules.py` 里直接 import `allocation_rules.py`**——通过 Protocol 接口解耦
4. **不能假设风格分类数据已有**——天天基金/晨星分类为季度更新，首次运行可能无数据
5. **不能修改 M1-M6 已有文件**——`defaults.py` 仅可新增 `GlidePathDefaults` dataclass

---

## ⚙️ 全局契约引用

- **defaults.py 新增**：本批次的 `GlidePathDefaults` + `DeviationThresholdDefaults` 需遵循命名规范 → 详见 [00-interface-contracts.md](00-interface-contracts.md) §二
- **冲突处理**：实现过程中遇到需要修改 M1-M6 文件（特别是 `allocation_rules.py`）等情况时 → **停止编码，按 [README.md](README.md) 冲突处理 SOP 记录冲突，等人工确认**
