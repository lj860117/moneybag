# Batch 6：行为归因检测 — 联动执行版（M9）

> 来源：`14-m7-plus-enhancement-for-claude.md` §四 4.2「联动执行版（M9）：检测 → 执行干预」
>
> **本文件为独立批次文档，包含开工所需全部信息。不需要翻阅原文。**

---

## 批次概览

| 属性 | 值 |
|---|---|
| 批次编号 | Batch 6 |
| 里程碑 | M9 |
| 产出文件 | `behavior_intervention_rules.py` |
| 需读文档 | 本文件 + `03-rule-engine.md`（position_pct_max/action 字段）+ Batch 5 产出 |
| 📋 前置依赖 | Batch 5（行为归因基础版）+ §九 验证 1 结果 |
| 可并行 | 与 Batch 7（事件解读）+ Batch 8（TradingView/行为干预）独立并行 |

---

## 详细设计

### 1. 目标

在 M8 基础版（Batch 5）跑稳后，通过 M3 规则引擎已有的 `position_pct_max` 和 `action` 字段，把风控从"报告"落到"执行"。

**关键原则**：所有执行干预都通过 M3 已有字段实现，**不新增执行层代码**，只新增规则配置。

### 2. 前提假设（§九 验证 1）

M3 的 `position_pct_max` 支持**运行时临时覆盖**（如 `temp_position_pct_max = base * 0.8`），并在月末/条件解除后自动恢复。

**若 M3 不支持运行时覆盖**（验证 1 = "不支持"），此功能降级为"纯弹窗提醒"。

### 3. 偏差→执行干预映射

| 检测到的偏差 | 触发的执行动作 | 复用的 M3 机制 | 白名单 | M3 扩展需求 |
|---|---|---|---|---|
| 追涨冲动（RSI>70） | **冷静期**：弹窗+24h 内再买同标的需二次确认 | `action=提醒` + `decision_log` 时间检查 | 再平衡不受限 | ⚠️ 需新增冷却期状态 |
| 过度交易（本月非定投 ≥5 次） | **仓位上限下调 20%** | `position_pct_max` 临时覆盖 | 定投/自动/再平衡不计入 | ⚠️ 需月度临时覆盖+自动恢复 |
| 行业集中（>35%） | **自动拦截**新增买入 | `action=禁止` | 再平衡不受限 | 无 |
| FOMO 交易（大涨日买入） | **金额锁死**：上限为余额 5% | `position_pct_max` 临时覆盖 | 再平衡不受限 | ⚠️ 需月度临时覆盖 |
| 高位加仓（PE 分位>70%） | **强制确认**弹窗 | `action=提醒` | 再平衡不受限 | 无 |

### 4. M3 扩展项

需要以下扩展（M7 开工前确认可行性）：

1. **冷却期状态**：新增 `cooldown_until: datetime`，交易前检查
2. **月度临时覆盖**：`temp_position_pct_max` 字段，月末自动恢复
3. **降级方案**：若 M3 不支持 → 纯弹窗提醒（无时间限制、无自动下调）

### 5. 再平衡白名单

再平衡操作不计入过度交易统计，不受仓位下调和冷静期限制。

### 6. 全局紧急开关

系统设置页增加"行为风控总开关"（`BEHAVIOR_GUARD_ENABLED`），用户可一键关闭**所有**行为干预。

| 开关状态 | 行为 |
|---|---|
| 🟢 开启（默认） | 正常执行所有干预规则（冷静期、仓位限制、拦截、确认弹窗） |
| 🔴 关闭 | 所有干预降级为**纯报告模式**——Batch 5 的检测和报告照常运行，但不触发任何冷静期、仓位下调、拦截。用户只看到报告，不被拦截 |

**设计要点**：
- 开关状态变更记录在 `decision_log` 中（时间 + 操作人 + 原因），方便后续复盘
- 关闭后页面顶部常驻提示条："⚠️ 行为风控已关闭，所有交易不受行为约束"
- **不提供单条规则的独立开关**——要么全开要么全关，避免用户逐条关闭后等于没有风控

**接口**：

```python
def is_guard_enabled(user_id: str) -> bool:
    """检查行为风控总开关状态。"""

def set_guard_enabled(user_id: str, enabled: bool, reason: str) -> None:
    """设置行为风控总开关，变更记录到 decision_log。"""
```

### 7. 用户可见产出

交易页面增加"行为风控状态"指示器（🟢 正常 / 🟡 受限 / 🔴 禁止）。

### 8. 验收标准

5 类偏差场景各触发 1 次，执行干预正确率 100%，用户可手动覆盖（需二次确认）。

---

## 接口契约占位

### `InterventionRule` 数据结构

```python
@dataclass
class InterventionRule:
    """单条干预规则"""
    trigger_pattern: str        # 对应 BehaviorPattern.pattern_type
    action_type: str            # "cooldown" | "position_limit" | "block" | "confirm"
    m3_action: str              # "提醒" | "禁止"
    m3_field: Optional[str]     # 如 "position_pct_max"
    temp_value: Optional[float] # 临时覆盖值
    duration: Optional[str]     # "24h" | "end_of_month" | "until_resolved"
    whitelist: list[str]        # ["rebalance", "auto_invest"]

@dataclass
class ActiveIntervention:
    """当前生效的干预"""
    user_id: str
    rule: InterventionRule
    triggered_at: datetime
    expires_at: Optional[datetime]
    status: str                 # "active" | "expired" | "overridden_by_user"
    trigger_evidence: BehaviorPattern
```

### `behavior_intervention_rules.py` 核心函数

```python
def evaluate_intervention(
    pattern: BehaviorPattern,
    current_trade: TradeRequest,
    m3_config: dict,
    verification_1_result: str,  # "supported" | "not_supported"
) -> Optional[InterventionAction]:
    """
    根据偏差模式评估是否触发干预。
    ⚠️ 入口处先调用 is_guard_enabled()，若全局开关关闭则直接返回 None。
    """

def is_guard_enabled(user_id: str) -> bool:
    """检查行为风控总开关状态。关闭时所有干预降级为纯报告。"""

def set_guard_enabled(user_id: str, enabled: bool, reason: str) -> None:
    """设置行为风控总开关，变更记录到 decision_log。"""

def get_active_interventions(user_id: str) -> list[ActiveIntervention]:
    """获取当前所有生效的干预。"""

def check_whitelist(trade: TradeRequest) -> bool:
    """检查交易是否在白名单内。"""

@dataclass
class InterventionAction:
    action_type: str             # "prompt" | "limit" | "block"
    message: str
    m3_overrides: Optional[dict]
    requires_confirmation: bool
    is_degraded: bool            # 验证 1 不支持时为 True
```

---

## 文件落位

| 文件路径 | 职责 | 预估行数 |
|---|---|---|
| `domain/rule_engine/behavior_intervention_rules.py` | 偏差→干预映射 + 白名单 + 降级 | <200 |

---

## 🔗 跨批次耦合

| 被哪个批次引用 | 引用内容 | 说明 |
|---|---|---|
| Batch 8（可选） | `ActiveIntervention` | TradingView 图表可选显示冷静期倒计时 |

本批次引用了：
| 引用哪个批次 | 引用内容 |
|---|---|
| Batch 5 | `BehaviorPattern` + `detect_patterns()` |
| Batch 2 | `deviation_thresholds.get_tolerance()`（市场背景判断） |

---

## 📋 前置依赖

| 依赖 | 内容 | 阻塞 |
|---|---|---|
| Batch 5 | `behavior_detector.py` + `BehaviorPattern` | 🔴 阻塞 |
| §九 验证 1 | `position_pct_max` 运行时覆盖 | 🔴 阻塞（决定正常 vs 降级） |

---

## 🚫 禁止假设

1. **不能假设 M3 支持运行时覆盖**——必须读 §九 验证 1
2. **不能新增执行层代码**——只通过 M3 已有字段
3. **不能假设 Batch 5 已完成**——必须等基础版跑稳
4. **不能禁止再平衡**——永远在白名单
5. **不能假设冷却期存储已有**——不支持则降级为无时间限制提醒

---

## ⚙️ 全局契约引用

- **defaults.py 新增**：本批次的 `BehaviorInterventionDefaults` 需遵循命名规范 → 详见 [00-interface-contracts.md](00-interface-contracts.md) §二
- **冲突处理**：实现过程中遇到 `position_pct_max`/`action` 字段不够用、需要新增 M3 字段等情况时 → **停止编码，按 [README.md](README.md) 冲突处理 SOP 记录冲突，等人工确认**
