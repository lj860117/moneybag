# Batch 4：行业偏离度（M8）

> 来源：`14-m7-plus-enhancement-for-claude.md` §二③「行业偏离检测」+ §三中行业相关验证
>
> **本文件为独立批次文档，包含开工所需全部信息。不需要翻阅原文。**

---

## 批次概览

| 属性 | 值 |
|---|---|
| 批次编号 | Batch 4 |
| 里程碑 | M8 |
| 产出文件 | `fund_filter_validation.py`（含行业偏离验证逻辑） |
| 需读文档 | 本文件 + Batch 3 产出的 `fund_filter_rules.py` + §九检查表（决策 2 结果） |
| 📋 前置依赖 | Batch 3（10 维评分）必须完成；Batch 2（决策 2 确定行业阈值） |
| 可并行 | 不可，强依赖 Batch 3 |

> **对应原 §十**：原 Batch 4 名为"10 维验证"。本文件聚焦行业偏离度，同时包含 10 维筛选的整体验证流程。

---

## 详细设计

### 1. 行业偏离检测规则

| 检测条件 | 触发级别 | 说明 |
|---|---|---|
| 单一行业占比 >25% | 🟡 标黄 | ⚠️ 覆盖 M3 的 40% 阈值（取决于决策 2） |
| 单一行业占比 >35% | 🔴 标红 | 严重集中 |
| 前三大行业占比 >70% | 🟡 标黄 | 组合集中度 |

**数据来源**：基金持仓行业分布采用天天基金行业分类；数据更新频率为季度（基金季报）。

**验收标准调整**：偏离度报告在季报更新后 24h 内生成（而非任意时点 24h）。

### 2. 与 M3 阈值的关系（需决策 2 结果）

M3 `03-rule-engine.md` §五 定义单行业集中度 >40% 触发"行业敞口警告"；07-decision-guard.md §二第 3 条定义加仓后单行业 >40% 红灯。M7+ 将阈值收紧为 25%/35%，与 M3/M5 存在不一致。

**三选一方案（§九 决策 2）**：

- **方案 A（推荐）**：区分层级——M3/07 的 40% 用于"风险画像/决策清单"层级（宽松底线），M7+ 的 25%/35% 用于"精细配置偏离"层级（严格监控），UI 上分开展示
  - 用户行业占比 30%：M3 显示正常，M7+ 显示标黄——这是预期行为，代表"未触及底线但已偏离理想状态"
- **方案 B**：统一调整——把 M3/07 的 40% 改为 35%，M7+ 的 25% 标黄作为"早期预警"。需改 M3 和 07 的阈值定义
- **方案 C**：M7+ 放弃独立阈值，沿用 M3 的 40%，仅在 UI 上增加"接近阈值"提示（如 35%-40% 标黄）

### 3. 10 维筛选规则逻辑验证流程

10 维筛选上线前，先用 VectorBT / Pandas 在历史数据上跑一遍验证：

1. 随机抽取 12 个月的历史数据，每月跑一遍 10 维筛选
2. 统计以下指标：
   - 红灯触发率（各维度，特别关注行业偏离维度）
   - 黄灯触发率
   - 复核池占比（进复核池的标的 / 总输入标的）
   - 一票否决率
   - 通过率
3. **行业偏离专项统计**：
   - 各行业命中 25%/35% 阈值的频次
   - 前三大行业占比分布
   - 方案 A/B/C 下的差异对比

**验收标准**：

| 指标 | 合格范围 | 不合格处理 |
|---|---|---|
| 复核池占比 | 10%-30% | 若偏高（>30%）：黄灯阈值过严；若偏低（<10%）：黄灯阈值过松 |
| 红灯排除率 | <80% | 若 >80%：红灯阈值过严 |
| 单维度红灯集中度 | 无单一维度贡献 >60% 红灯 | 该维度阈值不合理 |
| 行业偏离覆盖率 | 能检测到 ≥3 种行业偏离场景 | 行业分类可能有遗漏 |
| 10 维筛选后候选池任意二级分类占比 | ≤40% | 防同质化规则未生效 |

> 此验证只确认"规则逻辑是否正确"，**不验证策略收益**——MoneyBag 不做收益回测。

---

## 接口契约占位

### `IndustryDeviationChecker` 接口

```python
def check_industry_deviation(
    holdings: list[HoldingItem],
    decision_2_result: str,  # "A" | "B" | "C"
) -> list[IndustryDeviationAlert]:
    """
    检测行业偏离度。
    参数:
        holdings: 用户持仓列表（含行业分类）
        decision_2_result: §九 决策 2 的方案选择
    返回:
        IndustryDeviationAlert 列表
    """

@dataclass
class IndustryDeviationAlert:
    """行业偏离告警"""
    industry: str              # 行业名称
    actual_pct: float          # 实际占比
    threshold: float           # 触发阈值
    level: str                 # "yellow" | "red"
    m3_level: Optional[str]    # M3 层级的判定（方案 A 时使用）
    message: str               # 人类可读提示
```

### `fund_filter_validation.py` 核心函数

```python
def run_historical_validation(
    months: int = 12,
    data_source: DataSourceProtocol = None,
) -> ValidationReport:
    """在历史数据上跑 10 维筛选 + 行业偏离验证。"""

@dataclass
class ValidationReport:
    """验证报告"""
    total_months: int
    monthly_results: list[MonthlyResult]
    avg_red_rate: float
    avg_yellow_rate: float
    avg_review_pool_pct: float
    avg_pass_rate: float
    dimension_red_distribution: dict[str, float]
    industry_deviation_stats: IndustryDeviationStats
    is_qualified: bool
    issues: list[str]

@dataclass
class IndustryDeviationStats:
    """行业偏离专项统计"""
    industries_hit_25: dict[str, int]    # 各行业命中 25% 阈值的次数
    industries_hit_35: dict[str, int]    # 各行业命中 35% 阈值的次数
    top3_concentration_avg: float        # 前三大行业平均集中度
    scenarios_detected: int              # 检测到的偏离场景数
```

---

## 文件落位

| 文件路径 | 职责 | 预估行数 |
|---|---|---|
| `tests/validation/fund_filter_validation.py` | 规则逻辑验证（含行业偏离专项） | <200 |

---

## 🔗 跨批次耦合

| 被哪个批次引用 | 引用内容 | 说明 |
|---|---|---|
| 无 | — | 验证脚本为终端消费者 |

本批次引用了：
| 引用哪个批次 | 引用内容 |
|---|---|
| Batch 3 | `run_10d_filter()` 函数 + `FundFilterResult` |
| Batch 2 | 行业偏离阈值方案（决策 2） |

---

## 📋 前置依赖

| 依赖批次 | 依赖内容 | 是否阻塞 |
|---|---|---|
| Batch 3（10 维评分） | `fund_filter_rules.py` 全部函数 | 🔴 阻塞 |
| §九 决策 2 | 行业集中度阈值方案 | 🔴 阻塞 |

---

## 🚫 禁止假设

1. **不能假设 Batch 3 的 `fund_filter_rules.py` 已存在**——必须等 Batch 3 完成
2. **不能假设决策 2 的结果**——行业偏离阈值（25%/35% vs 40%）需从检查表读取
3. **不能做收益回测**——只验证规则逻辑
4. **不能修改 Batch 3 的筛选阈值**——发现问题输出报告，由人工调整
5. **不能假设历史数据完整**——某些月份可能缺失，验证需容错

---

## ⚙️ 全局契约引用

- **defaults.py 新增**：本批次的 `IndustryDeviationDefaults` 需遵循命名规范 → 详见 [00-interface-contracts.md](00-interface-contracts.md) §二
- **冲突处理**：实现过程中遇到行业阈值与 M3 现有代码不一致等情况时 → **停止编码，按 [README.md](README.md) 冲突处理 SOP 记录冲突，等人工确认**
