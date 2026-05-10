# 00 — 全局接口契约（跨批次共享）

> 本文件定义所有批次共享的接口契约。各批次文件中的接口契约为**本文件的子集或扩展**，不得与本文件冲突。
>
> **优先级**：本文件 > 各批次文件中的接口占位。若有出入，以本文件为准。

---

## 一、凌晨工厂统一接入协议

### 1.1 背景

M5 `05-scheduling.md` 的凌晨工厂为 3 个固定阶段（数据采集 → 规则引擎 → LLM 翻译）。M7+ 的 2 个批次需要接入：

| 接入方 | 挂载位置 | 说明 |
|---|---|---|
| Batch 1（外部数据同步） | 阶段 1（数据采集）内部 | 与 `tushare_data` 并列，作为新的数据来源步骤 |
| Batch 7（事件解读） | 阶段 2（规则引擎）与阶段 3（LLM 翻译）之间 | 规则引擎输出后、LLM 翻译前的内容生成步骤 |

### 1.2 统一接口：`NightWorkerStep`

所有接入凌晨工厂的步骤**必须**实现以下 Protocol：

```python
from typing import Protocol, Optional
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class StepStatus(Enum):
    SUCCESS = "success"
    PARTIAL = "partial"       # 部分成功（如 5 条记录中 3 条成功）
    SKIPPED = "skipped"       # 主动跳过（如无新数据）
    FAILED = "failed"         # 失败


@dataclass
class PipelineContext:
    """凌晨工厂流水线上下文，阶段间传递"""
    run_id: str                          # 本次运行唯一 ID（如 "nw-20260428-0200"）
    run_date: datetime                   # 本次运行日期
    user_id: str                         # 用户 ID
    stage_outputs: dict[str, any]        # 前序阶段/步骤的输出，key = step_name
    config: dict                         # 全局配置（从 defaults.py 读取）
    dry_run: bool = False                # True 时不写入持久化，仅验证


@dataclass
class StepResult:
    """单个步骤的执行结果"""
    step_name: str                       # 步骤唯一标识（如 "broker_sync", "event_interpret"）
    status: StepStatus
    records_processed: int = 0           # 处理的记录数
    records_succeeded: int = 0           # 成功的记录数
    records_failed: int = 0              # 失败的记录数
    duration_seconds: float = 0.0        # 执行耗时
    error_message: Optional[str] = None  # 失败时的错误信息
    output: Optional[dict] = None        # 输出数据（写入 ctx.stage_outputs 供后续步骤读取）


class NightWorkerStep(Protocol):
    """
    凌晨工厂步骤统一协议。

    所有接入凌晨工厂的 M7+ 步骤必须实现此接口。

    实现约束：
    1. execute() 必须是幂等的——重复运行同一日期不应产生重复数据
    2. execute() 内部必须自行 try-catch，不允许向外抛出未处理异常
    3. 失败时返回 StepResult(status=FAILED)，不中断同阶段其他步骤
    4. 超时由调用方控制（默认 300s），步骤内部不自行设置超时
    """

    @property
    def step_name(self) -> str:
        """步骤唯一标识，用于日志和 stage_outputs 的 key"""
        ...

    @property
    def target_stage(self) -> int:
        """
        挂载到哪个阶段（1/2/3）。

        阶段定义：
          1 = 数据采集（tushare_data, broker_sync, ...）
          2 = 规则引擎（scoring, deviation_check, ...）
          3 = LLM 翻译（report_generation, ...）

        特殊值：
          2.5 = 阶段 2 和阶段 3 之间（事件解读的挂载点）

        返回 int 或 float。
        """
        ...

    def execute(self, ctx: PipelineContext) -> StepResult:
        """
        执行步骤。

        参数:
            ctx: 流水线上下文（含前序步骤输出、全局配置）
        返回:
            StepResult — 执行结果
        约束:
            - 必须幂等
            - 必须自行 try-catch
            - 超时由调用方控制
        """
        ...

    def should_run(self, ctx: PipelineContext) -> bool:
        """
        预检查：本次是否需要运行。

        用于跳过无意义的执行（如 broker_sync 在无新 CSV 文件时跳过）。
        默认返回 True。
        """
        ...
```

### 1.3 各批次的实现映射

| 步骤 | step_name | target_stage | 实现文件 | 归属批次 |
|---|---|---|---|---|
| 券商流水同步 | `"broker_sync"` | 1 | `use_cases/sync_broker_statement.py` | Batch 1 |
| 事件解读 | `"event_interpret"` | 2.5 | `infra/knowledge/events/event_matcher.py` | Batch 7 |

### 1.4 注册方式

```python
# night_worker_pipeline.py 中的注册（伪代码，实际取决于 §九 验证 2 结果）

# 方式 A：阶段内步骤列表扩展（推荐，需验证 2 支持）
STAGE_1_STEPS: list[NightWorkerStep] = [
    TushareDataStep(),       # M1-M6 已有
    BrokerSyncStep(),        # M7+ Batch 1 新增
]

STAGE_2_5_STEPS: list[NightWorkerStep] = [
    EventInterpretStep(),    # M7+ Batch 7 新增
]

# 方式 B：硬编码顺序调用（降级方案，验证 2 不支持时）
# 在 stage_1() 末尾追加 BrokerSyncStep().execute(ctx)
# 在 stage_2() 和 stage_3() 之间插入 EventInterpretStep().execute(ctx)
```

### 1.5 错误隔离保证

```python
# 阶段内步骤执行器（推荐实现）
def run_stage_steps(steps: list[NightWorkerStep], ctx: PipelineContext) -> list[StepResult]:
    """
    依次执行阶段内所有步骤。
    单步骤失败记录日志并跳过，不阻塞同阶段其他步骤。
    """
    results = []
    for step in steps:
        if not step.should_run(ctx):
            results.append(StepResult(step_name=step.step_name, status=StepStatus.SKIPPED))
            continue
        result = step.execute(ctx)  # step 内部已 try-catch
        ctx.stage_outputs[step.step_name] = result.output
        results.append(result)
        logger.info(f"[{ctx.run_id}] {step.step_name}: {result.status.value} "
                     f"({result.records_succeeded}/{result.records_processed} records, "
                     f"{result.duration_seconds:.1f}s)")
    return results
```

---

## 二、`defaults.py` 新增 dataclass 规范

### 2.1 背景

原文 §七 3.1 承诺：`defaults.py` 可**新增** M7+ 所需常量，但**不修改/删除** M1-M6 已有的 dataclass。新增常量以独立 dataclass 形式放在文件末尾，标注 `# M7+ 新增`。

多个批次并行开发时可能同时向 `defaults.py` 添加 dataclass，需要统一规范避免命名冲突和合并冲突。

### 2.2 命名规范

```python
# ══════════════════════════════════════════════════════════
# M7+ 新增常量（按批次顺序排列，不修改上方 M1-M6 已有常量）
# ══════════════════════════════════════════════════════════

# --- Batch 1: 外部数据同步 ---
@dataclass(frozen=True)
class ExternalSyncDefaults:
    """M7+ Batch 1: 外部数据同步阈值"""
    AUTO_SYNC_EXPIRE_DAYS: int = 90       # 自动同步数据过期天数
    MANUAL_SYNC_EXPIRE_DAYS: int = 30     # 手动数据过期天数

# --- Batch 2: Glide Path ---
@dataclass(frozen=True)
class GlidePathDefaults:
    """M7+ Batch 2: 年龄下滑 + 偏离度"""
    GOLD_PCT: float = 0.05                # 黄金固定占比
    USER_OVERRIDE_RANGE: float = 0.10     # 用户可覆盖范围 ±10%
    STYLE_VALUE_TARGET: float = 0.50      # 价值风格目标占比
    STYLE_LARGE_CAP_TARGET: float = 0.70  # 大盘风格目标占比
    EXTREME_VOLATILITY_THRESHOLD: float = 0.30  # 极端行情阈值

# --- Batch 2: 动态阈值 ---
@dataclass(frozen=True)
class DeviationThresholdDefaults:
    """M7+ Batch 2: 波动率分档"""
    LOW_VOL_CEILING: float = 0.15         # 低波动率上界
    MID_VOL_CEILING: float = 0.25         # 中波动率上界
    LOW_VOL_TOLERANCE: float = 0.05       # 低波动率偏离容忍
    MID_VOL_TOLERANCE: float = 0.07       # 中波动率偏离容忍
    HIGH_VOL_TOLERANCE: float = 0.10      # 高波动率偏离容忍

# --- Batch 3: 10 维筛选 ---
@dataclass(frozen=True)
class FundFilterDefaults:
    """M7+ Batch 3: 10 维筛选阈值"""
    FEE_RED: float = 0.01                 # 管理费率红灯
    FEE_YELLOW: float = 0.005             # 管理费率黄灯
    SCALE_RED: int = 50_000_000           # 规模红灯（5000万）
    SCALE_YELLOW: int = 200_000_000       # 规模黄灯（2亿）
    # ... 其余 10 维阈值

# --- Batch 4: 行业偏离度 ---
@dataclass(frozen=True)
class IndustryDeviationDefaults:
    """M7+ Batch 4: 行业偏离阈值"""
    SINGLE_INDUSTRY_YELLOW: float = 0.25  # 单行业占比黄灯
    SINGLE_INDUSTRY_RED: float = 0.35     # 单行业占比红灯
    TOP3_INDUSTRY_YELLOW: float = 0.70    # 前三大行业占比黄灯

# --- Batch 5: 行为归因 ---
@dataclass(frozen=True)
class BehaviorDefaults:
    """M7+ Batch 5: 行为偏差检测阈值"""
    CHASING_RSI_THRESHOLD: float = 70.0   # 追高 RSI 阈值
    CHASING_GAIN_THRESHOLD: float = 0.15  # 追高近 20 日涨幅阈值
    FOMO_MARKET_GAIN: float = 0.02        # FOMO 大涨日阈值（沪深300 单日>2%）
    OVER_TRADING_HOLDING_DAYS: int = 30   # 过度交易持仓周期阈值
    OVER_TRADING_MONTHLY_COUNT: int = 5   # 过度交易月交易次数阈值
    HIGH_PE_PERCENTILE: float = 0.70      # 高位加仓 PE 分位阈值
    ANCHORING_MONTHS: int = 3             # 锚定效应月数阈值

# --- Batch 6: 行为干预 ---
@dataclass(frozen=True)
class BehaviorInterventionDefaults:
    """M7+ Batch 6: 行为干预参数"""
    BEHAVIOR_GUARD_ENABLED: bool = True   # 全局紧急开关（关闭后所有干预降级为纯报告）
    COOLDOWN_HOURS: int = 24              # 冷静期时长
    POSITION_LIMIT_REDUCTION: float = 0.20  # 仓位上限下调幅度
    FOMO_AMOUNT_CAP_PCT: float = 0.05     # FOMO 金额锁死比例
```

### 2.3 规则

1. **命名格式**：`{功能域}Defaults`（PascalCase），如 `GlidePathDefaults`、`BehaviorDefaults`
2. **注释标记**：每个 dataclass 上方用 `# --- Batch N: 简述 ---` 分隔
3. **docstring**：必须注明 `M7+ Batch N: 用途`
4. **排列顺序**：按批次编号从小到大排列
5. **frozen=True**：所有 defaults dataclass 必须为 frozen（不可变）
6. **不修改已有**：M1-M6 的 `AllocationDefaults` / `RiskDefaults` / `ScoringDefaults` / `RebalanceDefaults` 保持不动
7. **合并策略**：若两个批次并行开发（如 Batch 1 + Batch 2），各自在本地 branch 添加自己的 dataclass，merge 时按批次编号排序即可——不同批次的 dataclass 名称不会冲突（各有独立的功能域前缀）

---

## 三、跨批次数据流总图

```
                 ┌──────────────────────────────────────────────┐
                 │              defaults.py                      │
                 │  ┌─────────────────┐ ┌──────────────────┐    │
                 │  │ M1-M6 Defaults  │ │ M7+ Defaults     │    │
                 │  │ (不修改)         │ │ (按批次新增)      │    │
                 │  └─────────────────┘ └──────────────────┘    │
                 └──────────────────┬───────────────────────────┘
                                    │ 读取阈值
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
            ┌───────────┐   ┌───────────┐   ┌───────────┐
            │ Batch 1   │   │ Batch 2   │   │ Batch 5   │
            │ broker_   │   │ glide_    │   │ behavior_ │
            │ parser    │   │ path +    │   │ detector  │
            │           │   │ deviation │   │           │
            └─────┬─────┘   └─────┬─────┘   └─────┬─────┘
                  │ Transaction    │ AllocationTarget  │ BehaviorPattern
                  │               │ get_tolerance()   │
                  ▼               ▼                   ▼
            ┌───────────┐   ┌───────────┐   ┌───────────┐
            │ Batch 5   │   │ Batch 3   │   │ Batch 6   │
            │ (读交易   │   │ fund_     │   │ behavior_ │
            │  记录)    │   │ filter    │   │ interven- │
            └───────────┘   └─────┬─────┘   │ tion      │
                                  │          └───────────┘
                                  │ FundFilterResult
                                  ▼
                            ┌───────────┐
                            │ Batch 4   │
                            │ validation│
                            └───────────┘

            ┌───────────────────────────────────┐
            │        凌晨工厂 pipeline           │
            │                                   │
            │  阶段 1: [tushare] [broker_sync]  │ ← Batch 1: NightWorkerStep
            │  阶段 2: [scoring] [deviation]    │
            │  阶段 2.5: [event_interpret]      │ ← Batch 7: NightWorkerStep
            │  阶段 3: [llm_translate]          │
            └───────────────────────────────────┘
```

---

## 四、接口契约完整索引

| 接口/数据 | 定义文件 | 定义方 | 消费方 |
|---|---|---|---|
| `NightWorkerStep` Protocol | **本文件 §一** | 全局 | Batch 1, Batch 7 |
| `PipelineContext` | **本文件 §一** | 全局 | 所有凌晨工厂步骤 |
| `StepResult` | **本文件 §一** | 全局 | 所有凌晨工厂步骤 |
| `*Defaults` dataclass 规范 | **本文件 §二** | 全局 | 所有批次 |
| `Transaction` | 01-batch | Batch 1 | Batch 5, Batch 6 |
| `BaseBrokerParser` | 01-batch | Batch 1 | 后续券商 parser |
| `parse_broker_csv()` | 01-batch | Batch 1 | Batch 5 |
| `AllocationTarget` | 02-batch | Batch 2 | Batch 3, Batch 4, Batch 5 |
| `calculate_target_allocation()` | 02-batch | Batch 2 | Batch 3, Batch 4 |
| `get_tolerance()` | 02-batch | Batch 2 | Batch 3, Batch 5, Batch 6 |
| `should_trigger_extreme_confirmation()` | 02-batch | Batch 2 | Batch 6 |
| `apply_dynamic_filter()` | 02-batch | Batch 2 | Batch 5 |
| `FundFilterResult` | 03-batch | Batch 3 | Batch 4 |
| `run_10d_filter()` | 03-batch | Batch 3 | Batch 4 |
| `run_purchasability_check()` | 03-batch | Batch 3 | Batch 4 |
| `apply_anti_homogeneity()` | 03-batch | Batch 3 | Batch 4 |
| `ValidationReport` | 04-batch | Batch 4 | 终端 |
| `BehaviorPattern` | 05-batch | Batch 5 | Batch 6, Batch 8 |
| `detect_patterns()` | 05-batch | Batch 5 | Batch 6 |
| `BehaviorReport` | 05-batch | Batch 5 | M5 复盘报告 |
| `InterventionRule` | 06-batch | Batch 6 | Batch 8（可选） |
| `ActiveIntervention` | 06-batch | Batch 6 | Batch 8（可选） |
| `evaluate_intervention()` | 06-batch | Batch 6 | 终端 |
| `is_guard_enabled()` / `set_guard_enabled()` | 06-batch | Batch 6 | `evaluate_intervention()` 入口 |
| `EventTemplate` | 07-batch | Batch 7 | 凌晨工厂 |
| `MatchedEvent` | 07-batch | Batch 7 | 凌晨工厂 |
| `match_events()` | 07-batch | Batch 7 | 凌晨工厂 |
| `ChartResponse` | 08-batch | Batch 8 | 前端 |
| `fetch_daily_kline()` | 08-batch | Batch 8 | `api/chart.py` |
