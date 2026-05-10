# Batch 8：行为干预联动 + TradingView 辅助监控（M9+）

> 来源：`14-m7-plus-enhancement-for-claude.md` §五（补）「TradingView 辅助监控」+ §四 4.2 行为干预执行层可视化
>
> **本文件为独立批次文档，包含开工所需全部信息。不需要翻阅原文。**

---

## 批次概览

| 属性 | 值 |
|---|---|
| 批次编号 | Batch 8 |
| 里程碑 | M9+ |
| 产出文件 | `chart.py`、`tushare_chart.py` |
| 需读文档 | 本文件 + `12-framework-refactor.md`（api 层规范） |
| 📋 前置依赖 | 无（独立模块） |
| 可并行 | 与 Batch 6 + Batch 7 独立并行 |

> **命名说明**：用户要求的文件名为"行为干预联动"，但行为干预规则逻辑已在 Batch 6 中完整定义。本批次内容为 TradingView 辅助监控——作为行为干预的**可视化终端**，将 Batch 5/6 的检测与干预结果呈现在图表上，形成完整的"检测→干预→可视化"闭环。

---

## 详细设计

### Part A：TradingView 辅助监控（核心交付物）

#### 1. 问题与方案

**问题**：用户想直观看到标的价格走势、回撤幅度、关键价位突破，但 MoneyBag 前端不计划自研复杂图表组件。

**方案**：集成 TradingView Lightweight Charts（开源、免费、无水印）。

#### 2. 四层架构定位

TradingView 监控属于"数据展示"，不新增 `presentation/` 层。数据拉取走 `infra/data_source/`，API 端点走 `api/chart.py`，前端组件由前端框架承载。

#### 3. 功能范围

- 候选池每只标的点击进入「迷你行情页」，显示日线 K 线 + 成交量
- 叠加用户买入成本线（显示浮盈浮亏区域）
- 关键指标可视化：RSI 超买超卖区间、PE 分位标记线
- **不做实时推送**，页面打开时拉一次数据即可

#### 4. 边界

- 只展示、不分析（不输出"建议买入/卖出"）
- 不替代券商 App 的交易功能
- 数据来源用 Tushare 日线，不额外付费

#### 5. 验收标准

10 只标的的迷你行情页，首屏加载 <2s，图表交互流畅。

### Part B：行为干预可视化联动（可选增强）

Batch 6 定义了偏差→执行干预映射。Batch 8 的 TradingView 图表可**可视化增强**：

- 在迷你行情页上标记"追高买入"时点（RSI>70 时的买入用红点标记）
- 在迷你行情页上标记"FOMO 买入"时点（大涨日买入用蓝点标记）
- 显示"冷静期倒计时"（若 Batch 6 触发了冷静期）
- 显示"行为风控状态"指示器（🟢/🟡/🔴）

这些可视化增强**不是必须的**，属于 Batch 8 的可选增强。核心交付物是基础图表功能。

---

## 接口契约占位

### `chart.py` API 端点

```python
@router.get("/chart/{fund_code}")
async def get_chart_data(
    fund_code: str,
    period: str = "1y",        # "3m" | "6m" | "1y" | "3y"
    include_cost_line: bool = True,
    include_indicators: bool = True,
    include_behavior_marks: bool = False,  # 可选：行为偏差标记
) -> ChartResponse:
    """获取标的迷你行情数据。"""

@dataclass
class ChartResponse:
    fund_code: str
    fund_name: str
    period: str
    kline_data: list[KLinePoint]
    volume_data: list[VolumePoint]
    cost_line: Optional[float]
    indicators: Optional[ChartIndicators]
    behavior_marks: Optional[list[BehaviorMark]]  # 可选

@dataclass
class KLinePoint:
    date: str
    open: float
    high: float
    low: float
    close: float

@dataclass
class VolumePoint:
    date: str
    volume: int

@dataclass
class ChartIndicators:
    rsi_14: list[dict]
    pe_percentile: Optional[float]

@dataclass
class BehaviorMark:
    """行为偏差标记（可选增强）"""
    date: str
    mark_type: str       # "chasing_high" | "fomo" | "high_pe_adding"
    color: str           # "red" | "blue" | "orange"
    tooltip: str         # 鼠标悬停提示文案
```

### `tushare_chart.py` 数据拉取

```python
def fetch_daily_kline(ts_code: str, start_date: str, end_date: str) -> list[KLinePoint]:
    """从 Tushare 拉取日线数据。"""

def fetch_daily_volume(ts_code: str, start_date: str, end_date: str) -> list[VolumePoint]:
    """从 Tushare 拉取日成交量。"""

def calculate_rsi(kline_data: list[KLinePoint], period: int = 14) -> list[dict]:
    """基于收盘价计算 RSI。"""
```

---

## 文件落位

| 文件路径 | 职责 | 预估行数 |
|---|---|---|
| `api/chart.py` | 迷你行情接口端点 | <50 |
| `infra/data_source/providers/tushare_chart.py` | 日线数据拉取 + RSI 计算 | <100 |

---

## 🔗 跨批次耦合

| 被哪个批次引用 | 引用内容 | 说明 |
|---|---|---|
| 无 | — | TradingView 为终端展示层 |

本批次引用了：
| 引用哪个批次 | 引用内容 |
|---|---|
| M3 规则引擎 | 候选池数据 |
| Batch 5（可选） | `BehaviorPattern`（行为偏差标记） |
| Batch 6（可选） | `ActiveIntervention`（冷静期倒计时） |

---

## 📋 前置依赖

无。本批次为独立模块。

---

## 🚫 禁止假设

1. **不能输出投资建议**
2. **不能做实时推送**
3. **不能假设 TradingView 库已安装**
4. **不能额外付费购买数据**
5. **不能假设 Batch 5/6 已完成**——行为标记为可选增强
6. **不能新增 `presentation/` 层**

---

## ⚙️ 全局契约引用

- **冲突处理**：实现过程中遇到需要修改 M1-M6 文件、需要新增路由到已有路由文件等情况时 → **停止编码，按 [README.md](README.md) 冲突处理 SOP 记录冲突，等人工确认**
