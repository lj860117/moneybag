# 钱袋子 V4 终极方案 — AI 自主编排架构版

> **版本**: Final v3.0 | **日期**: 2026-04-14 18:30
> **升级**: v2.2 → v3.0 核心变化 = **3 底座架构**（ModuleRegistry + DecisionContext + PipelineRunner）
> **设计哲学**: AI 从"被人编排的工具"→"自主编排的主体"。44 个 service **一行不删**，只加 `MODULE_META` + `enrich()` 适配层。
> **基础**: 44 service 完整源码审计 + 6 份外部方案融合 + 多账号隔离 + 铁律合规
> **本文档是唯一权威版**，替代之前所有版本。

---

## 〇、全量代码审计结论

| 层 | 文件 | 行数 | 备注 |
|---|:---:|:---:|---|
| main.py | 1 | 3,039 | 142+ 路由 |
| services/ | 44 | ~13,200 | 含 1 死代码+1 假串联 |
| routers/ | 2 | 445 | profiles+wxwork |
| scripts/ | 1 | 172 | cron |
| prompts/ | 8 | ~156 | 7 skill |
| app.js | 1 | 2,418 | 152 函数 |
| **合计** | **58** | **~19,830** | |

**模块串联**: 5 个孤岛(ai_predictor/genetic_factor/rl_position/monte_carlo/portfolio_optimizer) + 1 假串联(agent_engine) + 1 死代码(chat.py)。v3.0 通过 MODULE_META+enrich() 全部激活。

---

## 一、v3.0 核心升级 — 3 底座架构

### 一.1 v2.2 vs v3.0

| 维度 | v2.2 手动编排 | v3.0 3底座 |
|---|---|---|
| AI发现能力 | steward硬编码调5模块 | AI查Registry看全部44模块描述，自己选 |
| 上下文完整性 | 手动拼prompt易漏 | DecisionContext每层自动累积，`to_llm_context()`组装 |
| 策略灵活性 | 固定6层顺序 | Pipeline按Regime动态选(default/fast/cautious) |
| 扩展性 | 加模块改steward | 新模块只加META+enrich，O(1)扩展 |
| 人可看 | 日志分散 | `to_user_response()`一个对象看全部 |
| 成本控制 | 门控写死steward | PIPELINE_FAST完全跳过LLM |

### 一.2 架构总览

```
            ModuleRegistry (注册制)
    ┌────┐ ┌────┐ ┌────┐     ┌────┐
    │模块A│ │模块B│ │模块C│ ... │模块N│   44个模块各有 MODULE_META + enrich()
    └──┬─┘ └──┬─┘ └──┬─┘     └──┬─┘
       └──────┴──────┴──────────┘
                    │ discover() → AI选模块
                    ▼
           DecisionContext (@dataclass)
    Layer0(用户数据) → Layer1(Regime) → Layer2(模块结果)
    → Layer3(门控) → Layer4(赔率EV) → Layer5(风控) → Layer6(输出) → Layer7(EMA)
    三视图: to_llm_context() / to_user_response() / to_judgment_record()
                    │
                    ▼
           PipelineRunner (管线引擎)
    PIPELINE_DEFAULT  = [regime→modules→gate→payoff→risk→output→ema] 日常
    PIPELINE_FAST     = [regime→modules→risk→output]                 紧急
    PIPELINE_CAUTIOUS = [regime→modules→gate→payoff→doctor→risk→output→ema] 熊市
```

---

## 二、底座 #1 — ModuleRegistry

### 二.1 核心代码 (~150行)

```python
# services/module_registry.py 🆕
class ModuleRegistry:
    _modules: dict[str, dict] = {}  # name → {meta, enrich_fn, module_ref}
    
    def _auto_discover(self):
        """启动时扫描 services/，找到有 MODULE_META 的模块自动注册"""
        for module in pkgutil.iter_modules([services_dir]):
            mod = importlib.import_module(f"services.{module.name}")
            if hasattr(mod, "MODULE_META"):
                self._modules[mod.MODULE_META["name"]] = {
                    "meta": mod.MODULE_META,
                    "enrich": getattr(mod, "enrich", None),
                }
    
    def discover(self, scope=None, cost=None, tag=None) -> list[dict]:
        """AI查询可用模块 — 支持scope/cost/tag筛选"""
    
    def to_llm_catalog(self) -> str:
        """生成给DeepSeek看的模块目录"""
```

### 二.2 MODULE_META 规范

每个 service 文件头部加（**不删现有代码**）：

```python
MODULE_META = {
    "name": "模块名",           # snake_case
    "scope": "public|private",  # public=市场数据 / private=需user_id
    "input": ["参数列表"],       # enrich()从ctx读的字段
    "output": "输出字段",        # enrich()写入ctx的字段
    "cost": "cpu|llm_light|llm_heavy",
    "tags": ["功能标签"],
    "description": "一句话描述",
    "layer": "data|analysis|risk|output",
    "priority": 1,
}
```

### 二.3 全部 42 模块 MODULE_META（排除 chat.py 死代码 + __init__.py）

#### 5 个孤岛模块

| 模块 | scope | cost | layer | tags | description |
|---|---|---|---|---|---|
| ai_predictor | public | cpu | analysis | 预测,ML | MLP+GBM双模型，40+特征，N日涨跌概率 |
| genetic_factor | public | cpu | analysis | 因子挖掘,遗传 | 表达式树+遗传进化200×30代，发现Alpha因子 |
| rl_position | public | cpu | analysis | 仓位,RL | Q-Learning离散空间，加/减/持建议 |
| monte_carlo | public | cpu | analysis | 风险,MC,VaR | GBM 5000次，P10-P90+VaR/CVaR+盈利概率 |
| portfolio_optimizer | **private** | cpu | analysis | 组合优化,MVO | 5种方法(MVO/MinVol/CVaR/HRP/Equal) |

#### 已串联核心模块

| 模块 | scope | cost | layer | tags |
|---|---|---|---|---|
| stock_screen | public | llm_light | analysis | 选股,30因子,动态权重 |
| signal | public | cpu | analysis | 信号,12维,技术指标 |
| ds_enhance | public | llm_light | output | DS增强,AI点评 |
| risk | **private** | cpu | risk | 风控,HHI,回撤 |
| stock_monitor | **private** | cpu | data | 盯盘,异动,纪律 |
| fund_monitor | **private** | cpu | data | 基金盯盘,估值 |
| agent_memory | **private** | cpu | data | 记忆,偏好 |
| agent_engine | **private** | llm_heavy | analysis | ⚠️假串联→重写 |
| portfolio | **private** | llm_light | output | 配置,投顾 |
| holding_intelligence | **private** | cpu | data | 个股情报,资金 |
| portfolio_calc | **private** | cpu | data | 交易流水 |
| portfolio_overview | **private** | cpu | data | 组合总览,偏离 |
| unified_networth | **private** | cpu | data | 净资产 |
| persistence | **private** | cpu | data | 用户IO |

#### 数据层模块

| 模块 | scope | cost | layer | tags |
|---|---|---|---|---|
| alt_data | public | cpu | data | 另类,北向,融资 |
| factor_data | public | cpu | data | 因子,财务 |
| factor_ic | public | cpu | analysis | IC检验,Barra |
| global_market | public | cpu | data | 全球,美股,外汇 |
| news_data | public | cpu | data | 新闻,情绪 |
| policy_data | public | llm_light | data | 政策,5主题 |
| macro_data | public | cpu | data | CPI,PMI,M2 |
| macro_extended | public | cpu | data | 美林时钟,LPR |
| macro_v8 | public | cpu | data | GDP,龙虎榜 |
| market_data | public | cpu | data | 恐贪,估值 |
| market_factors | public | cpu | data | 大宗,解禁,ETF |
| stock_data_provider | public | cpu | data | A股多源 |
| tushare_data | public | cpu | data | Tushare,PE,PB |
| technical | public | cpu | data | RSI,MACD,布林 |

#### 功能模块

| 模块 | scope | cost | layer | tags |
|---|---|---|---|---|
| ml_stock_screen | public | cpu | analysis | LightGBM选股 |
| llm_factor_gen | public | llm_heavy | analysis | LLM因子,Alpha-GPT |
| backtest_engine | public | cpu | analysis | 回测,夏普 |
| backtest | public | cpu | analysis | 策略回测 |
| wxwork_push | public | cpu | output | 推送,企微 |
| fund_rank | public | cpu | data | 基金排行 |
| fund_screen | public | cpu | analysis | 基金筛选 |
| utils | public | cpu | data | 工具 |
| data_layer | public | cpu | data | Facade |

### 二.4 ModuleRegistry 验证

| # | 验证项 | 方法 | 达标线 |
|---|---|---|---|
| R1 | 自动发现 | `registry.list_all()` | 返回42个模块 |
| R2 | scope筛选 | `discover(scope="private")` | 返回所有需user_id的模块 |
| R3 | cost筛选 | `discover(cost="llm_heavy")` | 仅llm_factor_gen+agent_engine |
| R4 | AI可读 | `to_llm_catalog()`传DeepSeek | DS能正确描述模块功能 |
| R5 | 容错 | 某模块import失败 | 跳过不影响其他41个 |
| R6 | 零改扩展 | 新建test.py加META | 无需改registry即自动发现 |

---

## 三、底座 #2 — DecisionContext

### 三.1 核心字段

```python
# services/decision_context.py (~200行) 🆕
@dataclass
class DecisionContext:
    """决策全链路唯一数据载体"""
    
    # Layer 0: 请求+用户
    user_id: str; question: str; request_type: str  # ask/briefing/review
    stock_holdings: list; fund_holdings: list
    user_memory: dict; risk_overrides: dict
    
    # Layer 1: Regime
    regime: str  # trending_bull/oscillating/high_vol_bear/rotation
    regime_params: dict  # 止损倍数/仓位上限/EV阈值
    
    # Layer 2: 模块并行结果
    module_results: dict  # {name: {available,direction,confidence,data,cost,latency}}
    modules_called: list; modules_skipped: list
    
    # Layer 3: 置信度门控
    weighted_score: float; divergence: float
    gate_decision: str  # "direct_output" / "llm_arbitration"
    module_weights: dict  # 来自judgment_tracker
    
    # Layer 3.5: LLM仲裁（仅需仲裁时填）
    llm_arbitration: dict  # {model,tokens,cost,conclusion,reasoning}
    
    # Layer 4: 赔率+EV
    atr_stop_loss: float; atr_take_profit: float
    kelly_position: float; trading_cost: float = 0.0023
    expected_value: float; ev_passed: bool
    
    # Layer 5: 风控
    risk_checks: list  # [{rule,passed,detail}]
    risk_blocked: bool; global_hold_active: bool
    
    # Layer 6: 输出
    final_direction: str; final_confidence: float; final_suggestion: str
    
    # Layer 7: EMA
    judgment_id: str; weights_before: dict; weights_after: dict
    
    # 性能+错误
    total_latency_ms: int; llm_calls_count: int; llm_total_cost: float
    errors: list  # [{module,error,fallback_used}]
```

### 三.2 三视图 — 同一份数据，不同受众

| 方法 | 受众 | 输出内容 | 用途 |
|---|---|---|---|
| `to_llm_context()` | DeepSeek | 完整结构化文本：用户持仓+Regime+全模块结果+门控状态+EV+风控 | LLM仲裁时的input |
| `to_user_response()` | 前端/用户 | 精简dict：方向+建议+EV+风控状态+延迟 | API返回给app.js |
| `to_judgment_record()` | judgment_tracker | 完整快照：所有层结果+权重+时间戳 | N日后验证用 |

**核心价值**：DeepSeek看到的不再是手动拼的残缺prompt，而是**所有层自动累积的完整视图**。不可能遗漏信息。

### 三.3 enrich() 适配层规范

每个 service 文件尾部加（**不删现有代码**）：

```python
def enrich(ctx: "DecisionContext") -> "DecisionContext":
    """从ctx读输入 → 调本模块现有函数 → 结果写回ctx"""
    try:
        result = existing_function(...)  # 调现有函数，一行不改
        ctx.module_results["module_name"] = {
            "available": True, "direction": ..., "confidence": ..., "data": result
        }
        ctx.modules_called.append("module_name")
    except Exception as e:
        ctx.errors.append({"module": "module_name", "error": str(e)})
        ctx.modules_skipped.append({"name": "module_name", "reason": str(e)})
    return ctx
```

**关键enrich实现（5个孤岛）**：

- **ai_predictor.enrich**: `batch_predict(codes, 5)` → 平均预测方向+置信度
- **rl_position.enrich**: `get_rl_recommendation(code)` → buy/sell/hold映射方向
- **monte_carlo.enrich**: `monte_carlo_single(code, 2000, 60)` → profit_prob映射方向
- **stock_screen.enrich**: `screen_stocks(20)` → top_stocks+regime
- **portfolio_optimizer.enrich**: `optimize_portfolio(user_id)` → adjustments
- **signal.enrich**: `generate_daily_signal()` → 综合信号方向
- **risk.enrich**: `calc_risk_metrics(user_id)` → risk_checks写入ctx

### 三.4 DecisionContext 验证

| # | 验证项 | 方法 | 达标线 |
|---|---|---|---|
| C1 | 全链路累积 | 依次调6层enrich | 每层字段非空 |
| C2 | LLM完整性 | `to_llm_context()`给DS问"看到几个模块" | 回答=实际调用数 |
| C3 | 用户视图精简 | `to_user_response()` | 不含raw_data |
| C4 | 判断可追溯 | 存JSON→30天后加载 | 能重现完整决策 |
| C5 | 错误不丢 | 让某模块抛异常 | errors记录，其他模块不受影响 |

---

## 四、底座 #3 — PipelineRunner

### 四.1 管线定义

```python
# services/pipeline_runner.py (~200行) 🆕
PIPELINES = {
    "default": [  # 日常决策，门控60%直出
        step_load_user_data,   # Layer0: 用户持仓+记忆+风控
        step_regime,           # Layer1: 市场状态4类
        step_parallel_modules, # Layer2: Registry发现→asyncio.gather并行enrich
        step_confidence_gate,  # Layer3: 加权分+分歧度→直出/仲裁
        step_llm_arbitration,  # Layer3.5: 仅需仲裁时调DS
        step_payoff_ev,        # Layer4: ATR止损+半凯利+EV(扣0.23%成本)
        step_risk_firewall,    # Layer5: 6类风控+全员观望+per-user覆盖
        step_generate_output,  # Layer6: 最终建议
        step_ema_calibration,  # Layer7: 记录判断+权重快照
    ],
    "fast": [  # 紧急/盘中，跳过LLM
        step_load_user_data,
        step_regime,
        step_parallel_modules,
        step_risk_firewall,
        step_generate_output,
    ],
    "cautious": [  # 熊市/大仓位，加体检
        step_load_user_data,
        step_regime,
        step_parallel_modules,
        step_confidence_gate,
        step_llm_arbitration,
        step_payoff_ev,
        step_portfolio_doctor,  # 附加：压力测试+集中度
        step_risk_firewall,
        step_generate_output,
        step_ema_calibration,
    ],
}
```

### 四.2 AI 动态选管线

```python
class PipelineRunner:
    async def run(self, pipeline_name: str, ctx: DecisionContext) -> DecisionContext:
        """按配置顺序执行步骤，每步读写ctx"""
    
    def select_pipeline(self, regime: str, urgency: str = "normal") -> str:
        """AI根据Regime+紧急度选管线"""
        if urgency == "urgent": return "fast"
        return {"trending_bull": "default", "oscillating": "default",
                "high_vol_bear": "cautious", "rotation": "default"}.get(regime, "default")
```

### 四.3 管线对比

| 管线 | 步骤 | LLM | 适用 | 耗时 |
|---|:---:|:---:|---|:---:|
| default | 9 | 0-1 | 日常 | 8-15s |
| fast | 5 | 0 | 紧急 | 3-5s |
| cautious | 10 | 0-1 | 熊市 | 15-25s |

### 四.4 PipelineRunner 验证

| # | 验证项 | 方法 | 达标线 |
|---|---|---|---|
| P1 | default完整 | run("default", ctx) | 所有Layer字段非空 |
| P2 | fast无LLM | run("fast", ctx) | llm_calls_count==0 |
| P3 | cautious含体检 | run("cautious", ctx) | 含doctor结果 |
| P4 | 容错 | 某步骤异常 | errors记录，后续继续 |
| P5 | 动态选 | regime=high_vol_bear | 返回"cautious" |
| P6 | 性能 | 计时default | <15秒 |

---

## 五、6层决策管线（v3.0 底座驱动版）

```
用户提问 → Steward → PipelineRunner.select_pipeline(regime)
  │
  ▼
┌────────────────────────────────────────┐
│ ① Regime引擎 regime_engine.py 🆕      │ ← 老师
│ 4类: 趋势牛/震荡/高波熊/轮动           │
│ → ctx.regime + ctx.regime_params       │
│ → PipelineRunner据此选管线             │
└──────────┬─────────────────────────────┘
           ▼
┌────────────────────────────────────────┐
│ ② 并行模块 (Registry自动发现)          │
│ Registry.discover(layer="analysis")    │
│ → asyncio.gather(*.enrich(ctx))        │
│ → ctx.module_results自动累积           │
│ 新模块加META+enrich即自动加入(O(1))    │
└──────────┬─────────────────────────────┘
           ▼
┌────────────────────────────────────────┐
│ ③ 置信度门控 🆕                       │ ← B
│ 读 ctx.module_results + module_weights │
│ 高一致(>0.7)+低分歧(<0.3) → 直出      │
│ 否则 → LLM仲裁(ctx.to_llm_context())  │
│ 预期60-80%直出                         │
└──────────┬─────────────────────────────┘
           ▼
┌────────────────────────────────────────┐
│ ④ 赔率+期望值 🆕                      │ ← 老师+D
│ ATR动态止损 + 半凯利仓位              │
│ E=(胜率×(盈-C))-(败率×(亏+C))         │
│ C=单次0.23%(佣金+印花+滑点) ← D       │
│ E≤0 → 拦截不出手                      │
└──────────┬─────────────────────────────┘
           ▼
┌────────────────────────────────────────┐
│ ⑤ 风控防火墙                          │ ← A/C/E
│ 单票≤25% TOP3≤60% 极端暂停 冷却48h    │
│ 全员观望开关 + per-user覆盖           │
│ 读 ctx.risk_overrides                 │
└──────────┬─────────────────────────────┘
           ▼
       ctx.to_user_response() → 前端
           ▼
┌────────────────────────────────────────┐
│ ⑥ EMA自校准 🆕                       │ ← B
│ ctx.to_judgment_record() → 存储        │
│ N日后验证 → 调整 module_weights        │
└────────────────────────────────────────┘
```

**所有DeepSeek调用经 llm_gateway.py 🆕**（← B+D）:
模型路由V3/R1 + 缓存 + **按user_id+module双标签记账** + 50次/日熔断

---

## 六、6个新建模块设计

| 模块 | 行数 | 私/公 | 底座集成方式 |
|---|:---:|:---:|---|
| regime_engine.py | ~200 | 公共 | Registry注册 + PipelineRunner Layer1调用 |
| llm_gateway.py | ~250 | 公共 | 所有LLM调用统一入口 |
| signal_scout.py | ~400 | 混合 | Registry注册 + enrich()写ctx |
| judgment_tracker.py | ~350 | 私有 | Pipeline Layer7调用 + get_weights()供Layer3 |
| portfolio_doctor.py | ~350 | 私有 | Registry注册 + cautious管线附加步骤 |
| steward.py | ~500 | 私有 | 创建DecisionContext + 调PipelineRunner.run() |

### 六.A llm_gateway.py — 3层防爆详细设计 🆕

> **问题根源**：现有代码 DeepSeek 调用完全无控制 —— ds_enhance.py 7处独立 httpx 调用 + /chat/stream 无限制 + 零计费零缓存零熔断。

#### 第1层：置信度门控（Pipeline Layer3，省 60-80% 调用）

```python
def step_confidence_gate(ctx):
    """模块一致 → 直出，不调 LLM"""
    scores = [(name, r["confidence"], r["direction"]) 
              for name, r in ctx.module_results.items() if r["available"]]
    
    # 加权平均（权重来自 judgment_tracker）
    ctx.weighted_score = sum(w * c for _, c, _ in scores) / len(scores)
    
    # 分歧度 = 方向不一致的模块比例
    dirs = [d for _, _, d in scores]
    majority = max(set(dirs), key=dirs.count)
    ctx.divergence = 1 - dirs.count(majority) / len(dirs)
    
    if ctx.weighted_score > 0.7 and ctx.divergence < 0.3:
        ctx.gate_decision = "direct_output"  # 不调 LLM！
    else:
        ctx.gate_decision = "llm_arbitration"
```

#### 第2层：llm_gateway 统一管控

```python
class LLMGateway:
    """所有 LLM 调用的唯一入口"""
    
    # 模型路由
    MODEL_ROUTING = {
        "llm_light": "deepseek-chat",      # V3: 快速、便宜、够用
        "llm_heavy": "deepseek-reasoner",   # R1: 深度推理、多步思考
    }
    
    # ━━ V3 vs R1 全量场景表 ━━
    # 
    # 【V3 (deepseek-chat)】— 快、便宜、适合"有标准答案"的任务
    # ┌────────────────────────┬──────────────────────────────────┐
    # │ 场景                    │ 说明                              │
    # ├────────────────────────┼──────────────────────────────────┤
    # │ 选基/选股一句话点评      │ comment_fund/stock_picks，15字      │
    # │ 今日关注生成            │ generate_daily_focus，3条tips       │
    # │ 信号解读               │ interpret_daily_signal，翻译成人话   │
    # │ 新闻风控评估            │ assess_news_risk，返回JSON          │
    # │ 新闻深度影响            │ deep_analyze_news_impact，结构化    │
    # │ 存款建议               │ analyze_idle_cash，250 token        │
    # │ 推荐配置AI点评          │ comment_recommend_funds             │
    # │ 选股动态权重            │ _get_dynamic_weights，7维权重JSON   │
    # │ /chat/stream 日常聊天   │ 用户闲聊、问行情、问术语            │
    # │ 企微推送文案            │ 每日报告文本生成                    │
    # └────────────────────────┴──────────────────────────────────┘
    # 特征: 输出短(<500 token) / 格式固定(JSON) / 不需要多步推理
    # 成本: ~0.001元/次
    #
    # 【R1 (deepseek-reasoner)】— 慢、贵、适合"需要权衡/推理"的任务
    # ┌────────────────────────┬──────────────────────────────────┐
    # │ 场景                    │ 说明                              │
    # ├────────────────────────┼──────────────────────────────────┤
    # │ Pipeline Layer3.5 仲裁  │ 模块分歧时多空辩论→裁决，核心场景   │
    # │ 全持仓深度诊断          │ stock/fund_holdings/analyze         │
    # │ 政策→A股影响分析        │ policy/impact，需要因果推理链       │
    # │ 全球→A股影响分析        │ global/impact，多市场关联推理       │
    # │ AI资产诊断             │ ds/asset-diagnosis，全量资产结构     │
    # │ LLM因子生成            │ llm_factor_gen，需要金融+代码推理    │
    # │ Agent收盘复盘          │ agent/analyze force=True            │
    # │ /chat R1模式           │ 用户手动选R1深度思考（前端下拉选）    │
    # └────────────────────────┴──────────────────────────────────┘
    # 特征: 输出长(>800 token) / 需要多步推理 / 需要权衡多方论据
    # 成本: ~0.01-0.05元/次（比V3贵10-50倍）
    #
    # 【选择原则】
    # 1. 默认用 V3，除非场景在 R1 列表里
    # 2. 门控直出的场景不调任何模型（省100%）
    # 3. 用户在前端手动选 R1 = 尊重用户选择
    # 4. 成本红线: R1 调用 ≤ 总 LLM 调用的 20%
    
    # 缓存（相同请求1小时内复用）
    _cache: dict  # key = hash(user_id + module + prompt_hash)
    CACHE_TTL = 3600  # 1小时
    
    # 计费（按 user_id + module 双标签）
    _usage: dict  # {user_id: {module: {calls, tokens, cost}}}
    
    # 熔断
    DAILY_LIMIT = 50    # 每天最多50次 LLM 调用
    BURST_LIMIT = 10    # 5分钟内最多10次
    _daily_count: int
    _burst_window: list  # 时间戳列表
    
    async def call(self, prompt, *, model_tier="llm_light", 
                   user_id="", module="", max_tokens=800) -> dict:
        # 1. 缓存命中？
        cache_key = hash(f"{user_id}:{module}:{hash(prompt)}")
        if cache_key in self._cache and not expired:
            return self._cache[cache_key]
        
        # 2. 熔断检查
        if self._daily_count >= self.DAILY_LIMIT:
            return {"content": "", "source": "rate_limited", "fallback": True}
        
        # 3. 选模型
        model = self.MODEL_ROUTING[model_tier]
        
        # 4. 调用
        result = await self._do_call(model, prompt, max_tokens)
        
        # 5. 记账
        self._usage[user_id][module]["calls"] += 1
        self._usage[user_id][module]["tokens"] += result["tokens"]
        self._daily_count += 1
        
        # 6. 写缓存
        self._cache[cache_key] = result
        return result
    
    def get_usage_report(self) -> dict:
        """日报/月报：按用户×模块的调用统计"""
```

#### 第3层：Pipeline 管线选择

| 管线 | LLM 调用次数 | 触发条件 |
|---|:---:|---|
| fast | **0** | 紧急/盘中 → 纯 CPU 出结果 |
| default | **0-1** | 日常 → 门控60%直出，40%仲裁1次 |
| cautious | **0-1** | 熊市 → 同 default + 体检（体检不调 LLM） |

#### 成本对比

| 指标 | 现在（无控制） | v3.0（3层防爆） |
|---|---|---|
| 日均 LLM 调用 | ~30-50 次 | **≤15 次** |
| 月费用（V3模型） | ¥30-50 | **¥10-15** |
| DS 挂了的影响 | 聊天全挂、ds_enhance全挂 | 门控直出 + 规则引擎降级，服务不中断 |
| 重复调用 | 同一问题重复调 | 缓存1小时内复用 |
| 费用可查 | 完全不知道花了多少 | `/api/llm-usage` 按用户×模块日报 |

#### ds_enhance.py 7处迁移清单

| 函数 | 现在调 | 迁移后调 | model_tier |
|---|---|---|---|
| `analyze_idle_cash()` | `_call_deepseek()` | `gateway.call(tier="llm_light", module="idle_cash")` | light |
| `comment_fund_picks()` | `_call_deepseek()` | `gateway.call(tier="llm_light", module="fund_picks")` | light |
| `comment_stock_picks()` | `_call_deepseek()` | `gateway.call(tier="llm_light", module="stock_picks")` | light |
| `generate_daily_focus()` | `_call_deepseek()` | `gateway.call(tier="llm_light", module="daily_focus")` | light |
| `assess_news_risk()` | `_call_deepseek()` | `gateway.call(tier="llm_light", module="news_risk")` | light |
| `interpret_daily_signal()` | `_call_deepseek()` | `gateway.call(tier="llm_light", module="signal_interpret")` | light |
| `deep_analyze_news_impact()` | `_call_deepseek()` | `gateway.call(tier="llm_heavy", module="news_deep")` | heavy |

---

**steward.py 简化为**：
```python
class Steward:
    def __init__(self):
        self.runner = PipelineRunner()
        self.registry = ModuleRegistry.instance()
    
    async def ask(self, user_id, question) -> dict:
        regime = RegimeEngine().classify()["regime"]
        pipeline = self.runner.select_pipeline(regime)
        ctx = DecisionContext(user_id=user_id, question=question)
        ctx = await self.runner.run(pipeline, ctx)
        return ctx.to_user_response()
```

v2.2 的 steward 需要 ~500 行手动编排 → v3.0 只需 ~200 行（编排逻辑在 PipelineRunner 里）。

---

## 七、多账号隔离设计（完整保留自 v2.2）

### 七.1 现有隔离矩阵
（已实现，复用：持仓CRUD✅、流水✅、记忆✅、净资产✅、聊天✅、Cron✅、企微✅）

### 七.2 V4新模块多账号策略

| 新模块 | 私/公 | userId传递 | 存储路径 |
|---|:---:|---|---|
| steward | 私有 | `ask(user_id, ...)` | ctx内流转不存 |
| signal_scout | 混合 | `match(signals, user_id)` | 原始=全局缓存，match后=私有 |
| judgment_tracker | 私有 | 全部函数带user_id | `data/judgments/{uid}/{YYYY-MM}.json` |
| portfolio_doctor | 私有 | `diagnose(user_id)` | 不存文件，实时算 |
| regime_engine | 公共 | 无需 | 全局缓存30min |
| llm_gateway | 公共 | module标签 | 按user_id+module双标签统计 |

**DecisionContext 的多用户**：每次 ask 创建新 ctx（user_id 不同 → 持仓不同 → 结果不同）。

### 七.3 steward多用户流转 + signal_scout公私分离 + judgment_tracker隔离 + Cron编排 + localStorage修复 + 已知安全漏洞 + 新增API多账号清单

（与 v2.2 §三.3-§三.9 完全一致，不重复。关键点：）
- steward: user_id贯穿6层ctx
- signal_scout: collect()公共 → match(uid)私有 → deliver(uid)私有
- judgment: `data/judgments/{uid}/weights.json` 每用户独立演化
- Cron: 公共只跑1次 → 按用户遍历私有
- localStorage: key加`_${getProfileId()}`后缀
- 安全: 暂不修token(2人) / adminKey→W1迁.env

---

## 八、现有模块改造清单

| 文件 | 现行数 | 改动类型 | 说明 |
|---|:---:|---|---|
| 全部42个service | ~13,200 | **+MODULE_META头部** | 每文件+8-15行 |
| 7个分析模块 | — | **+enrich()尾部** | 每文件+20-30行 |
| agent_engine.py | 246 | 重写为steward薄包装 | →80行 |
| ds_enhance.py | 485 | 7处httpx→llm_gateway | 函数体替换 |
| chat.py | 270 | **删除** | 死代码 |
| profiles.py | 168 | adminKey→.env | 安全 |
| app.js localStorage | — | key加userId后缀 | +20行 |
| 其余(news/policy/portfolio) | — | DS调用→gateway | ~0行 |

---

## 九、数据源地图

### 现有55+接口

| 类别 | 接口数 | 来源 | 稳定性 |
|---|:---:|---|:---:|
| A股行情 | 5 | AKShare+Tushare | ⚠️东财反爬 |
| 估值/财务 | 5 | Tushare(PE/PB/ROE) | ✅ |
| 资金面 | 4 | AKShare | ✅ |
| 宏观 | 12 | AKShare | ✅ |
| 另类 | 5 | AKShare | ✅ |
| 新闻/政策 | 7 | AKShare | ✅ |
| 全球 | 5 | AKShare | ✅ |
| 基金 | 5 | AKShare | ✅ |
| LLM | 1 | DeepSeek | ✅ |

### V4新增6项
Tushare公告+研报 → signal_scout | 港股恒生+南向 → global_market | ATR本地 | 相关矩阵复用

### V4新增数据源（朋友G方案吸收） 🆕

> 朋友G提供了机构级全量化数据需求清单（6大类），以下是经评估后适合钱袋子定位的吸收项：

**P0 — Tushare 直接可接（Token 已有 2000 积分，W3 排期，共 ~10h）：**

| 数据项 | Tushare 接口 | 写入位置 | 用途 | 工时 |
|---|---|---|---|:---:|
| 大股东增减持 | `stk_holdertrade` | signal_scout.py | 管理层动向→信号 | 2h |
| 股权质押比例 | `pledge_stat` | risk.py 风控因子 | 质押爆仓风险 | 1h |
| 解禁数据（增强） | `share_float` | holding_intelligence.py | 已有基础版，补全字段 | 1h |
| 分红送转 | `dividend` | stock_screen.py 因子 | 价值因子增强 | 1h |
| 公告全文 | `anns` | signal_scout.py | DS 提取信号 | 2h |
| 研报摘要 | `report_rc` | signal_scout.py | DS 提取观点 | 2h |
| ST/*ST 标记 | `namechange` + `suspend_d` | risk.py + stock_screen.py | 风控排除+选股过滤 | 1h |

**P1 — AKShare 可接（W4 排期，共 ~3h）：**

| 数据项 | 来源 | 写入位置 | 用途 | 工时 |
|---|---|---|---|:---:|
| 涨停跌停池+炸板率 | AKShare `stock_zt_pool_em` | signal_scout.py | 市场情绪温度计 | 2h |
| VIX 波动率指数 | AKShare CBOE VIX | global_market.py | 全球避险情绪 | 1h |

**P2 — 可选扩展（W4-W7 视情况）：**

| 数据项 | 来源 | 条件 | 工时 |
|---|---|---|:---:|
| 大单/中单/小单资金拆分 | Tushare `moneyflow` | Tushare 积分够 | 3h |
| 文本情绪 NLP 分析 | DS 处理已有新闻文本 | llm_gateway 就绪后 | 含在 W7 |

**明确不做的（投入产出比不适合 2 人理财 App）：**

| 不做的数据 | 原因 |
|---|---|
| 逐笔成交/盘口深度/百档挂单 | 需 Level-2 付费行情源（万元/年），高频交易用 |
| 分钟 K 线 | 数据量大，2C2G 服务器存不下，日频决策用日K够 |
| 期货/期权/融券对冲工具 | 不做自动交易和衍生品对冲 |
| 电力能耗/卫星灯光/货运物流 | 机构级商业数据集（几十万/年） |
| 链上数据/DEX/加密市场 | 不做加密市场 |
| 盘前盘后美股/ADR | 收盘数据已够，日频决策不需要 |

### 因子体系补充（老乡H方案吸收） 🆕

> 老乡H提供了7大类因子体系清单，经评估：现有30因子7维打分已覆盖70%，补1项。

**吸收：因子拥挤度检测（P1，W5 排期，2h）**

| 指标 | 实现 | 写入位置 |
|---|---|---|
| IC 下降速率 | `factor_ic.py` 已有 IC 衰减，加"近3期IC均值 vs 近12期IC均值"比值 | `factor_ic.py` 新函数 `calc_crowding_score()` |
| 用途 | 自动标记"拥挤因子"（比值<0.5=该因子大家都在用，正在失效） | `stock_screen.py` 动态降权拥挤因子 |

**已有验证（老乡说的"AI量化关键区别"3条咱们全有）：**
- 自动因子挖掘 → `genetic_factor.py` ✅
- 多模态融合 → `stock_screen.py` 7维统一打分 ✅
- 动态因子权重 → `_get_dynamic_weights()` DS调权 ✅

---

## 十、Prompt工程

| 文件 | 用途 | 复杂度 | 底座关系 |
|---|---|:---:|---|
| signal_extract.md | 新闻→结构化信号 | LIGHT | signal_scout.enrich() |
| steward_arbitrate.md | 多模块仲裁 | HEAVY | **接收ctx.to_llm_context()** |
| portfolio_diagnose.md | 压力+集中度→诊断 | HEAVY | portfolio_doctor.enrich() |
| weekly_report.md | 成绩+体检→周报 | HEAVY | ctx.to_judgment_record() |

**v3.0 关键变化**: steward_arbitrate.md 的输入不再手动拼装，而是直接用 `ctx.to_llm_context()` 自动组装完整视图。

---

## 十一、前端改造

### 新增Tab
| 位置 | 内容 | API | 需userId |
|---|---|---|:---:|
| 资讯→📡信号 | 信号列表+关联 | /api/signal-scout/latest | ✅ |
| 资讯→📊成绩单 | 准确率+权重 | /api/judgment/scorecard | ✅ |
| 持仓→🏥体检 | 压力+雷达+诊断 | /api/portfolio-doctor/* | ✅ |
| 首页→管家卡片 | 状态+TOP1+快捷 | /api/steward/briefing | ✅ |

### 双模式UI(W7)
小白(老婆): 净资产+管家一句话 | 专业(你): 全部+权重+Regime+Pipeline状态

### 十一.A chat/stream 升级方案 🆕

> **问题**：现在 `/chat/stream` 的 DeepSeek 只看到 `_build_market_context()` + `_build_portfolio_context()` 手动拼装的 ~50 行文本，**完全不知道** steward 和各模块的分析结果。聊天中 DeepSeek 是"独立问答"，不是"基于决策系统的智能回答"。

**升级方案：**

```python
# main.py /chat/stream 改造
async def chat_analysis_stream(req: ChatRequest):
    # 1. 检查最近15分钟是否有 steward 决策
    recent_ctx = steward.get_recent_context(req.userId, within_minutes=15)
    
    # 2. 构建增强 system prompt
    if recent_ctx:
        # 最近有决策 → 注入完整上下文
        enhanced_ctx = recent_ctx.to_llm_context()  # 200+ 行结构化
        system_prompt = f"""{_load_prompt_template()}

## 最近AI决策（{recent_ctx.total_latency_ms}ms前）
{enhanced_ctx}

## 用户正在就此决策追问，请基于以上完整分析回答。"""
    else:
        # 无最近决策 → 触发轻量 fast Pipeline
        ctx = await steward.ask(req.userId, req.message)
        system_prompt = f"""{_load_prompt_template()}

## 实时分析结果
{ctx.to_llm_context()}"""
    
    # 3. 通过 llm_gateway 调用（有缓存+计费+熔断）
    # 替代原来直接 httpx 调 DeepSeek
```

**效果**：用户问"茅台能买吗" → DeepSeek 看到 5个模块分析+Regime+EV+风控 → 回答有完整数据支撑。

### 十一.B 前后端 API 对应清单 🆕

> **问题**：完整审计发现 142 个后端路由中 70+ 个前端没调用。10 个高价值 API 完全浪费。

#### 现有高价值但前端未接入的 API（排期 W8）

| # | 后端路由 | 功能 | 价值 | 接入位置 |
|---|---|---|:---:|---|
| 1 | `/news/deep-impact` | DeepSeek 新闻→行业→持仓深度分析 | 🔴高 | 资讯→新闻 Tab 底部 |
| 2 | `/news/risk-assess` | DeepSeek 新闻风控评估 | 🔴高 | 资讯→新闻 Tab 底部 |
| 3 | `/stock-holdings/analyze` | 全股票持仓 7-Skill 深度分析 | 🔴高 | 持仓→股票 Tab 顶部按钮 |
| 4 | `/fund-holdings/analyze` | 全基金持仓 7-Skill 深度分析 | 🔴高 | 持仓→基金 Tab 顶部按钮 |
| 5 | `/daily-signal/interpret` | DeepSeek 信号解读 | 🟡中 | 资讯→总览 信号卡片 |
| 6 | `/timing` | 入场时机独立建议 | 🟡中 | 首页管家卡片 |
| 7 | `/smart-dca` | 智能定投建议 | 🟡中 | 首页管家卡片 |
| 8 | `/rl-position/portfolio/{uid}` | RL 全持仓建议 | 🟡中 | 资讯→RL Tab |
| 9 | `/stock/financials/{code}` | 个股财务数据 | 🟡中 | 持仓→股票详情弹窗 |
| 10 | `/agent/memory/{uid}` | Agent 记忆查看 | 🟡中 | 设置页 |

#### V4 新增 18 API 前端对应表

| 新增 API | 前端位置 | Tab |
|---|---|---|
| `/steward/ask` | AI聊天页（替代原 /chat/stream 的分析部分） | 聊天 |
| `/steward/briefing` | 首页管家卡片 | 首页 |
| `/steward/review` | 首页管家卡片→展开 | 首页 |
| `/steward/weekly` | 资讯→成绩单 Tab→周报按钮 | 资讯 |
| `/signal-scout/latest` | 资讯→📡信号 Tab | 资讯 |
| `/signal-scout/trigger` | 无前端（cron后端触发） | — |
| `/judgment/scorecard` | 资讯→📊成绩单 Tab | 资讯 |
| `/judgment/weights` | 资讯→📊成绩单 Tab→权重展开 | 资讯 |
| `/judgment/calibrate` | 无前端（cron后端触发） | — |
| `/portfolio-doctor/*` | 持仓→🏥体检 Tab | 持仓 |
| `/regime` | 资讯→总览 顶部 Regime 徽章 | 资讯 |
| `/global-hold` | 设置页→全员观望开关 | 设置 |
| `/llm-usage` | 设置页→AI 用量统计 | 设置 |
| `/backup` | 设置页→一键备份 | 设置 |
| `/registry/catalog` | 设置页→模块列表（专业模式） | 设置 |
| `/pipeline/status` | 首页管家卡片→Pipeline 执行状态 | 首页 |

#### 合理不接入的 API（聚合接口已覆盖）

以下独立接口通过聚合 API 获取数据，**不需要**前端单独调用：
- `/factors/*`（17个） → 通过 `/dashboard` 聚合
- `/macro/*`（10个） → 通过 `/dashboard` 聚合
- `/global/*`（4个细分） → 通过 `/global/snapshot` 聚合
- `/alt-data/*`（5个细分） → 通过 `/alt-data/dashboard` 聚合
- `/policy/real-estate`, `/policy/house-price` → 通过 `/policy/all-topics` 聚合

---

## 十二、Cron编排

> 休市日历门控 `is_trading_day()` + 告警分级(系统异常仅推主账号)

| 时间 | 作业 | 公共/按用户 |
|---|---|:---:|
| 08:25 | 信号收集 | 公共1次 |
| 08:28 | 信号匹配+推送 | 按用户 |
| 08:30 | 早间简报 | 按用户 |
| 09:30-14:30 | 盯盘6次 | 按用户(已有) |
| 15:30 | 收盘复盘 | 按用户 |
| 16:30 | 判断验证 | 按用户 |
| 周六02:00 | 遗传因子 | 公共 |
| 周六10:00 | 周报 | 按用户 |
| 周日22:00 | EMA校准 | 按用户 |

---

## 十三、API新增(16个)

| 路由 | userId | 公/私 | 来源 |
|---|:---:|:---:|:---:|
| /api/steward/ask | ✅ | 私 | 核心 |
| /api/steward/briefing | ✅ | 私 | 核心 |
| /api/steward/review | ✅ | 私 | 核心 |
| /api/steward/weekly | ✅ | 私 | 核心 |
| /api/signal-scout/latest | ✅ | 私 | 核心 |
| /api/signal-scout/trigger | ❌ | 公 | 核心 |
| /api/judgment/scorecard | ✅ | 私 | 核心 |
| /api/judgment/weights | ✅ | 私 | 核心 |
| /api/judgment/calibrate | ✅ | 私 | 核心 |
| /api/portfolio-doctor/* (3个) | ✅ | 私 | 核心 |
| /api/regime | ❌ | 公 | 核心 |
| /api/global-hold | ✅主 | 全局 | E |
| /api/llm-usage | ❌ | 公 | D |
| /api/backup | ✅主 | 全局 | E |
| 🆕 /api/registry/catalog | ❌ | 公 | 底座 |
| 🆕 /api/pipeline/status | ✅ | 私 | 底座 |

新增2个底座API: registry目录查询 + 管线执行状态

---

## 十四、风控规则表(steward_config.py)

（与 v2.2 §十三 完全一致：7类场景 + RISK_LIMITS + POSITION_LIMITS + TRADING_COST + GLOBAL_HOLD + per-user覆盖）

**v3.0 变化**: 风控不再在steward里硬编码，而是 step_risk_firewall(ctx) 读 ctx.risk_overrides。

---

## 十五、铁律合规检查

（与 v2.2 §十四 完全一致：20条铁律逐条✅ + 10条踩坑教训 + 代码规范）

**v3.0 新增合规项**:

| 新规 | V4措施 |
|---|---|
| MODULE_META格式一致性 | 每个META必须包含全部9个字段 |
| enrich()不改原函数 | enrich只调用原函数，不修改其实现 |
| Registry启动时间 | _auto_discover()<2秒 |

---

## 十六、验证方法论 — 证明 AI 确实发挥价值 🆕

### 十六.1 分阶段验证矩阵

| Phase | 验证什么 | 怎么验 | 达标线 | 失败回滚 |
|---|---|---|---|---|
| **W1-W2 底座** | 3底座能跑通 | 单元测试: Registry发现42模块 + Context累积 + Pipeline执行 | R1-R6,C1-C5,P1-P6全通过 | 底座代码回退，不影响44个service |
| **W3 适配** | MODULE_META+enrich不破坏现有 | 每个service加META后: ① python -c "import services.xxx" ② 原API功能正常 | 42模块全部import成功 + 现有API回归通过 | 删除META+enrich，原文件不变 |
| **W4 串联** | 5个孤岛真正被Pipeline调用 | steward.ask() → 检查ctx.modules_called | 包含5个孤岛模块名 | 原路由API仍可独立调用 |
| **W5 门控** | 置信度门控省LLM | 100次ask → 统计direct_output比例 | ≥60%直出 | 关闭门控=所有走LLM仲裁(v2.2行为) |
| **W6 EV** | 负EV拦截 | 构造负EV场景 → 检查risk_blocked=True | 100%拦截 | 关闭EV检查(v2.2无EV) |
| **W7 完整** | 全链路走通 | 真实市场数据 + 双用户 + 企微推送 | 两用户收到不同建议 | — |
| **W8 上线** | AI比人做得好 | 1周实盘判断追踪 → 准确率 | ≥55% | 降级"仅供参考" |

### 十六.2 AI 价值的4维度量化验证

| 维度 | 量化指标 | 测量方法 | 达标线 |
|---|---|---|---|
| **AI 自主决策** | 模块发现率 | Registry.list_all() | 42/42=100% |
| | 上下文完整率 | to_llm_context()字段非空率 | ≥95% |
| | 管线动态选择 | 不同Regime触发不同管线 | 4类Regime→3种管线 |
| **人可看** | 前端展示完整 | to_user_response()所有字段→DOM | 方向+置信度+EV+风控全显示 |
| | 判断可追溯 | to_judgment_record()→JSON→30天可查 | 100%判断有记录 |
| **DeepSeek最强大脑** | 仲裁质量 | 仲裁vs直出的准确率对比 | 仲裁≥直出(否则门控阈值调低) |
| | 上下文注入量 | to_llm_context()字数 | ≥手动拼装的2倍 |
| **成本控制** | LLM调用量 | 门控直出率 | ≥60%（较v2.2省60%LLM） |
| | 月费用 | llm_gateway月报 | ≤¥30 |
| | 管线效率 | fast管线LLM调用 | =0 |

### 十六.3 每步修改的验证 Checklist

**加 MODULE_META 时**:
1. ✅ `python -c "from services.xxx import MODULE_META; print(MODULE_META['name'])"` — 能导入
2. ✅ META 包含全部 9 个字段（name/scope/input/output/cost/tags/description/layer/priority）
3. ✅ 原有 API 功能不受影响（调一次原路由确认）

**加 enrich() 时**:
1. ✅ enrich 只调用现有公开函数，不修改函数实现
2. ✅ enrich 返回 ctx（不是 None）
3. ✅ 异常时 ctx.errors 有记录，不抛出到上层
4. ✅ 手动调 `enrich(mock_ctx)` → 检查 ctx.module_results 非空

**加 Pipeline 步骤时**:
1. ✅ 步骤只读写 ctx，不产生副作用（除了 Layer7 的 judgment 存储）
2. ✅ 某步骤抛异常 → 后续步骤仍执行
3. ✅ `ctx.total_latency_ms < 15000`

### 十六.4 联合验证组 — 多组件协同验证 🆕

> 单组件验证通过 ≠ 系统可用。以下定义跨组件联合验证。

#### 验证组 A：底座阶段（W1-W2）

| 组 | 涉及组件 | 验证方法 | 预计耗时 |
|---|---|---|:---:|
| A1 | Registry + Context | mock ctx → Registry 发现 42 模块 → 7 个 enrich 写入 ctx → 检查 ctx 完整性 | 10min |
| A2 | Registry + Pipeline | default 管线 → Registry 自动发现 analysis 层 → 并行执行 → 检查 modules_called | 10min |
| A3 | 3底座 + 现有 API | 启动服务 → 调原有 `/dashboard` `/signals` `/stock-holdings/scan` → 确认不受底座影响 | 15min |

#### 验证组 B：新功能阶段（W3-W4）

| 组 | 涉及组件 | 验证方法 |
|---|---|---|
| B1 | signal_scout + Cron + 企微 | 手动触发 cron → 信号收集 → 匹配用户持仓 → 企微收到推送 |
| B2 | judgment_tracker + 前端 | 记录判断 → `/judgment/scorecard` 返回数据 → 前端成绩单 Tab 显示 |
| B3 | 多用户隔离 | LeiJiang + BuLuoGeLi 各有不同持仓 → signal_scout 匹配不同 → judgment 文件在各自目录 |

#### 验证组 C：核心阶段（W5-W7）

| 组 | 涉及组件 | 验证方法 |
|---|---|---|
| C1 全链路-默认 | steward + default Pipeline + 门控 | ask("茅台能买吗") → Regime判断 → 5+模块并行 → 门控→直出 → 前端显示完整结果 |
| C2 全链路-仲裁 | steward + 门控 + DS 仲裁 | 构造模块分歧场景 → 门控触发 LLM → DS 看到 `to_llm_context()` → 输出仲裁 |
| C3 全链路-拦截 | steward + EV + 风控 | 构造负 EV 场景 → EV 拦截 → 前端显示"不建议操作" |
| C4 成本验证 | llm_gateway + 门控 | 100 次 ask → 统计 LLM 实际调用数 → ≤40 次（门控省 60%） |
| C5 降级验证 | 全链路 | 关闭 DS API key → 门控全部直出 → 规则引擎降级 → 服务不中断 |
| C6 chat/stream 升级 | chat + steward + llm_gateway | 聊天问"茅台" → system prompt 含最近 ctx → DS 回答引用模块结论 |

#### 验证组 D：上线阶段（W8-W10）

| 组 | 涉及组件 | 验证方法 |
|---|---|---|
| D1 双模式 UI | 前端切换 | 小白模式: 只显示管家一句话+净资产 / 专业模式: 全部数据 |
| D2 新旧 API 共存 | 10个遗漏API + 18个新API | 逐个调用确认返回正确 + 前端 Tab 能显示 |
| D3 企微全流程 | cron + steward + 企微 | 早简报→盘中盯盘→收盘复盘→企微推送 LeiJiang + BuLuoGeLi |

### 十六.5 回归测试清单 🆕

> 每次修改后按场景选择运行。

#### 基础回归（每次改完都跑）

```
□ python -c "import services.xxx" — 全部 42 模块可导入
□ GET /api/health → 200
□ GET /api/dashboard → 有数据返回（11 源）
□ POST /api/chat/stream → 流式响应正常
□ GET /api/stock-holdings/scan?userId=LeiJiang → 有持仓数据
□ GET /api/fund-holdings/scan?userId=LeiJiang → 有基金数据
□ 手机打开 → 5 个 Tab 都能进 → 数据加载 <15s
```

#### 底座回归（改 Registry/Context/Pipeline 后跑）

```
□ Registry.list_all() 返回 42 个模块
□ run("default", mock_ctx) → 所有 Layer 字段非空
□ run("fast", mock_ctx) → llm_calls_count == 0
□ run("cautious", mock_ctx) → 含 doctor 结果
□ Context.to_llm_context() 字符数 ≥ 500
□ Context.to_user_response() 不含 raw_data
```

#### 多用户回归（改隔离相关后跑）

```
□ LeiJiang 和 BuLuoGeLi 看到不同持仓
□ judgment 文件在各自 uid 目录下
□ llm_gateway 按 user_id 分别计费
□ localStorage key 有 userId 后缀（无 key 碰撞）
```

#### LLM 回归（改 gateway/门控/DS 相关后跑）

```
□ llm_gateway 缓存：相同请求第2次 <10ms
□ llm_gateway 熔断：超50次返回 fallback
□ 门控：高一致场景 gate_decision == "direct_output"
□ 门控：分歧场景 gate_decision == "llm_arbitration"
□ DS 挂了：全链路仍输出结果（规则引擎降级）
```

---

## 十七、10-12周实施路线图（v3.0）

> v2.2 的 8 周→v3.0 的 10-12 周：W1-W2 多了底座搭建

| 周 | 主题 | 内容 | 工时 | 交付物 |
|:---:|---|---|:---:|---|
| **W1** | **3底座搭建** | module_registry + decision_context + pipeline_runner + 单元测试 | 10h | 3个底座文件通过R/C/P验证 |
| **W2** | **44模块适配** | 全部service加MODULE_META+7个enrich + Registry自动发现验证 | 8h | 42模块注册成功 + 原API回归 |
| **W3** | 信号侦察兵 | signal_scout+cron+推送+前端 + localStorage修 + adminKey迁 + 休市日历 | 10h | 企微收信号 + 非交易日静默 |
| **W4** | 判断追踪器 | judgment_tracker + 埋点 + 验证cron + EMA + 前端成绩单 | 8h | 判断自动记录 + 5日验证 |
| **W5** | 持仓体检 | portfolio_doctor + 压力 + 集中度 + cautious管线 | 7h | 压力测试预估 |
| **W6** | 管家基座 | steward(ask/briefing/review) + regime + 5模块Pipeline串联 | 12h | 问"茅台能买吗"→5模块+Pipeline |
| **W7** | 管家进阶 | llm_gateway + 门控 + 赔率EV + agent重写 + DS迁移 | 12h | 门控直出60% + 负EV拦截 |
| **W8** | 双模式UI | 小白/专业 + 管家聊天 + 企微联动 | 6h | 老婆简洁，你专业 |
| **W9** | 闭环打磨 | 遗传闭环 + RL接入 + 周报 + 技术债清理 | 6h | 全链路无断点 |
| **W10** | 测试上线 | 全流程测试 + 双账号 + 性能 + 备份 + 灰度 | 6h | 稳定运行 |
| | | | **85h** | |

### 回滚安全网

- **W1-W2 底座不影响现有**: 3个新文件+44个文件头部加META → 删除这些改动=恢复原状
- **每Phase开始**: `git tag v4-w{n}-start`
- **底座验证不通过**: 回退到v2.2手动编排方案，只损失W1-W2工时(18h)

---

## 十八、验收标准（v3.0 更新）

| 维度 | 指标 | 达标线 | 来源 |
|---|---|---|:---:|
| 自动化率 | 日常AI自动 | ≥95% | A |
| 人工强度 | 周人工介入 | ≤30min | A |
| LLM调用 | DS日调用 | **≤30次**(门控) | B |
| 链路完整 | 5孤岛串联 | 无断点 | 原 |
| 风控覆盖 | 7类场景 | 100%推送 | A |
| 准确率 | 5日验证 | ≥60% | 原+B |
| 响应速度 | steward | ≤15秒 | 原 |
| 直出率 | 门控 | **≥60%** | B |
| EV过滤 | 负EV拦截 | **100%** | 老师 |
| Regime | 4类分类 | ≥70% | 老师 |
| 多账号 | 隔离 | 零交叉 | 🆕 |
| 铁律 | 20条 | 全通过 | 🆕 |
| **Registry** | **42模块注册** | **100%** | **v3.0** |
| **Context** | **to_llm字段** | **≥95%非空** | **v3.0** |
| **Pipeline** | **3种管线** | **全部可执行** | **v3.0** |

---

## 十九、六方案采纳/驳回记录

（与 v2.2 §十七 完全一致：A风控✅ / B门控+网关+EMA✅ / C参数✅ / 老师Regime+赔率+EV✅ / D成本+休市+记账✅ / E观望+告警+备份✅）

---

## 附录A：44个service完整清单

（与 v2.2 附录A 完全一致 — 行数/角色/V4状态不变，v3.0额外标记: 全部加MODULE_META，7个加enrich）

## 附录B：多账号存储路径规划

（与 v2.2 附录B 完全一致 — 新模块用模式C `data/{uid}/`）

---

## 二十、执行纪律与质量保障

> **为什么要有这一章**: 之前的开发出现了"后端做了前端没接"、"函数名拼错静默失败"、"save_context从未调用"等问题。这些不是设计问题，是**执行纪律**问题。本章定义强制规则，从制度上杜绝。

### 二十.1 每个 Phase 的强制执行流程

```
┌─────────────────────────────────────────────┐
│ Phase 开始                                    │
│  1. git tag v4-w{n}-start                    │
│  2. 读设计文档对应章节（必须，不是"大概知道"） │
│  3. 列出本 Phase 要改的文件清单               │
│  4. 列出本 Phase 的前端对应（哪个 API→哪个Tab）│
├─────────────────────────────────────────────┤
│ 开发中                                        │
│  5. 每写一个后端 API → 立即写前端调用         │
│  6. 每写一个函数 → 立即在调用方 import 验证    │
│  7. 改动 >50 行 → 先跑回归测试清单            │
├─────────────────────────────────────────────┤
│ Phase 完成                                    │
│  8. 跑联合验证组（§十六.4 对应组）            │
│  9. 跑回归测试清单（§十六.5）                 │
│ 10. git tag v4-w{n}-done                     │
│ 11. 写工作日志（本Phase改了什么+验证结果）    │
└─────────────────────────────────────────────┘
```

### 二十.2 防止"不读设计文档就动手"的 3 道关卡

**问题根因**: AI 助手（我）上下文长，容易"觉得自己知道"就直接写代码，跳过设计文档，导致实现和设计不一致。

**关卡 1: Phase 入口必读**

每个 Phase 开始时，我必须执行：
```
read_file("moneybag/docs/moneybag-v4-ultimate-plan.md")  # 读对应章节
```
不是"我记得大概内容"，是**实际调 read_file 读文件**。如果我没读就开始写代码，你可以直接问："你读设计文档了吗？"

**关卡 2: 后端→前端对照表**

每个新增/修改的后端 API 必须在开发时同步标注前端位置：

| 后端 API | 前端调用位置 | 状态 |
|---|---|---|
| `POST /api/steward/ask` | renderChat() 里替换 /chat/stream | ⬜ 待做 |
| `GET /api/steward/briefing` | renderInsightOverview() 顶部卡片 | ⬜ 待做 |

**铁律: 没有前端位置的 API 不允许提交。** "纯后端"的 API（如 cron 内部调用）必须标注 `前端: N/A（cron内部）`。

**关卡 3: 完工自检清单**

每个 Phase 完成后，我必须回答这 5 个问题（不是可选，是必须）：

```
□ 本Phase新增了几个API？每个API前端在哪调用？
□ 本Phase新增了几个函数？每个函数在哪被import+调用？
□ 有没有"写了但没被任何地方调用"的代码？
□ 回归测试跑了吗？结果如何？
□ 和设计文档有偏差吗？如有，更新设计文档。
```

### 二十.3 防止低级错误的 4 道防线

**问题根因**: `get_memory_summary` 写错成非 `build_memory_summary`、`save_context` 写了没调用——这些都是"写了但没验证"。

**防线 1: 写完即验（每个函数级别）**

```python
# 我写完一个函数后，必须立即验证：
# ❌ 错误做法：写完就去写下一个
# ✅ 正确做法：
python -c "from services.xxx import new_function; print('OK')"
```

**防线 2: import 对账（每个文件级别）**

每改一个文件，运行 import 检查：
```bash
cd /opt/moneybag/backend && python -c "
import importlib, sys
for mod in ['services.agent_memory', 'services.agent_engine', ...]:
    try:
        importlib.import_module(mod)
        print(f'✅ {mod}')
    except Exception as e:
        print(f'❌ {mod}: {e}')
"
```

**防线 3: 死代码检测（每个 Phase 完成时）**

```bash
# 搜索"定义了但没被调用"的函数
grep -rn "^def " backend/services/*.py | while read line; do
    func=$(echo "$line" | grep -oP 'def \K\w+')
    count=$(grep -rn "$func" backend/ --include="*.py" | grep -v "^def " | wc -l)
    if [ "$count" -eq 0 ]; then
        echo "⚠️ 未调用: $line"
    fi
done
```

**防线 4: try/except 审计**

```bash
# 搜索所有"静默吞异常"的代码
grep -n "except.*:\s*$\|except.*:.*pass" backend/main.py backend/services/*.py
# 每个 except pass 必须标注"为什么要静默"
```

**特别规则: 涉及 `import` 的 `try/except` 必须至少 `print` 错误信息，不允许纯 `pass`。**
这条规则直接杜绝 `get_memory_summary` 拼错被静默吞掉的问题。

### 二十.4 设计文档同步机制

| 触发条件 | 动作 |
|---|---|
| 实现和设计文档不一致 | **先改设计文档，再改代码**（不是反过来） |
| 新增了设计文档没有的功能 | 更新文档 §对应章节 + 路线图 |
| 发现设计文档遗漏 | 补到文档，标注 `[W{n}发现]` |
| Phase 完成 | 更新文档中对应 Phase 的状态（⬜→✅） |

---

## 二十一、已知 BUG 修复清单（W1 优先修）

> 在 v4 开发过程中审计发现的现有 BUG，W1 第一天优先修复。

| # | BUG | 位置 | 影响 | 修复 | 工时 |
|:---:|---|---|---|---|:---:|
| B1 | `get_memory_summary` 不存在 | main.py L1768, L1841; wxwork.py L126 | DS 聊天记忆注入完全失效 | → `build_memory_summary` | 5min |
| B2 | `save_context()` 从未调用 | agent_memory.py L222（定义）; 全项目 0 处调用 | "上次分析结论"永远为空 | agent_engine.py 分析完成后调用 | 5min |
| B3 | 聊天记录不保存 | /chat/stream 返回后无写操作 | 刷新丢失所有对话 | 追加 chat_log 到 `data/{uid}/chat/` | 30min |
| B4 | chat.py 死代码 | 270行，main.py 不 import | 占空间+误导 | 删除 | 1min |
| B5 | ds_enhance.py L480-484 不可达 | return 后还有代码 | 无实际影响但误导 | 删除不可达代码 | 1min |
| B6 | main.py L1768 应为 `build_memory_summary` | 同 B1，另一个视角 | 被 try/except:pass 吞掉 | 同时改 except 为 `except Exception as e: print(f"[MEM] {e}")` | 5min |

**修复顺序**: B1 → B2 → B6 → B3 → B4 → B5（先修影响最大的）

---

## 二十二、Claude↔DeepSeek 协作与记忆体系

### 二十二.1 数据流通路径

```
Claude（你和我聊天时）
  │
  ├─ 调 API 获取全量数据 → 分析 → 给你建议
  │
  ├─ 写入 decisions.json → DS 下次聊天时通过 build_memory_summary() 读取
  │
  ├─ 写入 context.json（via save_context）→ DS 读取"上次结论"
  │
  └─ 写入 MEMORY.md → 我自己下次会话读取（跨周跨月）

DeepSeek（你在钱袋子 App 聊天时）
  │
  ├─ 读 system_prompt.md（10 Skill 框架）
  │
  ├─ 读 _build_market_context()（45+维度实时数据）
  │
  ├─ 读 _build_portfolio_context()（持仓+风控+配置+持仓智能）
  │
  ├─ 读 build_memory_summary()（偏好+近期决策+规则+上次结论） ← B1修复后生效
  │
  └─ 输出 → 前端显示 + 聊天日志保存（B3修复后） + 决策日志（仅agent/analyze）
```

### 二十二.2 DeepSeek 3 层记忆体系

| 层 | 名称 | 范围 | 存储 | v3.0 实现 |
|:---:|---|---|---|---|
| L1 | 会话记忆 | 当前聊天（10 轮） | 前端内存 + 发给 DS | 前端维护 messages 数组，每次发最近 5 轮 |
| L2 | 结构化记忆 | 跨天/跨周 | `data/{uid}/memory/*.json` | `build_memory_summary()` 注入 prompt |
| L3 | 长期记忆 | 跨月/跨年 | Claude MEMORY.md + 日志 | 我维护，通过 L2 间接传给 DS |

### 二十二.3 DS 输出验证机制（v3.0 新增）

**3 层验证防止 DS "拍脑门"：**

1. **输入审计**: DecisionContext 送 DS 前检查 6 层数据完整性
2. **输出强制**: steward 要求 DS 返回结构化 JSON（不是自由文本）
3. **对账检查**: judgment_tracker 对比 DS 结论 vs 模块共识，发现矛盾则标红

---

## 一句话总结

**v2.2** = steward手动编排6层 → 5模块并行 → 门控省LLM → EV过滤 → 多账号隔离

**v3.0** = **3底座(Registry+Context+Pipeline)让AI自主编排** → 44模块一行不删+META+enrich → Pipeline按Regime动态选 → Context自动累积完整视图给DeepSeek → **每步可验证、每步可回滚** → 从"带嘴的量化排名器"到"自主编排的AI投资管家"

**执行纪律** = 每Phase必读文档 → 后端API必有前端位置 → 写完即验import → 死代码检测 → try/except不允许纯pass → **从制度上杜绝"写了没用"和"拼错了没发现"**
