# 钱袋子 V4 终极方案 — 代码级全维度设计

> 2026-04-14 04:00 | 基于 10 模块深度源码审计 + 图 1（管家架构）+ 图 2（9 大能力）全覆盖
> 
> **本文档是唯一权威版**，替代之前的 roadmap / full-plan。

---

## 〇、审计结论：现状全景图

### 模块串联状态

| 模块 | 文件 | 行数 | 串联状态 | 具体问题 |
|------|------|:----:|:--------:|----------|
| 选股 V3 | `stock_screen.py` | 719 | ✅ 已串联 | 被 portfolio.py 消费，内调 factor_data/stock_data_provider/llm_factor_gen |
| 12 维信号 | `signal.py` | 487 | ✅ 已串联 | 被 main.py daily-signal + ds_enhance 消费 |
| DeepSeek 增强 | `ds_enhance.py` | 485 | ✅ 已串联 | 被 main.py + portfolio.py 调用 |
| **AI 预测** | `ai_predictor.py` | 542 | 🔴 **孤岛** | 仅 main.py 3 个 API 路由惰性 import，预测结果不流向任何决策模块 |
| **遗传因子** | `genetic_factor.py` | 493 | 🔴 **孤岛** | 仅 main.py 1 个路由，挖掘结果不注入 stock_screen 评分 |
| **RL 仓位** | `rl_position.py` | 405 | 🔴 **孤岛** | 仅 main.py 2 个路由，仓位建议不被 stock_monitor/portfolio 消费 |
| **蒙特卡洛** | `monte_carlo.py` | 500 | 🔴 **孤岛** | 仅 main.py 3 个路由，不被其他模块消费 |
| **组合优化** | `portfolio_optimizer.py` | 366 | 🔴 **孤岛** | 仅 main.py 1 个路由 |
| Agent 引擎 | `agent_engine.py` | 246 | ⚠️ 假串联 | 有 Plan-and-Execute 框架但**不调用上述 5 个模块**，本质是文本→DeepSeek→文本 |
| chat.py | `chat.py` | 270 | ❌ **死代码** | main.py 没有 import 它（L1753 自己实现了 /api/chat） |

### 数据源现状

| 数据源 | 使用模块数 | AKShare 接口数 |
|--------|:---------:|:--------------:|
| AKShare | 15+ 个模块 | 40+ 个去重接口 |
| Tushare Pro | 2 个模块（tushare_data + factor_data 间接） | 2 个（daily_basic, fina_indicator） |
| httpx→LLM | 6 个模块 | DeepSeek API |

---

## 一、9 大能力逐项对照（图 2 全覆盖）

| # | 能力 | 现状 | V4 方案覆盖 | 具体实现 |
|---|------|:----:|:----------:|----------|
| 1 | 30 因子排名 | ⚠️ 半实现 | ✅ | stock_screen.py 已串联（不需改），steward 调用时补上遗传因子加分 |
| 2 | 多空辩论 | ⚠️ 半实现 | ✅ | **重写 agent_engine**，从文本拼接→真正调 5 模块收集结构化数据→DeepSeek 仲裁 |
| 3 | AI 涨跌预测 | ⚠️ 孤立 | ✅ | steward.py `_query_ai_predictor()` 拉预测结果，注入综合判断 |
| 4 | 每天扫 1000+ 新闻提取信号 | ❌ 没做 | ✅ | **新建 signal_scout.py**，并发拉 6 大数据源→DeepSeek 提取→匹配持仓→企微推送 |
| 5 | 5 模块交叉验证 | ❌ 没做 | ✅ | **steward.py 核心逻辑**：并行调 5 模块→收集结构化结果→矛盾检测→DeepSeek 仲裁→输出综合判断 |
| 6 | 记住每次判断 + 验证 | ❌ 没做 | ✅ | **新建 judgment_tracker.py**，自动记录→N 日后验证→准确率成绩单→元学习反馈 |
| 7 | 持仓压力测试 | ❌ 没做 | ✅ | **新建 portfolio_doctor.py**，5 情景压力测试 + 集中度深度检查 + AI 诊断处方 |
| 8 | 遗传因子挖掘 | ✅ 有但没闭环 | ✅ | **新增闭环机制**：genetic_factor 挖掘 → 自动 IC 验证 → 通过阈值的因子注入 stock_screen.py |
| 9 | RL 仓位管理 | ✅ 有但没接决策链 | ✅ | **steward.py 调用 RL**，仓位建议作为综合判断输入之一；stock_monitor cron 中也参考 RL |

### ✅ 之前方案的 2 个缺口，现已补齐

> **缺口 1**：遗传因子闭环 — V4 roadmap 未明确如何将挖掘出的因子注入 stock_screen
> → 本文档 §三.1 详细设计了 `_inject_genetic_factors()` 闭环路径
>
> **缺口 2**：RL 仓位接入决策链 — V4 roadmap 未明确如何将 RL 建议接入 steward/stock_monitor
> → 本文档 §三.2 详细设计了 steward 调用 RL + stock_monitor cron 参考 RL

---

## 二、管家大脑完整架构（图 1 全覆盖）

```
                                ┌─────────────────┐
                                │  steward.py      │   ← 新建，管家路由层
                                │  (~350 行)       │
                                └────────┬────────┘
                                         │
                   ┌─────────┬───────┬───┼───┬────────┬────────┐
                   ▼         ▼       ▼   ▼   ▼        ▼        ▼
              ┌─────────┐ ┌──────┐ ┌───┐ ┌──┐ ┌────┐ ┌──────┐ ┌──────┐
              │stock_    │ │ai_   │ │mc │ │rl│ │alt_│ │signal│ │port_ │
              │screen.py │ │pred. │ │.py│ │.py│ │data│ │.py   │ │doctor│
              │(选股)    │ │(预测)│ │(MC)│ │(RL)│ │(另类)│ │(12维)│ │(体检)│
              └─────────┘ └──────┘ └───┘ └──┘ └────┘ └──────┘ └──────┘
                   ▲                                       ▲
                   │                                       │
              genetic_factor.py ──(闭环注入)──→ stock_screen.py
```

### steward.py 核心方法

```python
# services/steward.py (~350行)

class Steward:
    """管家大脑 — 统一编排所有模块，输出用户能理解的结论"""

    def ask(self, user_id: str, question: str) -> dict:
        """
        用户问"茅台能买吗？" → 5 模块并行调用 → 矛盾检测 → DeepSeek 仲裁
        返回: {"verdict": "暂不建议买入，等回调", "confidence": 75, 
               "modules": {...}, "reasoning": "..."}
        """

    def morning_briefing(self, user_id: str) -> dict:
        """
        早间简报 = signal_scout TOP3 + portfolio_doctor 过夜风险 
                  + global_market 美股走势 + macro 经济日历
        压缩到 200 字企微推送
        """

    def closing_review(self, user_id: str) -> dict:
        """
        收盘复盘 = 今日持仓盈亏 + 信号验证 + AI 预测 vs 实际 + 明日展望
        """

    def weekly_report(self, user_id: str) -> dict:
        """
        周报 = judgment_tracker 成绩单 + 持仓体检 + 信号回顾 + 下周展望
        """
```

---

## 三、5 个孤岛模块串联方案（精确到函数调用）

### 三.1 AI 预测引擎 → steward

```python
# steward.py 内部

def _query_ai_predictor(self, code: str) -> dict:
    """
    调用: from services.ai_predictor import predict_stock
    输入: predict_stock(code, forward_days=5)
    返回: {"prediction": 2.35, "direction": "看涨", "confidence": 72.5, 
            "backtest": {"direction_accuracy": 58.3}}
    错误处理: "error" in result → 降级为 "AI 预测不可用，跳过此维度"
    """
    from services.ai_predictor import predict_stock
    result = predict_stock(code, forward_days=5)
    if "error" in result:
        return {"available": False, "error": result["error"]}
    return {
        "available": True,
        "prediction_pct": result["prediction"],      # float, 如 2.35
        "direction": result["direction"],             # "看涨"/"看跌"/"中性"
        "confidence": result["confidence"],           # 0-100
        "accuracy": result["backtest"]["direction_accuracy"],  # 历史准确率
    }
```

### 三.2 RL 仓位 → steward + stock_monitor

```python
# steward.py 内部

def _query_rl_position(self, code: str) -> dict:
    """
    调用: from services.rl_position import get_rl_recommendation
    输入: get_rl_recommendation(code)
    返回: {"recommendations": [...], "market_state": {...}, "training_summary": {...}}
    
    注意: 所有函数都是同步 def，steward 需用 asyncio.to_thread() 包装
    """
    from services.rl_position import get_rl_recommendation
    result = get_rl_recommendation(code)
    if "error" in result:
        return {"available": False, "error": result["error"]}
    
    # 提取当前仓位（假设 50%）对应的建议
    rec_50 = next((r for r in result["recommendations"] 
                   if r["current_position"] == "50%"), None)
    return {
        "available": True,
        "action": rec_50["action"] if rec_50 else "未知",
        "target_position": rec_50["target_position"] if rec_50 else "50%",
        "market_state": result["market_state"],
        "outperformance": result["training_summary"]["outperformance"],
    }

# stock_monitor_cron.py 增强
# 在现有 scan_all_holdings() 后增加:
# if rl_available:
#     rl_advice = get_rl_recommendation(code)
#     if "大幅减仓" in rl_advice → 追加企微预警
```

### 三.3 蒙特卡洛 → steward

```python
# steward.py 内部

def _query_monte_carlo(self, code: str) -> dict:
    """
    调用: from services.monte_carlo import monte_carlo_single
    输入: monte_carlo_single(code, simulations=2000, horizon_days=60)
    注意: 默认 5000 次模拟太慢（~5秒），steward 用 2000 次 60 天短期模拟（~1秒）
    
    返回: {"percentiles": {P10, P50, P90}, "probabilities": {"profit": 62.3},
            "risk_metrics": {"var_95": -8.5, "cvar_95": -12.3}}
    """
    from services.monte_carlo import monte_carlo_single
    result = monte_carlo_single(code, simulations=2000, horizon_days=60)
    if "error" in result:
        return {"available": False, "error": result["error"]}
    return {
        "available": True,
        "profit_probability": result["probabilities"]["profit"],
        "p10_return": result["percentiles"]["P10"],   # 最差 10% 情景
        "p50_return": result["percentiles"]["P50"],   # 中位数
        "p90_return": result["percentiles"]["P90"],   # 最好 10% 情景
        "var_95": result["risk_metrics"]["var_95"],
    }
```

### 三.4 组合优化 → steward

```python
# steward.py 内部

def _query_portfolio_optimizer(self, user_id: str) -> dict:
    """
    调用: from services.portfolio_optimizer import optimize_portfolio
    输入: optimize_portfolio(user_id, method="all", max_weight=0.20)
    
    返回: {"methods": {5种方法结果}, "recommendation": "推荐...", 
            "adjustments": [调仓建议]}
    """
    from services.portfolio_optimizer import optimize_portfolio
    result = optimize_portfolio(user_id, method="all")
    if "error" in result:
        return {"available": False, "error": result["error"]}
    return {
        "available": True,
        "recommendation": result["recommendation"],
        "adjustments": result["adjustments"],
        "best_sharpe": max(
            (m["metrics"]["sharpe_ratio"] for m in result["methods"].values()),
            default=0
        ),
    }
```

### 三.5 遗传因子 → stock_screen 闭环

```python
# 新增: services/genetic_factor_bridge.py (~80行)

"""
遗传因子闭环桥接器
将 genetic_factor.py 挖掘出的因子自动注入 stock_screen.py 评分体系
"""

def inject_genetic_factors(code: str) -> list:
    """
    1. 调 genetic_factor.evolve_factors(code) 获取 top_factors
    2. 过滤: 只保留 IC > 0.03（"有效"以上评级）的因子
    3. 调 factor_ic.py 做独立验证（双重检验）
    4. 通过验证的因子写入 data/genetic_factors_active.json
    5. stock_screen.py 的 _get_llm_factor_bonus() 改为同时查:
       - llm_factor_gen 缓存（现有）
       - genetic_factors_active.json（新增）
    6. 返回注入结果
    
    触发时机: 
    - 每周六 steward 自动触发（非交易日跑，不影响性能）
    - 用户手动在前端点"🧬 挖掘因子"
    """
    from services.genetic_factor import evolve_factors
    from services.factor_ic import calc_factor_ic  # 已有
    
    gf_result = evolve_factors(code, population_size=200, generations=30, top_k=10)
    if "error" in gf_result:
        return []
    
    active_factors = []
    for f in gf_result["top_factors"]:
        if f["ic"] > 0.03:  # 有效以上
            # 独立 IC 验证（不同时间窗口）
            ic_verify = calc_factor_ic(code, factor_expression=f["expression"])
            if ic_verify.get("ic_mean", 0) > 0.02:  # 二次验证通过
                active_factors.append({
                    "expression": f["expression"],
                    "ic": f["ic"],
                    "ic_verified": ic_verify["ic_mean"],
                    "rating": f["rating"],
                    "source": "genetic",
                    "created_at": time.strftime("%Y-%m-%d"),
                })
    
    # 写入活跃因子库
    _save_active_factors(code, active_factors)
    return active_factors

# stock_screen.py 修改点:
# _get_llm_factor_bonus() 增加:
#   genetic_factors = _load_active_genetic_factors(code)
#   if genetic_factors:
#       bonus += sum(f["ic"] * 10 for f in genetic_factors)  # IC→分数
```

---

## 四、新增 4 大模块详细设计

### 四.1 信号侦察兵 `signal_scout.py` (~400 行)

```python
class SignalScout:
    """每天扫描 1000+ 新闻/公告/数据 → 提取投资信号 → 匹配持仓"""

    def collect(self) -> list:
        """
        并发拉取 6 大信息源 (预计 10-15 秒):
        
        ① policy_data.get_policy_news()        → 政策新闻 ~30 条
        ② news_data.get_market_news()           → 市场新闻 ~30 条  
        ③ alt_data.get_northbound_flow_detail()  → 北向资金 + 持股变动
        ④ alt_data.get_insider_trading()         → 增减持
        ⑤ global_market.get_*()                  → 美股/外汇/利率
        ⑥ tushare_data.get_announcements()       → 公告 (新增接口)
        ⑦ tushare_data.get_research_reports()     → 研报摘要 (新增接口)
        
        合计: ~200 条原始信息 → DeepSeek 分 3 批处理
        """

    def extract(self, raw_data: list) -> list:
        """
        DeepSeek 结构化提取 (并行 3 批, 每批 ~70 条):
        
        prompt: prompts/signal_extract.md
        输出: [{
            "signal": "发改委新能源补贴延期",
            "direction": "positive",
            "magnitude": "high",      # low/medium/high
            "sectors": ["新能源汽车", "锂电池"],
            "codes": ["300750", "002594"],
            "logic_chain": "补贴延期→销量上修→需求增→龙头受益",
            "confidence": 0.85,
            "time_horizon": "1-5天",
            "source": "发改委官网"
        }]
        """

    def match(self, signals: list, user_id: str) -> list:
        """
        匹配用户持仓:
        ① 直接命中: signal.codes ∈ user_holdings
        ② 行业命中: signal.sectors ∩ holding_industries
        ③ 输出增加: "relevance_to_you": "你持有宁德时代，直接受益"
        """

    def rank(self, signals: list) -> list:
        """按 magnitude × confidence × relevance 排序, 取 TOP 5"""

    def deliver(self, user_id: str, signals: list):
        """
        企微推送 TOP 3 (纯文本，带逻辑链)
        同时调 judgment_tracker.record_judgment() 埋点
        """
```

**Tushare 新增接口** (tushare_data.py 增加 ~60 行):

```python
def get_announcements(trade_date: str = "") -> list:
    """Tushare anns_d 接口 — 上市公司公告"""
    return _call_tushare("anns_d", {"trade_date": trade_date}, 
                         "ts_code,ann_date,title,content")

def get_research_reports(trade_date: str = "") -> list:
    """Tushare report_rc 接口 — 卖方研报摘要"""  
    return _call_tushare("report_rc", {"trade_date": trade_date},
                         "ts_code,report_date,title,rating,organ")
```

**港股数据** (global_market.py 增加 ~30 行):

```python
def get_hk_market() -> dict:
    """恒生指数 + 港股通资金"""
    # ak.stock_hk_index_daily_sina() — 恒生指数日K
    # ak.stock_hsgt_south_net_flow_in_em() — 南向资金
```

### 四.2 AI 判断追踪器 `judgment_tracker.py` (~300 行)

```python
class JudgmentTracker:
    """追踪每次 AI 判断 → N 天后自动验证 → 准确率成绩单"""

    def record_judgment(self, judgment: dict):
        """
        记录一次判断:
        {
            "id": uuid,
            "source": "signal_scout" | "steward" | "agent_engine",
            "target_code": "300750",
            "direction": "positive",
            "confidence": 0.85,
            "price_at_judgment": 245.30,
            "verify_date": "2026-04-19",  # 5 天后
            "created_at": "2026-04-14"
        }
        存储: data/judgments/{user_id}/{YYYY-MM}.json
        """

    def verify_pending(self, user_id: str):
        """
        每日 16:30 cron 调用:
        1. 加载所有 verify_date <= today 的未验证判断
        2. 拉实际价格 (akshare)
        3. 计算: 方向对不对 + 幅度偏差
        4. 更新 status: "correct" | "wrong" | "neutral"
        """

    def get_scorecard(self, user_id: str, days: int = 30) -> dict:
        """
        成绩单:
        {
            "accuracy_rate": 62.5,      # 方向准确率
            "total_judgments": 48,
            "correct": 30, "wrong": 14, "neutral": 4,
            "best_category": "政策信号 (78%)",
            "worst_category": "技术面预测 (43%)",
            "trend": "↗️ 近 7 天准确率 68% (上升)",
            "insights": "AI 在宏观政策判断上最准，短期技术预测较弱"
        }
        """

    def learn_from_history(self, user_id: str) -> dict:
        """
        元学习 (每周执行一次):
        1. 分析哪类信号最准/最差
        2. 调整 signal_scout 的 confidence 权重
        3. 调整 steward 的模块权重 (如 AI 预测准确率低→降低权重)
        """
```

### 四.3 持仓体检师 `portfolio_doctor.py` (~350 行)

```python
class PortfolioDoctor:
    """持仓体检 — 压力测试 + 集中度检查 + AI 诊断"""

    SCENARIOS = {
        "美联储加息50bp":    {"成长股": -0.08, "银行": +0.03, "债券": -0.05, "黄金": -0.02},
        "中美关系恶化":      {"科技": -0.15, "内需": +0.02, "黄金": +0.05, "军工": +0.08},
        "房地产暴雷":        {"地产": -0.20, "银行": -0.10, "基建": +0.05},
        "疫情反复":          {"医药": +0.10, "消费": -0.08, "线上": +0.05},
        "A股系统性暴跌20%": {"全市场": -0.20},
    }

    def stress_test(self, user_id: str, scenario: str = "all") -> dict:
        """
        1. 获取用户持仓 (stock_monitor.get_stock_holdings)
        2. 查持仓行业 (holding_intelligence._fetch_industry_safe)
        3. 按情景 beta 计算预估损失
        4. 输出: {scenario, portfolio_impact, worst_holdings, hedging_suggestion}
        """

    def concentration_check(self, user_id: str) -> dict:
        """
        深度集中度检查 (增强现有纪律检查):
        - 行业集中度 (现有: >30% 警告)
        - 风格集中度 (新增: 全成长 or 全价值 → 警告)
        - 市值集中度 (新增: 全大盘 or 全小盘 → 警告)
        - 相关性检查 (新增: 任意两只相关系数>0.8 → "假分散")
        
        调用: 
        - portfolio_optimizer._get_returns_matrix() → 计算相关矩阵
        - stock_screen 的因子分类 → 判断风格/市值
        """

    def diagnose(self, user_id: str) -> dict:
        """
        AI 综合诊断:
        1. 调 stress_test(all) + concentration_check()
        2. 调 monte_carlo_portfolio() 获取尾部风险
        3. 调 ai_predictor.predict_portfolio() 获取组合预测
        4. 以上结构化数据 → DeepSeek prompt: 
           "基于持仓结构+市场环境+压力测试，组合有什么健康问题？"
        5. 输出: {health_score: 72, issues: [🔴/🟡], prescriptions: [💊]}
        """
```

### 四.4 管家路由层 `steward.py` (~350 行)

**核心：`ask()` 的 5 模块交叉验证详细流程**

```python
def ask(self, user_id: str, question: str) -> dict:
    """
    用户问 "茅台能买吗？"
    
    Step 1: 意图识别 (简单规则 + LLM 兜底)
    → 提取 target_code = "600519"
    
    Step 2: 并行调 5 模块 (asyncio.gather + to_thread)
    ┌──────────────────────────────────────────────────────┐
    │ Module A: stock_screen 排名                          │
    │   → screen_stocks(top_n=20) 里有没有 600519          │
    │   → 结果: "排名第 3/20，综合评分 85/100"             │
    │                                                      │
    │ Module B: ai_predictor 预测                          │
    │   → predict_stock("600519", forward_days=5)          │
    │   → 结果: "预测 5 天跌 -2.3%，置信度 65%"           │
    │                                                      │
    │ Module C: monte_carlo 模拟                           │
    │   → monte_carlo_single("600519", sims=2000, days=60) │
    │   → 结果: "P10=-8%, P50=+1.5%, 盈利概率 58%"        │
    │                                                      │
    │ Module D: rl_position 仓位建议                       │
    │   → get_rl_recommendation("600519")                  │
    │   → 结果: "当前市场建议持仓 50%→40% (小幅减仓)"     │
    │                                                      │
    │ Module E: signal_scout 相关信号                      │
    │   → 查最近 24h 信号库中与 600519 相关的信号          │
    │   → 结果: "无直接信号" or "白酒板块有政策风险"        │
    │                                                      │
    │ Bonus: alt_data 北向资金                             │
    │   → 查北向持股 TOP 15 有没有 600519                  │
    │   → 结果: "北向 3 天净流出 / 净流入"                 │
    └──────────────────────────────────────────────────────┘
    
    Step 3: 矛盾检测
    → 分别判断每个模块的结论方向: bullish/bearish/neutral
    → 统计: 3 bullish vs 2 bearish → 意见分歧
    
    Step 4: DeepSeek 仲裁 (prompt: prompts/steward_arbitrate.md)
    → 输入: 5 个模块的结构化结果 + 用户问题 + 用户持仓
    → 指令: "综合以上 5 个维度的数据，给出一个明确的买卖建议"
    → 要求输出格式:
      {
        "verdict": "暂不建议买入，等回调到 1400 以下再考虑",
        "confidence": 65,
        "bull_factors": ["因子评分 85 分排名前 3", "北向 TOP 15 持仓"],
        "bear_factors": ["AI 预测 5 天跌 2.3%", "MC P10=-8%", "RL 建议减仓"],
        "key_conflict": "因子面看好但短期预测偏空，核心矛盾在于时间维度",
        "action_plan": "短期观望(1-2周)，跌到 1400 以下可分批建仓 20%",
        "monitor_triggers": ["如果北向资金连续 3 天净流入 > 50 亿 → 重新评估"]
      }
    
    Step 5: 记录判断 (judgment_tracker.record_judgment)
    
    返回: 完整结构化报告
    """
```

---

## 五、数据源完整整合方案

### 现有数据源（55+ 接口，已在用）

| 类别 | 接口数 | 来源 | 状态 |
|------|:------:|------|:----:|
| A 股行情 | 3 | AKShare(东财/新浪/雪球) | ✅ |
| 估值指标 | 5 | AKShare + Tushare | ✅ |
| 资金面 | 4 | AKShare(北向/两融/主力) | ✅ |
| 宏观数据 | 8 | AKShare(CPI/PMI/M1/社融/LPR/涨跌家数) | ✅ |
| 另类数据 | 7 | AKShare(龙虎榜/大宗/增减持/行业资金流) | ✅ |
| 新闻资讯 | 5 | AKShare(东财新闻/政策新闻) | ✅ |
| 全球市场 | 5 | AKShare(美股/外汇/美联储) | ✅ |
| 基金相关 | 5 | AKShare(净值/排行/持仓) | ✅ |
| 大宗商品 | 3 | AKShare(黄金/铜/ETF) | ✅ |
| 财务数据 | 3 | Tushare(PE/PB/ROE) + AKShare 降级 | ✅ |
| LLM 情绪 | 1 | DeepSeek(新闻标题→情绪评分) | ✅ |

### V4 新增数据源（6 项）

| # | 数据源 | 接口 | 模块 | 工时 |
|---|--------|------|------|:----:|
| 1 | **Tushare 公告** | `anns_d` | tushare_data.py + signal_scout | 30min |
| 2 | **Tushare 研报** | `report_rc` | tushare_data.py + signal_scout | 30min |
| 3 | **港股恒生指数** | `stock_hk_index_daily_sina` | global_market.py | 30min |
| 4 | **港股通(南向)资金** | `stock_hsgt_south_net_flow_in_em` | global_market.py + alt_data | 30min |
| 5 | **行业分类映射** | 雪球 `stock_individual_info_em` | holding_intelligence.py(已有) | 0 |
| 6 | **持仓相关矩阵** | 复用 portfolio_optimizer._get_returns_matrix | portfolio_doctor.py | 0 |

### 数据流向全景图

```
                        ┌── AKShare (40+ 接口) ──┐
                        │                         │
                        ├── Tushare (4 接口)  ────┤
                        │                         │
外部数据源 ──────────── ├── DeepSeek LLM ─────────┤ ──→ 6 个数据模块
                        │                         │       │
                        └── 用户输入 ──────────────┘       │
                                                           ▼
           ┌────── data_layer.py (Facade, 28行) ─────────────┐
           │                                                   │
           ├── market_data.py    (基金净值/恐贪/估值)          │
           ├── technical.py      (RSI/MACD/布林)               │
           ├── news_data.py      (东财/政策新闻)               │
           ├── macro_data.py     (CPI/PMI/M2/PPI)             │
           ├── factor_data.py    (北向/两融/国债/SHIBOR/情绪)  │
           ├── fund_rank.py      (基金排行)                    │
           ├── stock_data_provider.py (A股行情多源)            │
           ├── tushare_data.py   (PE/PB/财务 + 新增公告/研报)  │
           ├── alt_data.py       (6维另类数据仪表盘)           │
           ├── macro_extended.py (M1/社融/LPR/涨跌家数)        │
           ├── macro_v8.py       (V8宏观: 大宗/ETF/限售)       │
           ├── global_market.py  (美股/外汇/利率 + 新增港股)    │
           └── holding_intelligence.py (个股新闻/资金/行业)     │
                                                               │
                    ┌──────────────────────────────────────────┘
                    │
                    ▼
           ┌── 功能模块层 ────────────────────────────────────┐
           │                                                    │
           ├── stock_screen.py     (30因子选股V3)               │
           ├── signal.py           (12维综合信号)               │
           ├── ai_predictor.py     (MLP+GBM预测)               │
           ├── genetic_factor.py   (遗传因子挖掘)              │
           ├── monte_carlo.py      (蒙特卡洛模拟)              │
           ├── rl_position.py      (RL仓位管理)                │
           ├── portfolio_optimizer.py (5种组合优化)             │
           ├── ds_enhance.py       (DeepSeek增强层)            │
           ├── backtest_engine.py  (回测引擎)                  │
           ├── factor_ic.py        (因子IC检验)                │
           ├── llm_factor_gen.py   (LLM因子生成)               │
           ├── 🆕 signal_scout.py  (信号侦察兵)               │
           ├── 🆕 judgment_tracker.py (判断追踪器)             │
           ├── 🆕 portfolio_doctor.py (持仓体检师)             │
           └── 🆕 genetic_factor_bridge.py (遗传因子闭环)      │
                    │                                           │
                    ▼                                           │
           ┌── steward.py (管家路由层) ← 🆕 核心 ──────────────┘
           │   调用上面所有模块，输出综合判断
           │
           ├── stock_monitor.py    (持仓盯盘)
           ├── stock_monitor_cron.py (定时盯盘)
           ├── fund_monitor.py     (基金盯盘)
           ├── portfolio.py        (配资建议)
           ├── wxwork_push.py      (企微推送)
           └── agent_engine.py     (决策引擎 → 重写为调steward)
                    │
                    ▼
           ┌── main.py (API层, ~3100行) ──────────────────────┐
           │   140+ API路由                                     │
           │   新增: /api/steward/* (4个路由)                   │
           │   新增: /api/signal-scout/* (2个路由)             │
           │   新增: /api/judgment/* (3个路由)                  │
           │   新增: /api/portfolio-doctor/* (3个路由)          │
           └───────────────────────────────────────────────────┘
```

---

## 六、需清理的技术债

| # | 问题 | 动作 | 优先级 |
|---|------|------|:------:|
| 1 | `chat.py` 270 行死代码 | 删除文件 | P2 |
| 2 | `ds_enhance.py` L480-484 return 后死代码 | 删除 | P2 |
| 3 | `agent_engine.py` 假串联 | Phase 4 重写为调 steward | P1 |
| 4 | main.py 3039 行臃肿 | 持续拆分到 routers/ | P2 |
| 5 | factor_data + alt_data 重复调北向/两融 | 统一为 alt_data 调用 | P3 |

---

## 七、6 周实施路线图（更新版）

```
Week 1 (Phase 1)     Week 2 (Phase 2)     Week 3 (Phase 3)
信号侦察兵 ~8h       判断追踪器 ~5.5h     持仓体检师 ~7h
├ 数据源补齐 (2h)    ├ 核心 tracker (2h)  ├ 压力测试 (2h)
├ signal_scout (2h)  ├ 埋点 (1h)          ├ 集中度检查 (1h)
├ 信号提取 prompt(1h)├ 验证 cron (30m)    ├ AI 诊断 (1h)
├ 持仓匹配 (30m)     ├ 元学习 (30m)       ├ 相关性/风格 (1h)
├ cron + 推送 (1h)   ├ 前端成绩单 (1h)    ├ 前端体检页 (1.5h)
├ 前端信号页 (1h)    └ 企微周报 (30m)     └ 企微推送 (30m)
└ 部署验证 (30m)

Week 4 (Phase 4)     Week 5 (Phase 5)     Week 6 (Phase 6)
管家路由层 ~8h       双模式UI ~6h         打磨+复盘 ~5h
├ steward.py (2h)    ├ 小白模式首页 (3h)  ├ 遗传因子闭环 (2h)
├ 5模块串联 (2h)     ├ 专业模式开关 (1h)  ├ RL→盯盘cron (1h)
├ 矛盾检测 (1h)      ├ 老婆测试 (1h)      ├ 全流程测试 (1h)
├ agent重写 (1h)     └ 企微联动 (1h)      └ 文档更新 (1h)
├ chat→steward (1h)
└ API + 部署 (1h)

总工时: ~39.5h (预估 AI 编码实际 ~20h，因复用大量现有模块)
```

### 每周核心交付物

| 周 | 交付 | 验证标准 |
|:--:|------|----------|
| 1 | 每天 08:30 企微收到 TOP3 信号 + 逻辑链 | 信号覆盖政策/新闻/资金 3 大类 |
| 2 | 每次 AI 判断自动记录 + 5 天后验证 | 首页能看到准确率趋势 |
| 3 | 输入"如果中美恶化" → 看到持仓预估损失 | 压力测试结果合理 |
| 4 | 问"茅台能买吗" → 5 模块并行 → 综合结论 | 结论包含多空因素 + 矛盾分析 |
| 5 | 老婆打开看简洁界面，你切专业模式 | 两种模式信息量差 3 倍以上 |
| 6 | 遗传因子自动注入选股 + RL 接入盯盘 | 全链路闭环无断点 |

---

## 八、一句话总结

**现在的钱袋子 = 5 个孤岛模块各自为政 + 1 个假串联的 agent_engine**

**V4 做完后 = steward 统一编排 → 5 模块交叉验证 → 矛盾检测 → DeepSeek 仲裁 → 一句用户能懂的结论**

从"带嘴的量化排名器" → 真正的"AI 投资管家"。

所有细节、所有模块、所有数据源、所有函数调用路径——都在这份文档里了。
