# MoneyBag V6 完整升级设计方案

> **版本**：V4-Final（整合 V1~V4 全部迭代）
> **日期**：2026-04-15
> **背景**：双AI vs 6家机构对比，发现 AI 最大盲点是**地缘政治**和**大宗商品**。本文档设计 7 个改进模块（A~G），从数据层→分析层→展示层→历史存档全链路打通。
> **设计原则**：不新建框架，在现有 ModuleRegistry + Pipeline + `_build_market_context` 体系上增量扩展。每个模块独立可部署，互相可选依赖。
> **配套文件**：`双AI投资分析对比报告.md` / `AI分析vs专业机构对比报告.md`

---

## 目录

1. [项目背景与问题诊断](#1-项目背景与问题诊断)
2. [架构总览](#2-架构总览)
3. [模块A：地缘政治/重大事件追踪 🔴 P0](#3-模块a地缘政治重大事件追踪--p0)
4. [模块B：原油/大宗商品扩展 🔴 P0](#4-模块b原油大宗商品扩展--p0)
5. [模块C：修复北向资金 + ETF资金流 🟡 P1](#5-模块c修复北向资金--etf资金流--p1)
6. [模块D：行业轮动分析 🟡 P1](#6-模块d行业轮动分析--p1)
7. [模块E：券商研报摘要 🟢 P2](#7-模块e券商研报摘要--p2)
8. [模块F：情景分析引擎 🟢 P2](#8-模块f情景分析引擎--p2)
9. [模块G：分析历史 + 多源对比 + 自动录入 🔴 P0](#9-模块g分析历史--多源对比--自动录入--p0)
10. [模块关联矩阵](#10-模块关联矩阵)
11. [各场景下的使用方式](#11-各场景下的使用方式)
12. [前后端对应审查结果](#12-前后端对应审查结果)
13. [Simple/Pro 模式分配](#13-simplepro-模式分配)
14. [多账号（铁律#19）审查](#14-多账号铁律19审查)
15. [铁律检查点矩阵](#15-铁律检查点矩阵)
16. [逐 Phase 验证清单](#16-逐-phase-验证清单)
17. [实施计划（5 Phase / 8-12天）](#17-实施计划5-phase--8-12天)
18. [风险评估](#18-风险评估)
19. [预期效果](#19-预期效果)

---

## 1. 项目背景与问题诊断

### 对比实验摘要

2026-04-15 进行了 **MoneyBag 双AI（Claude + DeepSeek Pipeline V4.5）** vs **6家专业机构（招商/易方达/华龙/国泰海通/中信/证券市场周刊）** 的三方对比验证。

**AI 做对的（3.0/5 综合评分）**：
- ✅ 市场方向判断：B+（Claude"谨慎乐观"与招商/易方达吻合）
- ✅ 估值判断：A（45.1%百分位精确量化）
- ✅ 配置方向：B（宽基ETF+黄金+科技+红利与机构方向一致）
- ✅ 技术面分析：A-（RSI/MACD/布林带量化到位，机构研报通常不覆盖）
- ✅ 数据透明度：A（标出5个数据源不可用）
- ✅ 响应速度：A+（46秒 vs 机构研报1-2周）

**AI 做错的（致命盲点）**：
- ❌ **地缘政治：F** — 3月A股暴跌6-7%的元凶（美以伊冲突）完全未提及
- ❌ **能源/资源板块：F** — 油价100美元/桶背景下完全遗漏能源板块
- ❌ 产业链深度：D — 只到ETF级别，无法触及产业链细节
- ❌ 个股研判：D — 不推荐个股

### V6 要解决的 7 个问题

| # | 问题 | 优先级 | 对应模块 |
|---|------|--------|---------|
| 1 | AI 看不到地缘政治/重大事件 | 🔴 P0 | 模块A |
| 2 | AI 看不到原油/大宗商品价格 | 🔴 P0 | 模块B |
| 3 | 北向资金数据断了 + ETF资金流不准 | 🟡 P1 | 模块C |
| 4 | 只有指数级别，缺行业颗粒度 | 🟡 P1 | 模块D |
| 5 | AI 不知道机构在想什么 | 🟢 P2 | 模块E |
| 6 | 不能做"如果...会怎样"的情景分析 | 🟢 P2 | 模块F |
| 7 | 每次分析完结果就丢了 + Claude只能手动粘贴 + 没有多源对比 | 🔴 P0 | 模块G |

---

## 2. 架构总览

```
                          ┌─────────────────────────────────────────┐
                          │           _build_market_context()        │
                          │  (main.py:2017-2159, 注入 DeepSeek)     │
                          └──────────┬──────────────────────────────┘
                                     │ 读取
    ┌────────────────────────────────┼────────────────────────────────┐
    │                                │                                │
    ▼                                ▼                                ▼
┌──────────┐  ┌───────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────┐
│ 模块A    │  │ 模块B         │  │ 模块C        │  │ 模块D       │  │ 模块E    │
│地缘事件  │  │原油+大宗扩展  │  │北向+ETF修复  │  │行业轮动     │  │研报摘要  │
│ NEW      │  │ EXTEND        │  │ FIX          │  │ NEW         │  │ NEW      │
└────┬─────┘  └──────┬────────┘  └──────┬───────┘  └─────┬───────┘  └────┬─────┘
     │               │                  │                │               │
     └───────┬───────┴────────┬─────────┴────────┬───────┘               │
             │                │                  │                       │
             ▼                ▼                  ▼                       │
      ┌──────────────────────────────────────────────┐                   │
      │  模块F: 情景分析引擎 (scenario_engine.py)     │ ◄────────────────┘
      │  "如果中东停火" / "如果油价突破120"            │
      └──────────────────────────────────────────────┘
             │
             ▼
      ┌──────────────┐     ┌────────────────┐     ┌──────────────┐
      │ Pipeline      │     │ signal.py      │     │ regime_engine │
      │ (仲裁时注入)  │     │ (第13维因子)   │     │ (地缘加权)   │
      └──────────────┘     └────────────────┘     └──────────────┘

      ┌──────────────────────────────────────────────┐
      │  模块G: 分析历史系统 (analysis_history.py)    │
      │  统一存档 / 多源对比 / Claude 自动录入        │
      └──────────────────────────────────────────────┘
```

---

## 3. 模块A：地缘政治/重大事件追踪 🔴 P0

### 问题
AI 完全没看到美以伊冲突、霍尔木兹海峡、油价100美元——这是3月A股暴跌6-7%的元凶。现有 `news_data.py` 的 `POLICY_KEYWORDS` 只有**被动关键词过滤**，没有主动追踪、严重性评估、持续时间感知。

### 设计

**新建文件**：`backend/services/geopolitical.py`

```python
MODULE_META = {
    "name": "geopolitical",
    "scope": "public",
    "input": [],
    "output": "geopolitical_risk",
    "cost": "llm_light",
    "tags": ["地缘", "风险事件", "黑天鹅"],
    "description": "地缘政治事件追踪+严重性评估+A股影响链",
    "layer": "data",
    "priority": 1,
}
```

**核心函数**：

| 函数 | 职责 | 数据源 | 缓存 |
|------|------|--------|------|
| `get_geopolitical_events()` | 抓取地缘新闻 + 分类 + 评级 | AKShare 新闻 + 关键词规则 | 30min |
| `assess_event_severity(events)` | DS 评估严重性 1-5 级 | DeepSeek V3 | 1h |
| `get_geopolitical_risk_score()` | 输出综合地缘风险分 0-100 | 规则 + DS | 30min |
| `get_geo_impact_on_sectors()` | 地缘→行业→持仓影响链 | 规则映射 | 30min |
| `enrich(ctx)` | Pipeline 注入 | 上面四个 | - |

**关键词体系**（扩展现有 POLICY_KEYWORDS）：

```python
GEO_EVENT_CATEGORIES = {
    "军事冲突": {
        "keywords": ["战争", "军事", "空袭", "导弹", "入侵", "开战", "停火",
                     "以色列", "伊朗", "中东", "俄乌", "台海", "朝鲜",
                     "霍尔木兹", "红海", "南海"],
        "base_severity": 4,
    },
    "制裁升级": {
        "keywords": ["制裁", "禁运", "封锁", "脱钩", "出口管制", "实体清单",
                     "芯片禁令", "技术封锁"],
        "base_severity": 3,
    },
    "能源危机": {
        "keywords": ["石油危机", "天然气", "能源安全", "断供", "管道",
                     "OPEC减产", "油价暴涨", "能源短缺"],
        "base_severity": 4,
    },
    "金融风险": {
        "keywords": ["银行倒闭", "债务危机", "主权违约", "资本外逃",
                     "汇率崩盘", "流动性危机"],
        "base_severity": 3,
    },
    "贸易摩擦": {
        "keywords": ["关税", "贸易战", "报复", "反倾销", "WTO"],
        "base_severity": 2,
    },
}
```

**地缘→行业影响映射表**：

```python
GEO_SECTOR_IMPACT = {
    "军事冲突": {
        "bullish": ["黄金", "军工", "石油", "债券"],
        "bearish": ["航空", "旅游", "消费", "科技"],
        "a_share_impact": "避险情绪升温，资金从成长转向防御",
    },
    "能源危机": {
        "bullish": ["石油", "煤炭", "新能源", "黄金"],
        "bearish": ["航空", "化工", "运输", "消费"],
        "a_share_impact": "输入性通胀压力，央行政策空间收窄",
    },
    # ...其他类别类似
}
```

**严重性评估逻辑**：

```
规则预筛（0 成本）：
  1. 关键词命中 → base_severity
  2. 多类别同时命中 → severity +1
  3. 连续3天出现 → severity +1（持续性）
  4. severity >= 3 → 调 DeepSeek 做精细评估

DS 精细评估（1 次 LLM）：
  prompt: "评估以下地缘事件对A股的影响，返回JSON:
           {severity:1-5, duration_days, affected_sectors[], a_share_impact_pct}"
  → 用 LLMGateway.call_sync(model_tier="llm_light")
```

### 接入点

| 接入位置 | 怎么接 | 影响 |
|---------|--------|------|
| `_build_market_context()` | 新增"地缘风险"段落 | DeepSeek 聊天/分析都能看到 |
| `signal.py` | 新增第13维因子"地缘面" | 综合信号评分纳入地缘 |
| `regime_engine.py` | severity≥4 时强制 regime=high_vol_bear | Pipeline 自动切 cautious 管线 |
| `pipeline_runner.py` | step_risk_firewall 读 geo risk score | severity=5 → 风控一票否决 |
| `cache_warmer.py` | warm_morning + warm_midday 预热 | 地缘新闻时效性高 |
| `stock_monitor_cron.py` | 扫描时检查地缘事件 | 有重大事件→企微推送 |
| `ds_enhance.py` | assess_news_risk 增加地缘维度 | 新闻风控更准 |

---

## 4. 模块B：原油/大宗商品扩展 🔴 P0

### 问题
`market_factors.py` 只有黄金(AU0)和铜(CU0)，**没有原油**——2026年3月油价从65涨到100美元，所有机构都在讨论能源冲击，AI 一个字没提。

### 设计

**修改文件**：`backend/services/market_factors.py`

**新增大宗商品**：

| 品种 | AKShare 接口 | symbol | 意义 |
|------|-------------|--------|------|
| 原油（上期能源） | `futures_main_sina` | `SC0` | A股能源链+通胀预期 |
| 布伦特原油（国际） | `futures_foreign_hist` | `BZ` | 国际油价基准 |
| 天然气 | `futures_main_sina` | `LU0` | 能源替代品 |
| 铁矿石 | `futures_main_sina` | `I0` | 钢铁/基建晴雨表 |
| 螺纹钢 | `futures_main_sina` | `RB0` | 基建活跃度 |

**新增函数**：

```python
def get_crude_oil_price() -> dict:
    """专门获取原油价格（国内SC + 国际布伦特）"""
    return {
        "sc": {"price": 750, "change_pct": 2.1, "unit": "元/桶"},
        "brent": {"price": 102, "change_pct": 1.5, "unit": "美元/桶"},
        "alert_level": "warning",  # normal/warning/crisis
        "vs_30d_avg": "+15%",
        "available": True,
    }

def get_commodity_impact_assessment() -> dict:
    """大宗商品价格→A股影响评估（纯规则，0 LLM）"""
    return {
        "oil_impact": "输入性通胀压力，航空化工承压，能源股受益",
        "metal_impact": "铜价回暖反映经济预期改善",
        "overall_tone": "bearish",
    }
```

**油价阈值配置**（加到 `config.py`）：

```python
OIL_BRENT_NORMAL = 80      # 美元/桶，正常区间
OIL_BRENT_WARNING = 100    # 警戒线
OIL_BRENT_CRISIS = 120     # 危机线
```

### 接入点

| 接入位置 | 怎么接 |
|---------|--------|
| `_build_market_context()` 大宗商品段 | 原来只有黄金/铜，加上原油/铁矿石，油价>100标红 |
| `get_all_market_factors()` | 返回值加 `crude_oil` 字段 |
| `NEWS_IMPACT_MAP` "大宗商品"规则 | 扩展 impact 描述，加入油价区间判断 |
| `signal.py` 宏观面因子 | 油价偏离度纳入宏观面评分 |
| 模块A 的 `get_geo_impact_on_sectors()` | 能源危机类事件 + 油价数据联动 |

### 模块A↔B 联动

```
模块A 检测到"能源危机"类事件 (severity≥3)
  → 自动调 模块B 的 get_crude_oil_price()
  → 如果 brent>100 → 触发联合警报: "地缘冲突+油价飙升"
  → 注入 _build_market_context(): "⚠️ 地缘+能源双重风险"
  → regime_engine 强制 cautious
```

---

## 5. 模块C：修复北向资金 + ETF资金流 🟡 P1

### 问题
- 北向资金：2024年8月起交易所不再披露每日净流入，代码已做降级处理但数据过时
- ETF资金流：AKShare 1.18.55 无专用接口，已用增长率排名替代，但不是真实资金流

### 设计

**修改文件**：`backend/services/factor_data.py`（北向）+ `backend/services/market_factors.py`（ETF）

**北向资金修复方案（三级降级）**：

```python
def get_northbound_flow() -> dict:
    """北向资金"""
    # 方案1: stock_hsgt_hist_em（官方，8月后可能不完整）
    # 方案2: 港交所数据 stock_hsgt_hold_stock_em（个股持仓变化推算）
    # 方案3: 沪港通/深港通成交额差值估算
    # 新增：从个股层面推算整体方向
```

**ETF 资金流修复方案（四级降级）**：

```python
def get_etf_fund_flow() -> dict:
    """ETF资金流"""
    # 方案A: fund_etf_fund_flow_em（真实流）
    # 方案B: fund_etf_spot_em（当日实时）
    # 方案C: fund_etf_fund_daily_em 增长率排名（当前已实现）
    # 方案D: ETF份额变化（fund_etf_hist_em 的份额列日差）
```

### 接入点
- 已有接入无需新增（`signal.py` 资金面 + `_build_market_context()` 已读北向和ETF）
- 修复后数据质量提升，下游自动受益

---

## 6. 模块D：行业轮动分析 🟡 P1

### 问题
目前只到"沪深300指数"级别，缺乏行业颗粒度。机构研报的核心是"配半导体/配新能源/配能源"这种行业级建议，AI做不到。

### 设计

**新建文件**：`backend/services/sector_rotation.py`

```python
MODULE_META = {
    "name": "sector_rotation",
    "scope": "public",
    "input": [],
    "output": "sector_data",
    "cost": "cpu",
    "tags": ["行业", "轮动", "板块", "资金流"],
    "description": "行业轮动分析：申万一级行业涨跌/资金流/动量排名",
    "layer": "data",
    "priority": 2,
}
```

**核心函数**：

| 函数 | 职责 | AKShare 接口 |
|------|------|-------------|
| `get_sector_performance()` | 31个申万一级行业近1/5/20日涨跌排名 | `stock_board_industry_name_em` + `stock_board_industry_hist_em` |
| `get_sector_fund_flow()` | 行业资金流向TOP5/BOTTOM5 | `stock_sector_fund_flow_rank` |
| `get_sector_momentum()` | 行业动量因子（20日涨幅 + 5日加速度） | 计算列 |
| `detect_rotation_pattern()` | 检测轮动模式（哪些板块在接力） | 规则引擎 |
| `get_hot_sectors()` | 当前热门板块（资金+涨幅+新闻三维） | 综合 |
| `enrich(ctx)` | Pipeline 注入行业数据 | - |

**轮动模式检测**：

```python
ROTATION_PATTERNS = {
    "防御转进攻": {
        "condition": "近5日 银行/公用事业下跌 AND 科技/军工上涨",
        "meaning": "资金从避险切换到进攻",
        "a_share_signal": "bullish",
    },
    "进攻转防御": {
        "condition": "近5日 科技/新能源下跌 AND 银行/煤炭上涨",
        "meaning": "资金从成长切换到防御",
        "a_share_signal": "bearish",
    },
    "全面普涨": {
        "condition": "近5日 >20个行业上涨",
        "meaning": "牛市信号",
        "a_share_signal": "strong_bullish",
    },
    "全面普跌": {
        "condition": "近5日 >20个行业下跌",
        "meaning": "熊市信号",
        "a_share_signal": "strong_bearish",
    },
}
```

### 接入点

| 接入位置 | 怎么接 |
|---------|--------|
| `_build_market_context()` | 新增"行业轮动"段落：TOP3涨幅行业 + 轮动模式 |
| `signal.py` | 新增第14维因子"行业面" |
| `regime_engine.py` | 全面普跌→高波熊市加分 |
| `ds_enhance.py` generate_daily_focus | 注入行业数据让"今日关注"更有料 |
| `portfolio.py` 配置建议 | 基于行业轮动推荐超配/低配行业 |
| `holding_intelligence.py` | 用户持仓所属行业是热门还是冷门 |
| 前端 | 新增"行业热力图"展示模块 |

### 跨模块联动

**A↔D 联动**：
```
模块A 检测到"制裁升级"→半导体行业
  → 模块D 检查半导体行业近期表现
  → 如果半导体已跌5% → "利空已部分消化"
  → 如果半导体还在涨 → "利空尚未反映，注意风险"
```

**B↔D 联动**：
```
模块B 检测到油价>100 + 持续上涨
  → 模块D 检查能源/煤炭行业排名
  → 如果能源行业TOP3 → "能源板块已热，追高需谨慎"
  → 如果能源行业不在TOP10 → "能源链尚未被市场充分定价，关注机会"
```

---

## 7. 模块E：券商研报摘要 🟢 P2

### 问题
AI不知道机构在想什么。用户需要"听听专业人士怎么说"的能力。

### 设计

**新建文件**：`backend/services/broker_research.py`

```python
MODULE_META = {
    "name": "broker_research",
    "scope": "public",
    "input": [],
    "output": "broker_views",
    "cost": "llm_light",
    "tags": ["研报", "券商", "策略观点"],
    "description": "主流券商策略观点摘要（月度+事件驱动）",
    "layer": "data",
    "priority": 4,
}
```

**数据源策略（由易到难）**：

| 优先级 | 数据源 | 可行性 | 说明 |
|--------|--------|--------|------|
| 1️⃣ | AKShare `stock_report_fund_hold_em` | ✅ 已有 | 机构持仓变化（间接推断观点）|
| 2️⃣ | 东方财富研报标题（AKShare 爬取） | 🔶 需验证 | `stock_news_em(symbol="研报")` |
| 3️⃣ | Web 搜索券商策略关键词 | ✅ WebSearch | "中信证券 4月策略 A股" |
| 4️⃣ | Tushare `report_rc`（需积分） | 🔶 备选 | 已有 tushare_data.py 框架 |

**核心函数**：

```python
def get_broker_consensus() -> dict:
    """获取主流券商策略共识"""
    return {
        "consensus": "谨慎乐观",
        "bullish_count": 4, "bearish_count": 1, "neutral_count": 2,
        "key_sectors": ["半导体", "新能源", "能源"],
        "key_risks": ["中东局势", "油价", "美联储"],
        "source_count": 7,
        "updated_at": "2026-04-15",
    }
```

### 接入点

| 接入位置 | 怎么接 |
|---------|--------|
| `_build_market_context()` | 新增"机构观点"段落 |
| `ds_enhance.py` 配置建议增强 | 对比AI建议 vs 机构共识的差异 |
| `cache_warmer.py` warm_morning | 早盘预热拉一次 |
| 企微推送 | 早报加一行"机构今日共识" |

---

## 8. 模块F：情景分析引擎 🟢 P2

### 问题
用户问"如果中东停火A股会怎样"——AI 没有这个能力。这是专业机构报告中"情景分析/压力测试"的核心功能。

### 设计

**新建文件**：`backend/services/scenario_engine.py`

```python
MODULE_META = {
    "name": "scenario_engine",
    "scope": "public",
    "input": ["scenario_type"],
    "output": "scenario_analysis",
    "cost": "llm_heavy",  # 需要 R1 深度推理
    "tags": ["情景", "假设", "压力测试", "What-if"],
    "description": "情景分析：给定假设条件→推演A股影响→给配置建议",
    "layer": "analysis",
    "priority": 5,
}
```

**预设情景模板（4 个即用型）**：

```python
PRESET_SCENARIOS = {
    "ceasefire": {
        "name": "中东停火",
        "assumptions": "美以伊达成停火协议，霍尔木兹海峡恢复正常通航",
        "affected_vars": ["oil_price:-30%", "gold:-5%", "risk_appetite:+"],
        "sector_impact": {"能源": "bearish", "航空": "bullish", "军工": "bearish", "消费": "bullish"},
    },
    "oil_120": {
        "name": "油价突破120",
        "assumptions": "布伦特原油突破120美元/桶，持续1个月以上",
        "affected_vars": ["oil_price:+20%", "cpi:+0.5%", "a_share:-5%"],
        "sector_impact": {"能源": "strong_bullish", "化工": "bearish", "航空": "strong_bearish"},
    },
    "fed_cut": {
        "name": "美联储意外降息",
        "assumptions": "美联储紧急降息50BP",
        "affected_vars": ["usd:-2%", "gold:+3%", "northbound:+", "a_share:+3%"],
        "sector_impact": {"科技": "bullish", "地产": "bullish", "银行": "neutral"},
    },
    "chip_ban": {
        "name": "芯片禁令升级",
        "assumptions": "美国扩大对华芯片出口限制范围",
        "affected_vars": ["tech_sentiment:-", "domestic_sub:+"],
        "sector_impact": {"半导体": "short_bearish_long_bullish", "国产替代": "bullish"},
    },
}
```

**核心函数**：

```python
def analyze_scenario(scenario_id: str, custom_text: str = "") -> dict:
    """情景分析主函数（消费所有其他模块数据 + R1 推理）"""
    return {
        "scenario": "中东停火",
        "probability": "30%",
        "market_impact": {"a_share": "+3~5%", "oil": "-25~30%", "gold": "-3~5%"},
        "sector_winners": ["航空", "消费", "旅游"],
        "sector_losers": ["能源", "军工", "黄金"],
        "portfolio_advice": "减持能源ETF，加仓消费ETF",
        "timeframe": "1-3个月",
    }
```

**API 端点（3个）**：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/scenario/{scenario_id}` | GET | 预设情景分析（**注意：需加 userId 参数**） |
| `/api/scenario/custom` | POST | 自定义情景分析 |
| `/api/scenarios` | GET | 列出所有预设情景 |

### 接入点

| 接入位置 | 怎么接 |
|---------|--------|
| 前端 | 新增"情景分析"Tab（选预设 or 输入自定义）|
| 企微 | "情景 中东停火" 快捷指令 |
| AI聊天 | 规则引擎识别"如果...会怎样" → 自动调用 |
| 不注入 market_context | 按需调用，不是常态数据 |

### 模块F 消费所有其他模块
```
情景分析 = f(
    当前地缘状态(A) + 当前油价/大宗(B) + 当前资金流向(C) +
    当前行业排名(D) + 当前机构观点(E) + 假设条件 + DeepSeek R1 推理
)
```

---

## 9. 模块G：分析历史 + 多源对比 + 自动录入 🔴 P0

> **V4 重写（2026-04-15）**：用户要求 **Claude 自动录入** + **每次分析都保留历史** + **能查看历史**。DeepSeek/机构同理。

### 问题（5个）

| # | 问题 | 来源 |
|---|------|------|
| 1 | 前端没调 3 个 analyze API | V2 识别 |
| 2 | 没有多源对比框架 | V2 识别 |
| 3 | **没有分析历史**——每次分析完结果就丢了 | V4 新增 |
| 4 | **Claude 只有手动粘贴**——应该自动录入 | V4 新增 |
| 5 | **机构观点没存档**——无法看"上周机构怎么说的" | V4 新增 |

### 核心架构：统一分析存档系统

所有来源的分析统一存到 `DATA_DIR/{userId}/analysis_history/`，格式一致，可查可比可追溯。

**存档目录结构**：
```
data/{userId}/analysis_history/
  ├── 20260415_1530_deepseek_stock.json   ← DeepSeek 股票分析（自动存）
  ├── 20260415_1545_claude_full.json      ← Claude 全量分析（自动推送）
  ├── 20260415_1600_broker_consensus.json ← 机构共识快照（定时存）
  └── ...（按日期排序，90天自动清理）
```

**统一记录格式（JSON）**：
```json
{
  "id": "20260415_1530_deepseek_stock",
  "source": "deepseek",
  "source_label": "DeepSeek V3",
  "type": "stock",
  "analysis": "分析正文...",
  "direction": "看多",
  "confidence": 72,
  "market_snapshot": {
    "sh_index": 3156,
    "oil_brent": 102,
    "gold": 2380,
    "fear_greed": 55
  },
  "created_at": "2026-04-15T15:30:00",
  "userId": "LeiJiang",
  "metadata": {}
}
```

### 后端改动

**新建 `backend/services/analysis_history.py`**：

| 函数 | 职责 |
|------|------|
| `save_analysis(userId, source, type, text, ...)` | 存一条分析记录 + 自动拍市场快照 |
| `get_analysis_history(userId, source?, type?, days=30)` | 查历史列表（分页/筛选） |
| `get_analysis_detail(userId, record_id)` | 查单条完整内容 |
| `get_latest_by_source(userId)` | 取各来源最新记录（对比视图用） |
| `_take_market_snapshot()` | 存档时自动保存当时的沪指/油价/黄金/恐贪 |
| `_cleanup_old_records(days=90)` | 自动清理过期记录 |

**改造现有 3 个 analyze 函数**（return 前自动存档）：

```python
# analyze_stock_holdings / analyze_fund_holdings / agent_analyze
from services.analysis_history import save_analysis
if result.get("source") == "ai":
    save_analysis(uid, "deepseek", "DeepSeek V3", "stock", result["analysis"])
```

**新增 5 个 API 端点**：

| 端点 | 方法 | 功能 | userId |
|------|------|------|--------|
| `/api/analysis/history` | GET | 查询分析历史列表 | ✅ 查询参数 |
| `/api/analysis/detail/{record_id}` | GET | 获取单条分析完整内容 | ✅ |
| `/api/analysis/latest` | GET | 各来源最新分析（对比视图） | ✅ |
| `/api/analysis/compare` | POST | 多源对比（可选强制刷新DS） | ✅ request body |
| `/api/analysis/external` | POST | 接收外部分析（Claude自动推送入口） | ✅ |

**`/api/analysis/external`（Claude 自动录入核心端点）**：

```python
@app.post("/api/analysis/external")
def receive_external_analysis(req: dict):
    uid = req.get("userId", "default")
    text = req.get("analysis", "")
    source = req.get("source", "claude")
    source_label = req.get("sourceLabel", "Claude")
    analysis_type = req.get("type", "full")
    direction = req.get("direction", "unknown")
    
    from services.analysis_history import save_analysis
    result = save_analysis(
        userId=uid, source=source, source_label=source_label,
        analysis_type=analysis_type, analysis_text=text,
        direction=direction,
    )
    return result
```

### Claude 录入方式（V4：自动优先）

| 方式 | 优先级 | 实现 | 阶段 |
|------|--------|------|------|
| **① WorkBuddy 自动推送** | **最高** | Claude 分析完自动 POST `/api/analysis/external`——你在 WorkBuddy 说"帮我分析持仓"，分析报告给你的同时自动推送到后端存档 | Phase 5 |
| ② 手动粘贴（兜底） | 并行做 | 前端"粘贴外部分析"按钮 → POST 同一个端点 | Phase 5 |
| ③ API Key 直调 | 后续 | 后端直接调 Claude API，不经过 WorkBuddy | ROI 低先不做 |

**WorkBuddy 自动推送完整链路**：
```
1. 你在 WorkBuddy 说"帮我分析一下持仓"
2. Claude 用 finance-data 拉实时数据 → 做分析 → 输出给你看
3. 同时自动调 POST http://150.158.47.189:8000/api/analysis/external
   body: {userId: "LeiJiang", source: "claude", analysis: "...", type: "full"}
4. 后端 analysis_history.save_analysis() 存档
5. 你打开钱袋子 App → "分析历史" → 能看到刚才 Claude 的分析
6. 点"对比" → 同时看 DeepSeek + Claude + 机构的最新分析
```

### 前端改动

**① 新增"分析历史"Tab**（Pro only）：

```
┌────────────────────────────────────────────────┐
│  分析历史                          [筛选]       │
├────────────────────────────────────────────────┤
│ 来源: [全部] [DeepSeek] [Claude] [机构]        │
│ 类型: [全部] [股票] [基金] [全量]              │
├────────────────────────────────────────────────┤
│ 2026-04-15                                     │
│ ┌──────────────────────────────────────────┐   │
│ │ DeepSeek 股票分析  15:30                 │   │
│ │ 方向: 看多 | 置信度: 72%                │   │
│ │ "整体持仓估值合理，技术面偏多..."        │   │
│ │                        [查看全文] [对比]  │   │
│ └──────────────────────────────────────────┘   │
│ ┌──────────────────────────────────────────┐   │
│ │ Claude 全量分析  15:45                   │   │
│ │ 方向: 谨慎                              │   │
│ │ "地缘风险是当前最大不确定性..."           │   │
│ │                        [查看全文] [对比]  │   │
│ └──────────────────────────────────────────┘   │
│ ┌──────────────────────────────────────────┐   │
│ │ 机构共识  16:00                          │   │
│ │ 方向: 谨慎乐观（4看多/1看空/2中性）      │   │
│ │                        [查看全文] [对比]  │   │
│ └──────────────────────────────────────────┘   │
│                                                │
│ 2026-04-14                                     │
│ └ ...                                          │
│                   [加载更多]                    │
└────────────────────────────────────────────────┘
```

**② 对比视图**（点"对比"触发）：

```
┌────────────────────────────────────────────────┐
│  多源分析对比                  [重新分析(DS)]   │
├────────────────────────────────────────────────┤
│  [DeepSeek] | [Claude] | [机构]                │
│  4/15 15:30   4/15 15:45  4/15 16:00           │
│ ──────────────────────────────────────────────  │
│  （当前 Tab 的分析全文，可滚动）               │
├────────────────────────────────────────────────┤
│  分歧汇总                                       │
│  DeepSeek: 看多 72%（估值+技术双支撑）         │
│  Claude:   谨慎（地缘风险尚未消化）             │
│  机构:     谨慎乐观（先抑后扬）                 │
│  关键分歧：地缘风险的定价程度                    │
├────────────────────────────────────────────────┤
│  分析时市场快照                                  │
│  沪指:3156 | 油价:$102 | 黄金:$2380 | 恐贪:55  │
└────────────────────────────────────────────────┘
```

**③ 持仓页新增入口**：
- 股票持仓页 → "请求深度分析"按钮（触发 DS analyze + 自动存档）
- 基金持仓页 → 同上
- 两个页面都有"粘贴外部分析"兜底按钮

**④ 首页 Dashboard**：
- 新增"最近分析"卡片 → 显示最近一次各来源分析的时间和方向
- 点击跳转到分析历史页

### 定时任务自动存档

| 已有定时任务 | V6 改造 |
|-------------|---------|
| `stock_monitor_cron.py` 15:30 收盘复盘 | 复盘调 `agent_analyze()` → 结果自动存档 |
| `cache_warmer.py` 9:15 早盘预热 | 顺便拉一次 `broker_consensus` → 存档 |
| **新增** | 每日 16:00 自动调 analyze_stock + analyze_fund → 双双存档 |

---

## 10. 模块关联矩阵

```
         A(地缘)  B(原油)  C(资金)  D(行业)  E(研报)  F(情景)  G(历史)
A(地缘)    —      A→B     ·       A→D      ·       A→F      ·
B(原油)   B→A      —      ·       B→D      ·       B→F      ·
C(资金)    ·       ·       —      C→D      ·       C→F      ·
D(行业)    ·       ·       ·       —       D↔E     D→F      ·
E(研报)    ·       ·       ·      D↔E       —      E→F      E→G
F(情景)    ·       ·       ·       ·        ·       —       F→G
G(历史)    ·       ·       ·       ·        ·       ·        —
```

**关键联动链**：

1. **地缘+油价 → 行业 → 情景**：中东冲突→油价飙升→能源行业暴涨→其他行业承压→自动触发"能源危机"情景分析
2. **资金+行业 → 信号**：北向流入+行业轮动到科技→综合信号偏多
3. **研报+行业 → 配置**：机构共识推荐半导体+AI检测到半导体行业TOP3→配置建议加权科技ETF
4. **所有模块 → 市场上下文**：`_build_market_context()` 汇聚所有模块一次性注入 DeepSeek
5. **所有分析 → 历史存档**：DS/Claude/机构的每次分析都自动存档到模块G

---

## 11. 各场景下的使用方式

### 场景1：用户打开首页

```
首页Dashboard:
  ├── 恐惧贪婪指数（已有）
  ├── 估值百分位（已有）
  ├── [NEW] 地缘风险指数: 75/100 (中东局势紧张)
  ├── [NEW] 油价: 布伦特 $102 (+1.5%) [警戒]
  ├── [NEW] 行业热点: 能源+4.2% / 军工+3.1% / 银行+1.5%
  ├── [NEW] 机构共识: 谨慎乐观 (4看多/1看空/2中性)
  ├── [NEW] 最近分析: DS 15:30 看多 | Claude 15:45 谨慎
  └── 今日关注（DS生成，现在有地缘+油价+行业数据，质量大幅提升）
```

### 场景2：用户问"现在适合入场吗"

```
_build_market_context() 注入:
  ├── 已有: 估值45%/恐贪55/RSI中性/MACD金叉
  ├── [NEW] 地缘: 中东冲突持续，severity=4/5，油价102美元
  ├── [NEW] 行业: 防御板块领涨(银行+3.9%)，进攻板块回调(科技-2.1%)
  ├── [NEW] 机构: 4家看先抑后扬，建议4月下旬布局
  └── DeepSeek 现在能说:
      "地缘风险是当前最大不确定性，建议等油价企稳后分批建仓"
      （之前只能说"估值合理，技术面中性"）
```

### 场景3：10分钟盯盘 cron 发现地缘事件

```
stock_monitor_cron.py:
  1. 扫描持仓（已有）
  2. [NEW] 检查地缘事件 → severity=5（重大军事冲突）
  3. [NEW] 检查油价 → 布伦特突破110
  4. [NEW] 检查持仓行业 → 用户持有航空股
  5. → 企微推送: "重大地缘风险! 油价110美元，你持有的XX航空可能承压"
```

### 场景4：收盘复盘

```
stock_monitor_cron.py --close:
  steward.review() 的 DecisionContext 现在包含:
  ├── [NEW] ctx.geopolitical_risk = {severity:4, events:[...]}
  ├── [NEW] ctx.crude_oil = {brent:102, alert:"warning"}
  ├── [NEW] ctx.sector_rotation = {pattern:"防御转进攻", hot:["银行","能源"]}
  └── R1 复盘: "今日市场受中东局势缓和预期影响，能源板块回落..."
  
  → 复盘结果自动存档到 analysis_history（模块G）
```

### 场景5：用户问"如果中东停火呢"

```
规则引擎匹配 "如果" → 调 scenario_engine:
  1. 加载 PRESET_SCENARIOS["ceasefire"]
  2. 获取当前状态: 油价102/地缘severity=4/能源行业TOP3
  3. R1 推演: 停火→油价回落到75-80→能源板块回调15-20%→消费航空反弹
  4. 对用户持仓的影响: 如持有能源ETF→建议减持30%
  5. 输出完整情景分析报告 → 自动存档到 analysis_history
```

### 场景6：WorkBuddy 分析完自动存档

```
用户在 WorkBuddy 说"帮我分析持仓":
  1. Claude 拉数据 → 分析 → 输出报告给用户
  2. 同时 POST http://150.158.47.189:8000/api/analysis/external
  3. 后端存档 → 用户打开 App "分析历史" Tab 可查看
  4. 点"对比" → 同时看 DeepSeek + Claude + 机构
```

---

## 12. 前后端对应审查结果

> 基于完整扫描 app.js（2890行/82个API端点）+ main.py（3262行/161个路由）+ routers/（13个端点）。

### 铁律#18 违规清单

**🔴 严重违规（有价值功能，前端 0 调用）— 9 个**：

| 后端 API | 功能 | V6 修复方案 |
|---------|------|------------|
| `POST /api/stock-holdings/analyze` | 股票深度分析 | **模块G：持仓页"请求深度分析"按钮** |
| `POST /api/fund-holdings/analyze` | 基金深度分析 | **模块G：持仓页"请求深度分析"按钮** |
| `POST /api/agent/analyze` | 全量AI分析 | **模块G：对比视图"重新分析"** |
| `POST /api/timing` | 择时信号 | 接入信号Tab |
| `POST /api/smart-dca` | 智能定投 | 接入管家Tab |
| `POST /api/take-profit` | 止盈建议 | 接入管家Tab |
| `GET /api/news/deep-impact` | 新闻深度影响 | 嵌入新闻详情 |
| `GET /api/news/risk-assess` | 新闻风险评估 | 嵌入新闻详情 |
| `GET /api/daily-signal/interpret` | 信号解读 | 注入信号Tab |

**🟡 中等违规 — 9 个**（独立端点/ML/batch/agent子端点等）

**🟢 低风险 — 9 个**（管理/调试/内部API，不需前端）

**⚠️ 前端Bug**：`POST /portfolio/transaction/delete` (app.js:L1026) vs 后端 `DELETE /api/portfolio/transaction/{tx_id}` — HTTP方法+路径都不匹配。

---

## 13. Simple/Pro 模式分配

| 功能 | Simple | Pro | 理由 |
|------|--------|-----|------|
| 首页 地缘风险指数卡片 | ✅ | ✅ | 所有人需看到重大风险 |
| 首页 油价卡片 | ✅ | ✅ | 直观信息 |
| 首页 行业热点卡片 | ✅ TOP3 | ✅ TOP5+详情 | Simple精简 |
| 首页 机构共识卡片 | ✅ 一句话 | ✅ 完整 | Simple精简 |
| 首页 最近分析卡片 | ❌ | ✅ | Pro功能 |
| 油价>100 警戒标红 | ✅ | ✅ | 风险提示 |
| 行业轮动 Tab | ❌ | ✅ 新增 | 专业功能 |
| 情景分析 Tab | ❌ | ✅ 新增 | 专业功能 |
| 分析历史 Tab | ❌ | ✅ 新增 | 专业功能 |
| 全量分析对比 | ❌ | ✅ 持仓页入口 | 只有Pro用 |
| 粘贴外部分析 | ❌ | ✅ | 只有Pro用 |
| AI聊天增强 | ✅ 自动受益 | ✅ | context注入，不走Tab |
| 企微推送 | ✅ | ✅ | 不区分模式 |

**Simple 白名单不需修改**——新 Tab 只加到 `all` 数组：
```javascript
// all 数组新增：
['sector','行业'], ['scenario','情景'], ['compare','对比'], ['analysis-history','分析']
// simple 数组保持不变：['overview','news','policy','doctor','steward']
```

---

## 14. 多账号（铁律#19）审查

**结论：基本合格，1 处 Gap**

- ✅ 前端 userId 已统一（`getProfileId()` 30+处 / `getUserId()` 18处 / `getProfileParam()` 8处，无冲突）
- ✅ 后端 ~60+ 路由有 userId 参数，~65 个公共路由不需要
- ✅ 模块 A-E（公共市场数据）+ 模块 G（已有userId）覆盖正常
- ⚠️ **模块F Gap**：`GET /api/scenario/{id}` 设计中没有 userId 参数，但内部会调 `analyze_stock_holdings(userId)` — **需改为 `GET /api/scenario/{id}?userId=xxx`**

---

## 15. 铁律检查点矩阵

### 每条铁律在 V6 中的绑定关系

| 铁律# | 铁律内容 | V6 中何时触发 | 检查方法 |
|:---:|---------|-------------|---------|
| 1 | 绝不用正则做批量重构 | 修改 main.py、signal.py 时 | 所有改动用 replace_in_file，禁止脚本 |
| 2 | 改代码前确认可回滚 | **每个 Phase 开始前** | 列文件清单 → git commit checkpoint |
| 3 | 改完一个文件立即验证 | **每改一个文件后** | read_file 重读 + import 语法检查 |
| 4 | 超过2轮修不好就停 | 遇到接口不可用时 | 2 次降级失败 → 停下来标记降级 |
| 5 | linter ≠ 编译器 | 每个 Phase 完成后 | 部署服务器 → 检查日志 → 调真实 API |
| 6 | 涉及技术深度先查参考 | 行业轮动算法、情景模型 | 先搜业界成熟方案 |
| 7 | 方案要有出处 | 油价阈值、行业分类标准 | 引用 IEA/Bloomberg/申万 |
| 8 | 最小可用版本先交 | 每个 Phase 定义 | Phase 1 数据获取 → 验证 → Phase 2 分析 |
| 9 | 超出能力范围就说 | 券商研报爬取 | AKShare 没接口就直说 |
| 10 | 记忆 ≠ 事实 | AKShare 接口名 | 每次调接口前先验证确实存在 |
| 12 | 改完必须只读验证 | 每改一个文件后 | read_file + 检查关联 import |
| 13 | "不能"=穷尽所有选项 | 数据源降级 | 北向3级/ETF4级/原油2级降级 |
| **18** | **后端做了前端必须接** | **每个新 API** | 写后端后同 Phase 写前端，grep 确认 |
| **19** | 多ID系统统一入口 | 用户身份相关 | 统一用 userId 参数 |
| **20** | 推送先查格式兼容性 | 地缘事件推送企微 | 用 text_card 格式 |

### 实施护栏（硬性规则）

```
✅ 开始 Phase N 前：
   1. git commit -m "checkpoint before phase N" （你确认）
   2. 列出本 Phase 要改的文件清单
   3. 你确认"可以开始"

✅ 每改一个文件后：
   1. read_file 重读确认写入正确
   2. python -c "import ..." 语法验证

✅ Phase N 完成后：
   1. 部署到服务器
   2. curl 调真实 API 验证返回格式
   3. 如果有前端改动 → 浏览器打开确认能看到
   4. git commit + push

❌ 禁止：
   - 攒多个文件改动最后一起验证（违反铁律#3）
   - 写了后端 API 但不写前端调用（违反铁律#18）
   - 用 PowerShell 脚本做代码批量替换（违反铁律#1）
   - 接口报错超过 2 轮还在硬修（违反铁律#4）

✅ 代码组织约束（防止 main.py 膨胀）：
   - 新 API 端点必须放 routers/ 目录（如 routers/scenario.py、routers/analysis_history.py），
     main.py 只加一行 app.include_router()
   - _build_market_context() 只做编排循环，具体数据拉取逻辑在各模块的 enrich(ctx) 方法里
   - 每个 Phase 结束时检查 main.py 行数，增长超过 50 行必须拆
```

---

## 16. 逐 Phase 验证清单

### Phase 1 验证：P0 核心数据层

| 步骤 | 验什么 | 怎么验 | 通过标准 | 失败处理 |
|------|--------|--------|---------|---------|
| 1.1 | geopolitical.py 关键词匹配 | `python -c "from services.geopolitical import get_geopolitical_events; print(...)"` | 返回 dict 且有 events 列表 | 检查 AKShare 新闻接口 |
| 1.2 | 原油 SC0/BZ 数据 | `python -c "from services.market_factors import get_crude_oil_price; print(...)"` | `available=True`, price > 0 | 降级到只用 SC0 |
| 1.3 | market_context 注入 | `curl /api/decision-data` 检查文本 | 包含"地缘"和"原油" | 检查注入逻辑 |
| 1.4 | config.py 阈值 | `python -c "from config import *; print(...)"` | 输出 80 100 120 | 语法检查 |
| 1.5 | 线上部署 | `systemctl restart + journalctl + curl` | 200 + crude_oil 字段 | 检查 requirements |

**端到端验证脚本**：
```bash
curl -s http://localhost:8000/api/decision-data | python3 -c "
import json,sys; d=json.load(sys.stdin); ctx=str(d)
assert '地缘' in ctx or 'geopolitical' in ctx, '缺少地缘数据'
assert '原油' in ctx or 'crude' in ctx or 'oil' in ctx, '缺少原油数据'
print('Phase 1 验证通过')
"
```

### Phase 2 验证：P0 分析层集成

| 步骤 | 验什么 | 怎么验 | 通过标准 |
|------|--------|--------|---------|
| 2.1 | DS 严重性评估 | 构造假新闻 → `assess_event_severity()` | 返回 severity(1-5) |
| 2.2 | signal 第13维 | `/api/daily-signal` | factors 含"地缘面" |
| 2.3 | regime 地缘加权 | 模拟 severity=4 | 返回 high_vol_bear |
| 2.4 | pipeline 风控 | 模拟 severity=5 | 含"风控否决" |
| 2.5 | NEWS_IMPACT_MAP | 输入地缘新闻 | impacts 有地缘影响 |
| 2.6 | cron 预热 | `python cache_warmer.py --once morning` | 日志显示 geo 预热成功 |
| 2.7 | **AI聊天能提到地缘** | 问"最大风险是什么" | 回复含地缘/油价 |

**端到端验证**：
```bash
curl -X POST http://localhost:8000/api/chat \
  -d '{"message":"目前市场最大的风险是什么？","userId":"default"}' \
  | python3 -c "
import json,sys; d=json.load(sys.stdin); r=d.get('reply','')
found=[k for k in ['地缘','冲突','油价','能源','中东'] if k in r]
print(f'关键词: {found}')
if len(found)>=2: print('Phase 2 验证通过')
else: print('AI 可能还没看到地缘数据')
"
```

### Phase 3 验证：P1 行业+资金修复

| 步骤 | 验什么 | 通过标准 |
|------|--------|---------|
| 3.1 | sector_rotation 31行业 | ≥20 个行业有数据 |
| 3.2 | 北向资金修复 | `available=True` + 有净流入数据 |
| 3.3 | ETF份额变化 | 有 share_change 字段 |
| 3.4 | market_context 行业段 | 聊天能说具体行业 |
| 3.5 | 首页行业卡片 | 浏览器看到卡片（**铁律#18**） |

### Phase 4 验证：P2 研报+情景

| 步骤 | 验什么 | 通过标准 |
|------|--------|---------|
| 4.1 | broker_research | consensus 字段返回正常 |
| 4.2 | scenario_engine | 4个预设都能跑通 |
| 4.3 | 情景 API 路由 | `/api/scenarios` 返回4个 |
| 4.4 | 企微快捷指令 | 企微收到分析推送 |
| 4.5 | 前端情景Tab | 4个预设+自定义输入框（**铁律#18**） |
| 4.6 | 全量验证 | "如果中东停火对我持仓影响"→含油价/行业/持仓 |

### Phase 5（模块G）验证

| 步骤 | 验什么 | 通过标准 |
|------|--------|---------|
| G.1 | 存档模块存/取 | save + get 返回列表含刚存记录 |
| G.2 | DS自动存档 | analyze 后目录下有 JSON |
| G.3 | 外部录入端点 | POST → ok=True + 文件存在 |
| G.4 | 历史查询+筛选 | records 列表按时间倒序 |
| G.5 | 对比视图 | sources ≥1 项 |
| G.6 | 前端历史Tab | 按日期分组显示卡片 |
| G.7 | 前端对比 | 3个Tab + 分歧汇总 |
| G.8 | **铁律#18** | grep app.js 含 ≥5 处 analysis/ 调用 |
| G.9 | 90天清理 | 91天前文件被删 |

---

## 17. 实施计划（5 Phase / 8-12天）

### Phase 1（1-2天）：🔴 P0 核心数据层

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 1.0 | checkpoint | git | 5min | #2 | git status 干净 |
| 1.1 | 新建 geopolitical.py | 新建 | 2h | #6 | import + 调用测试 |
| 1.2 | 扩展 get_commodity_prices() | market_factors.py | 1h | #3 #10 | 有原油数据 |
| 1.3 | _build_market_context 加地缘+原油 | main.py | 0.5h | #3 #12 | /api/decision-data |
| 1.4 | config.py 油价阈值 | config.py | 0.2h | #7 | print 验证 |
| 1.5 | 部署+线上验证 | deploy | 0.5h | #5 | 端到端脚本 |

### Phase 2（1-2天）：🔴 P0 分析层集成

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 2.0 | checkpoint | git | 5min | #2 | 确认 |
| 2.1 | geopolitical.py 加 DS 评估 | geopolitical.py | 1.5h | #4 | 假新闻→评估 |
| 2.2 | signal.py 第13维 | signal.py | 1h | #1 #3 | /api/daily-signal |
| 2.3 | regime_engine 地缘加权 | regime_engine.py | 0.5h | #3 | severity=4 测试 |
| 2.4 | pipeline 风控加地缘 | pipeline_runner.py | 0.5h | #3 | severity=5 测试 |
| 2.5 | NEWS_IMPACT_MAP 扩展 | news_data.py | 0.5h | #3 | 关键词测试 |
| 2.6 | cron 预热+推送 | scripts/ | 1h | #20 | 企微格式 |
| 2.7 | 部署+验证 | deploy | 0.5h | #5 | 端到端脚本 |

### Phase 3（1-2天）：🟡 P1 行业+资金修复

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 3.0 | checkpoint | git | 5min | #2 | 确认 |
| 3.1 | 新建 sector_rotation.py | 新建 | 2h | #6 | ≥20行业 |
| 3.2 | 北向资金多级降级 | factor_data.py | 1.5h | #4 #13 | 3级逐个验证 |
| 3.3 | ETF份额变化 | market_factors.py | 1h | #3 | share_change |
| 3.4 | market_context 加行业 | main.py | 0.5h | #18 | 聊天说具体行业 |
| 3.5 | 前端行业热点卡片 | app.js | 1h | #18 | 浏览器看到 |
| 3.6 | 部署+验证 | deploy | 0.5h | #5 | Phase 3 清单 |

### Phase 4（2-3天）：🟢 P2 研报+情景

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 4.0 | checkpoint | git | 5min | #2 | 确认 |
| 4.1 | 新建 broker_research.py | 新建 | 2h | #10 | 共识返回正常 |
| 4.2 | 新建 scenario_engine.py | 新建 | 3h | #6 #4 | 4预设跑通 |
| 4.3 | main.py 加情景 API | main.py | 0.5h | #3 #12 | /api/scenarios 200 |
| 4.4 | 企微"情景 XX"指令 | wxwork.py | 0.5h | #20 | 收到推送 |
| 4.5 | 前端情景 Tab | app.js | 2h | **#18** | 浏览器能用 |
| 4.6 | 部署+全量验证 | deploy | 0.5h | #5 | Phase 4 清单 |

### Phase 5（2天）：模块G 分析历史+自动录入

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 5.0 | checkpoint | git | 5min | #2 | 确认 |
| 5.1 | 新建 analysis_history.py | 新建 | 1.5h | #3 #12 | save/get 测试 |
| 5.2 | 改造 3 个 analyze 自动存档 | main.py | 0.5h | #3 | 调 analyze → 有文件 |
| 5.3 | 5 个 API 端点 | main.py | 1h | #3 #12 | curl 逐个验证 |
| 5.4 | 前端"分析历史" Tab | app.js | 2h | **#18** | 浏览器看到列表 |
| 5.5 | 前端"对比视图"+分歧汇总 | app.js | 1.5h | **#18** | Tab切换+汇总 |
| 5.6 | 持仓页入口按钮 | app.js | 1h | **#18** | 两个入口能用 |
| 5.7 | WorkBuddy 自动推送 | 配置 | 0.5h | — | 分析完后端自动收到 |
| 5.8 | cron 改造定时存档 | scripts/ | 0.5h | #3 | 收盘后有新存档 |
| 5.9 | 部署+验证 | deploy | 0.5h | #5 | G.1-G.9 全部通过 |

### 总工时

| Phase | 内容 | 工时 |
|-------|------|------|
| Phase 1 | P0 数据层 | 1-2天 |
| Phase 2 | P0 分析层 | 1-2天 |
| Phase 3 | P1 行业+资金 | 1-2天 |
| Phase 4 | P2 研报+情景 | 2-3天 |
| Phase 5 | 分析历史+自动录入 | 2天 |
| **总计** | **7 个模块** | **8-12天** |

---

## 18. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| AKShare 原油接口不可用 | 中 | 模块B降级 | 降级：从新闻提取油价/用国内SC0替代 |
| 地缘新闻被关键词过滤遗漏 | 中 | 模块A漏报 | DS定期扫描全量新闻做补充 |
| 行业数据API变更 | 低 | 模块D不可用 | 多接口降级链 |
| LLM调用成本增加 | 中 | 超日限50次 | severity≥3才调DS，其余纯规则 |
| 服务器2C2G内存不足 | 低 | 行业数据量大 | 只缓存TOP10+BOTTOM10 |
| 券商研报数据源不稳定 | 中 | 模块E降级 | WebSearch兜底 |
| analysis_history 文件膨胀 | 低 | 磁盘满 | 90天自动清理 |

---

## 19. 预期效果

| 维度 | 改进前（3.0/5） | 改进后预期 |
|------|-----------------|-----------|
| 地缘政治 | F（完全空白） | B+（能识别+评级+推送） |
| 能源/大宗 | F（没有原油） | A-（油价+多品种+影响评估） |
| 资金流向 | D（数据断了） | B（修复+多级降级） |
| 行业深度 | D（只有指数） | B+（31行业轮动+热点） |
| 机构观点 | F（没有） | B（每日共识摘要） |
| 情景分析 | F（没有） | A-（预设+自定义+R1推理） |
| 分析存档 | F（每次丢失） | A（统一存档+90天+对比） |
| Claude录入 | F（手动粘贴） | A（WorkBuddy自动推送） |
| **综合评分** | **3.0/5** | **4.2~4.5/5** |

> 从"快速量化参考"升级为"准专业级 AI 投研助手"。
> 
> **最大的三个改变**：
> 1. **AI 终于能看到"房间里的大象"** — 地缘政治和能源危机不再是盲区
> 2. **每次分析都有据可查** — 不再"用完即弃"，支持历史回溯和多源交叉验证
> 3. **Claude 从"外人"变成"住家" ** — 自动推送存档，与 DeepSeek/机构形成三足鼎立的分析矩阵

---

*文档编写: 2026-04-15*
*版本历程: V1(17:30 六模块初版) → V2(18:30 模块G+铁律矩阵+验证方案) → V3(18:41 前后端审查+Simple/Pro+多账号) → V4(18:46 分析历史+Claude自动录入) → V4-Final(19:10 整合完整版)*
*基于完整代码库分析: main.py 3262行 + 40+ services + app.js 312KB (2890行/82个API)*
*配套报告: `双AI投资分析对比报告.md` / `AI分析vs专业机构对比报告.md`*
