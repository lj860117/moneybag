# MoneyBag 全量体检报告

> **检测日期**：2026-04-19（周日，非交易日）
> **环境**：本地 Python 3.11.9 + uvicorn + .env 全量 Key
> **测试账号**：`qa_test_20260419`
> **持仓**：茅台(600519) / 五粮液(000858) / 宁德时代(300750) + 沪深300ETF(510300) / 交银裕隆纯债A(519736)
> **代码版本**：APP_VERSION="7.1.0"（config.py）/ FastAPI version="6.0.0-phase0"（main.py）

---

## 🔴 目录（按严重度排序）

| 级别 | 数量 | 含义 |
|------|------|------|
| **P0 — 必须修** | 6 | 数据错误/用户看到错误结果/核心功能不可用 |
| **P1 — 应该修** | 8 | 影响分析质量/判断偏差/专业性降低 |
| **P2 — 可以修** | 10 | 体验优化/代码卫生/维护性提升 |
| **设计建议** | 7 | 非 Bug，但值得考虑的架构改进 |

---

## 🔴 P0 — 必须修（6 个）

### F1: Sortino 比率计算严重错误
- **文件**：`services/backtest.py:184`
- **问题**：`risk_free_monthly = 0.15`，注释说"假设年化 1.8%/12"，但 0.15 = **15%/月 = 180%/年**！正确值应为 `0.0015`
- **影响**：Sortino 比率计算完全失真。因为阈值过高（15%/月），几乎所有月度收益都被归为"下行"，Sortino 永远为负或极小
- **修复**：`risk_free_monthly = 0.0015`（年化 1.8% ÷ 12）
- **工时**：5 分钟

### F2: 非交易日 AI 分析数据全 null（"巧言令色"问题）
- **文件**：`services/stock_monitor.py:167-215`
- **问题**：`get_stock_realtime()` 在非交易日从东方财富/雪球获取不到数据，返回全 null。但 `stock-holdings/analyze` 接口仍然调用 DeepSeek 生成分析文字 → **AI 在没有任何实时数据的情况下"编"出了详细分析**
- **证据**：
  ```json
  // scan.holdings 里每只股票：
  {"price": null, "changePct": null, "indicators": {"rsi14": null, "macd_trend": null, ...}, "signals": []}
  // 但 AI 分析文字洋洋洒洒 500+ 字，包含"PE约28倍"、"RSI偏高"等数据
  ```
- **影响**：**用户看到的 AI 分析看似专业，实际是 LLM 幻觉**。非交易日 + 东方财富被反爬时都会触发
- **修复方向**：
  1. `get_stock_realtime()` 增加降级：获取不到实时数据时，用最近一个交易日的收盘数据
  2. AI 分析 prompt 中明确注入"以下字段为 null，请不要编造"
  3. 前端显示数据时间戳，让用户知道数据截止日期
- **工时**：2-3 小时

### F3: 持仓存储双轨分裂
- **文件**：`services/persistence.py` vs `services/stock_monitor.py` / `services/fund_monitor.py`
- **问题**：MoneyBag 有两套完全独立的持仓存储，互不通信：
  - **V4 路径**：`portfolio.transactions[]` → 用于 signal/risk/allocation/backtest
  - **Monitor 路径**：`stock_holdings_{userId}.json` + `fund_holdings_{userId}.json` → 用于 overview/monitor/AI分析
- **影响**：用户通过 V4 交易流水买入的股票，在 overview 和 AI 分析中**完全看不到**（反之亦然）
- **修复方向**：统一为单数据源，或建立同步桥接层
- **工时**：4-8 小时（取决于选择合并还是桥接）

### F4: 选股权重 stock_screen.py 与 config.py 不一致
- **文件**：`services/stock_screen.py:38-46` vs `config.py:53-61`
- **问题**：
  ```python
  # stock_screen.py（实际使用的）
  "quality": 0.18    # ← 质量维度 18%
  
  # config.py（声称的 Single Source of Truth）
  "quality": 0.15    # ← 质量维度 15%
  ```
- **影响**：config.py 注释声称"禁止在业务代码里写魔法数字"，但 stock_screen.py 有自己的一套权重且不读 config.py 的。选股排名结果与配置文件声称的不一致
- **修复**：stock_screen.py 改为从 config.py 读取 `STOCK_SCREEN_WEIGHTS`
- **工时**：30 分钟

### F5: risk.py 回撤计算用假数据
- **文件**：`services/risk.py:82`
- **问题**：`peak = total_cost * 1.1`（注释：简化——假设历史高点）
- **影响**：回撤计算完全失真。假设你投了 10 万，高点 15 万，跌到 12 万，实际回撤 20%，但这里算出来只有 `(11万-12万)/11万 ≈ -9%`。**用户看到"回撤正常"，但实际已经该止损了**
- **修复**：从历史净值数据中计算真实峰值
- **工时**：1-2 小时

### F6: risk.py 资产类型判断用硬编码基金代码
- **文件**：`services/risk.py:104-106`
- **问题**：
  ```python
  stock_count = sum(1 for h in active if h["code"] in ["110020", "050025", "008114"])
  bond_count = sum(1 for h in active if h["code"] in ["217022"])
  gold_count = sum(1 for h in active if h["code"] in ["000216"])
  ```
- **影响**：这段代码**只认这 5 个特定基金代码**。用户持有任何其他基金（包括 QA 测试的 510300/519736），`stock_count`、`bond_count`、`gold_count` 全部为 0 → 相关性分析直接跳到 `avg = 0.5` 的默认值 → **所有用户得到的相关性提示都是无意义的**
- **修复**：按基金类型（股票型/混合型/债券型/货币型/黄金）分类判断，而非硬编码代码列表
- **工时**：1 小时

---

## 🟡 P1 — 应该修（8 个）

### F7: ai-predict 接口缺 sklearn 依赖
- **文件**：`services/ai_predictor.py` + `requirements.txt`
- **问题**：`ModuleNotFoundError: No module named 'sklearn'`
- **影响**：AI 预测功能完全不可用
- **修复**：`requirements.txt` 添加 `scikit-learn`
- **工时**：5 分钟

### F8: requirements.txt 还缺 pycryptodome 和 tushare
- **文件**：`requirements.txt`
- **问题**：线上靠手动 `pip install` 装过了，但新环境部署会直接报错
- **修复**：补全依赖列表
- **工时**：5 分钟

### F9: rl_position.py 训练崩溃
- **文件**：`services/rl_position.py:228`
- **问题**：`TypeError: float() argument must be a string or a real number, not 'dict'`
- **影响**：强化学习仓位建议模块静默失败，Pipeline 降级但不报错
- **修复**：检查训练数据输入格式，做好 dict/float 类型适配
- **工时**：30 分钟 - 1 小时

### F10: 东方财富数据源不稳定，降级链路过慢
- **证据**：
  ```
  [DATA_PROVIDER] 东方财富 FAIL → 降级到新浪+雪球 → 新浪 OK: 5505 stocks in 29.4s
  ```
- **问题**：东方财富被反爬是常态，降级到新浪耗时 29 秒 + 雪球 PE 补充 26 秒 = **首次加载 55+ 秒**
- **影响**：用户等待时间太长；非交易日行情接口全部失败
- **修复方向**：
  1. 启动时预加载数据（已有 `precomputed_cache.py`，但需检查生效范围）
  2. 非交易日检测 → 直接用缓存 → 标注"截至 X 日收盘"
- **工时**：2-3 小时

### F11: GLOBAL_PE 中美 PE 相同，数据源错误
- **证据**：`[GLOBAL_PE] 中美PE相同(75.34)，疑似数据源错误，标记不可用`
- **问题**：已有自检逻辑检测到问题，但仅打日志，不影响上层。但 AI 分析可能仍引用了错误的全球 PE 数据
- **修复**：确认数据源 API 是否返回正确值
- **工时**：1 小时

### F12: 版本号不一致
- **文件**：`config.py:113` vs `main.py:66`
- **问题**：`APP_VERSION = "7.1.0"` vs `FastAPI(version="6.0.0-phase0")`
- **影响**：混乱，/docs 页面显示错误版本
- **修复**：统一引用 config.py 的 APP_VERSION
- **工时**：5 分钟

### F13: NAV 获取返回 HTML（SyntaxError: Unexpected token '<'）
- **证据**：
  ```
  [NAV] Failed to fetch 600519: <anonymous>:2: SyntaxError: Unexpected token '<'
  ```
- **问题**：股票代码 600519 不是基金，但 NAV 接口尝试按基金方式获取净值，返回了 HTML 错误页
- **影响**：无实际影响（stock 走 realtime 接口），但日志噪音大
- **修复**：NAV 接口加入 code 类型判断，股票代码直接跳过
- **工时**：15 分钟

### F14: Steward Pipeline 4 模块结果"消失"
- **证据**：steward/ask 返回 `modules_called` 16 个，但 `modules_results` 只有 12 个，缺失：
  - `earnings_forecast` — 盈利预测
  - `business_exposure` — 业务敞口
  - `rl_position` — RL 仓位建议（已知崩溃，F9）
  - `valuation_engine` — DCF 估值
- **问题**：这 4 个模块在执行中出错或被跳过，但 Pipeline 静默吞掉了错误
- **影响**：用户不知道分析少了 4 个维度。如果这些模块正常工作，分析结论可能完全不同
- **修复方向**：
  1. 修复 rl_position 崩溃（F9）
  2. Pipeline 在返回结果中增加 `modules_errors` 和 `modules_skipped` 字段
  3. 前端显示"以下模块执行失败：xxx"
- **工时**：2 小时

---

## 🟢 P2 — 可以修（10 个）

### F15: portfolio-overview vs portfolio/overview 路径混淆
- SPA fallback 会把错误路径静默返回 HTML，不报 404
- 建议统一 API 命名规范

### F16: API 路径发现困难
- 201 条路由在 main.py 里，没有 API 文档或 /docs 引导
- 建议利用 FastAPI 自带的 OpenAPI 文档

### F17: TREASURY 获取失败
- `[TREASURY] Failed: name 'get_valuation_percentile' is not defined`
- 国债收益率模块引用了不存在的函数

### F18: Brent 原油数据获取失败
- `[COMMODITY] Brent(foreign_hist) fail: Expected object or value`
- 外盘数据源不稳定

### F19: ETF 流量数据获取失败
- `[ETF_FLOW] fund_etf_spot_em failed: Connection aborted`
- 东方财富反爬影响

### F20: valuation_engine DCF 折现率硬编码 10%
- 对成长股（宁德时代）和价值股（茅台）使用相同折现率不合理
- 建议根据行业/Beta 动态调整

### F21: 配置调整步长硬编码在 portfolio.py
- 估值极度高估 → 减股 10%，这个"10%"应该进 config.py

### F22: 恐贪指数三维权重硬编码
- market_data.py:123 `dim1_score * 0.4 + dim2_score * 0.3 + dim3_score * 0.3`
- 应提取到 config.py

### F23: 基金筛选时间权重硬编码
- fund_screen.py 各期限收益率权重散落在业务代码中

### F24: judgment_tracker 默认模块权重与 pipeline 不完全对齐
- judgment_tracker 的 9 个模块权重 vs pipeline 实际调用 16 个模块，有些模块没被追踪

---

## 💡 设计建议（7 条）

### D1: AI 分析的"含金量"评估
**结论：AI 分析内容 70% 来自真实数据，30% 可能是幻觉**

| 接口 | 实际数据源 | 含金量 |
|------|-----------|--------|
| **steward/ask** | 16 模块并行 → LLM 仲裁。12/16 模块有数据 | **★★★★☆** 最高质量，但 4 模块静默失败 |
| **agent/analyze** | stock_holdings 扫描 + DeepSeek 10 Skill 分析 | **★★★☆☆** 非交易日全 null 时变成纯幻觉 |
| **ai-comment/stock** | 简单基本面 + 新闻 → 一句话 | **★★☆☆☆** PE/ROE=0 时照样输出 |
| **ai-predict** | sklearn ML 模型 | **☆☆☆☆☆** 依赖缺失，完全不可用 |

**与机构研报的对比**：
- MoneyBag steward 的多空辩论框架是**独特优势**，券商研报通常只有看多
- 但 MoneyBag 缺少：盈利预测拆分、可比公司估值、行业竞争格局、管理层分析
- 数据维度上 MoneyBag 有 45+ 维度（AKShare+Tushare），接近付费终端的 60-70%
- 核心差距：**实时性**（非交易日/反爬）和**公司特定数据**（财报细项拆分）

### D2: 非交易日策略
当前：非交易日所有实时接口失败 → null → AI 幻觉
建议：
1. 启动时检测是否交易日（`is_trading_day()`）
2. 非交易日 → 从本地缓存/预计算数据加载最近一个交易日数据
3. 所有数据标注时间戳，AI prompt 注入"数据截止 YYYY-MM-DD 收盘"
4. 前端顶部显示"📅 数据截至 4/18 收盘（非交易日）"

### D3: 硬编码参数集中治理
当前 70+ 处硬编码散落在 20+ 个文件中，config.py 声称"禁止在业务代码写魔法数字"但未执行。
建议按领域分组迁移：
- `config.py` → 风控阈值、信号权重、配置比例（已有 ~35 处 ✅）
- `config_valuation.py`（新建）→ DCF 参数、PE/PEG 评分阈值
- `config_backtest.py`（新建）→ 无风险利率、止盈止损参数
- `config_rl.py`（新建）→ RL 状态空间、奖励函数参数

### D4: 小白可用性改进方向（快速扫描）
- ❌ 专业术语无解释：PE-TTM、RSI、MACD、Sortino、夏普比率、CVaR、HRP — 前端直接显示数字，没有 tooltip/解释
- ❌ 操作引导缺失：首次进入页面没有引导流程，不知道先添加持仓还是先设置风险偏好
- ❌ AI 分析结果过长（500+字），小白看不到重点
- ✅ 多空辩论框架（🟢多头 / 🔴空头 / ⚖️仲裁）天然适合小白理解
- ✅ emoji 使用得当，视觉引导明确
- 建议：增加"小白模式"开关，简化显示 + 术语解释 tooltip

### D5: steward Pipeline 耗时优化
当前 steward/ask 耗时 87 秒，主要瓶颈：
- 16 个模块串行 or 半并行
- 东方财富降级到新浪 55 秒
- DeepSeek LLM 调用 15-20 秒
建议：
1. 利用预计算缓存减少实时计算
2. 模块分优先级：fast（5个核心模块 < 10s）→ 先返回初步结论 → 后台继续深度分析
3. 流式返回（SSE）让用户看到进度

### D6: 数据源健壮性
| 数据源 | 调用量 | 稳定性 | 建议 |
|--------|--------|--------|------|
| AKShare | 117+ 处 | ⚠️ 东方财富反爬频发 | 增加缓存层 + 离线模式 |
| Tushare | 21 处 | ✅ 稳定但有积分限制 | 扩大使用范围替代 AKShare 不稳定接口 |
| DeepSeek | 12+ 文件 | ✅ 稳定 | 增加 prompt 中的数据质量声明 |
| 新浪 | 降级备用 | ✅ 稳定但字段少 | 维持现状 |
| 雪球 | 降级备用 | ⚠️ 偶尔超时 | PE 数据补充有价值 |

### D7: 测试覆盖
当前 0 个自动化测试。关键模块建议优先添加：
1. `backtest.py` — 数值计算正确性
2. `risk.py` — 回撤/相关性/阈值判断
3. `pipeline_runner.py` — 门控逻辑
4. `stock_screen.py` — 权重计算和排名

---

## 📊 修复工时估算

| 优先级 | 数量 | 总工时 | 建议排期 |
|--------|------|--------|---------|
| P0 | 6 | 10-16 小时 | 本周必修 |
| P1 | 8 | 7-10 小时 | 本周尽量 |
| P2 | 10 | 5-8 小时 | 下周 |
| 设计建议 | 7 | 需评估 | V8 规划 |

**建议修复顺序**：
1. **F1** Sortino 错误（5分钟，立即修）→ F7/F8 补依赖（5分钟）→ F12 版本号统一（5分钟）
2. **F2** 非交易日数据降级（2h）→ **F5** 回撤真实峰值（1h）→ **F6** 资产类型判断（1h）
3. **F3** 双轨存储统一（4-8h，最大工作量）
4. **F4** 权重统一（30分钟）→ **F9** RL 修复（1h）→ **F14** Pipeline 错误透出（2h）
5. **F10** 数据源降级优化（2-3h）

---

## 附录 A：API 接口测试详情

### 测试 1: `POST /api/steward/ask`
```bash
curl -X POST "http://127.0.0.1:8000/api/steward/ask" \
  -H "Content-Type: application/json" \
  -d '{"userId":"qa_test_20260419","question":"分析一下贵州茅台600519现在值不值得买"}'
```
- **状态**：✅ 返回 200
- **耗时**：86.8 秒
- **Pipeline**：default（9 步）
- **Regime**：trending_bull（趋势牛市）
- **模块调用**：16 个（ai_predictor, broker_research, business_exposure, earnings_forecast, factor_data, geopolitical, market_factors, monte_carlo, news_data, risk, rl_position, sector_rotation, signal, signal_scout, stock_screen, valuation_engine）
- **有效结果**：12/16（缺 earnings_forecast, business_exposure, rl_position, valuation_engine）
- **LLM 调用**：1 次 deepseek-chat
- **结论**：neutral / 55% / "财务稳健但增长放缓，市场分歧大，建议观望"
- **EV**：4.67（正期望值，可出手但不强烈）

### 测试 2: `POST /api/agent/analyze`
```bash
curl -X POST "http://127.0.0.1:8000/api/agent/analyze" \
  -H "Content-Type: application/json" \
  -d '{"userId":"qa_test_20260419","code":"600519"}'
```
- **状态**：✅ 返回 200
- **内容**：10 Skill 框架全量分析（异动原因/技术面/资金面/关联分析/估值/基本面/情绪/风险收益比/仓位管理/操作建议）
- **Alerts**：3 条（交银估算偏差、交银回撤 4.5%、沪深300ETF 回撤 6.1%）
- **结论**：bearish / 70% / 建议减仓

### 测试 3: `POST /api/stock-holdings/analyze`
```bash
curl -X POST "http://127.0.0.1:8000/api/stock-holdings/analyze" \
  -H "Content-Type: application/json" \
  -d '{"userId":"qa_test_20260419"}'
```
- **状态**：✅ 返回 200
- **⚠️ 问题**：`scan.holdings` 中所有股票的 `price`/`changePct`/`indicators` 全为 **null**（非交易日无实时数据）
- **⚠️ 问题**：AI 仍然生成了看似专业的分析（"PE约28倍"等），但这些数据是 LLM 幻觉

### 测试 4: `GET /api/ai-comment/stock?code=600519`
- **状态**：✅ 返回 200
- **⚠️ 问题**：返回"基本面数据异常（PE/ROE为0）"，但仍然给出了分析
- 分析内容来自新闻（"茅台2025年利润罕见下滑4.5%"），不含实时行情

### 测试 5: `GET /api/ai-predict/600519`
- **状态**：❌ 失败
- 第一次：`No module named 'sklearn'`（已修复安装）
- 第二次：`数据不足：需要200+天，只有0天`（非交易日无法获取历史数据？）

### 测试 6: `GET /api/portfolio/overview?userId=qa_test_20260419`
- **状态**：✅ 返回 200
- **数据正确**：3 股 2 基金全部显示，健康评分 75 分
- 配置偏离度正确计算（股 63.2% vs 目标 50%，偏高 13.2%）

---

## 附录 B：硬编码魔法数字完整清单

> 共发现 **70+ 处**硬编码值散落在 20+ 个文件中

### 极高影响（15 处）

| 文件 | 行号 | 值 | 场景 | 问题 |
|------|------|-----|------|------|
| backtest.py | 184 | `0.15` | Sortino 无风险利率 | **P0 BUG** 应为 0.0015 |
| risk.py | 82 | `total_cost * 1.1` | 历史高点 | **P0** 假设失真 |
| risk.py | 104-106 | `["110020","050025","008114"]` | 资产类型判断 | **P0** 硬编码代码列表 |
| risk.py | 109 | `0.75` | 全股组合相关系数 | **P1** 非真实计算 |
| valuation_engine.py | 249 | `0.10` | DCF 折现率 | 对所有行业一刀切 |
| valuation_engine.py | 250 | `0.03` | 永续增长率 | 同上 |
| valuation_engine.py | 252 | `0.30` | 安全边际 | 同上 |
| valuation_engine.py | 300 | `0.08` | 默认盈利增速 | 获取不到预测时回退 |
| judgment_tracker.py | 29-38 | 9 组权重 | 模块权重融合 | 决策核心 |
| pipeline_runner.py | 148 | `0.7 / 0.3` | 门控阈值 | 决定是否调 LLM |
| pipeline_runner.py | 300 | `0.0023` | 交易成本 | EV 核心 |
| pipeline_runner.py | 309-310 | `0.8 / 0.5` | 盈亏比乘数 | EV 核心 |
| portfolio.py | 211-238 | ±5%/10% | 配置调整步长 | 资产配置核心 |
| portfolio.py | 241 | `0.15` | 塔勒布现金底线 | 反脆弱核心 |
| stock_screen.py | 38-46 | 7 组权重 | 选股维度权重 | **P0** 与 config.py 不一致 |

### 高影响（25 处）
包括：止盈止损参数、PE/PEG/PB 评分阈值、定投倍率、蒙特卡洛纪律参数、RL 状态空间等。
（详见 subagent 审计报告，此处省略以控制篇幅）

---

## 附录 C：服务端日志错误汇总

```
[ERROR] ModuleNotFoundError: No module named 'sklearn'
[ERROR] TypeError: float() argument must be a string or a real number, not 'dict' (rl_position.py:228)
[ERROR] [TREASURY] Failed: name 'get_valuation_percentile' is not defined
[ERROR] [COMMODITY] Brent(foreign_hist) fail: Expected object or value
[ERROR] [NAV] Failed to fetch 600519/000858/300750: SyntaxError: Unexpected token '<'
[ERROR] [DATA_PROVIDER] 东方财富 FAIL: RemoteDisconnected
[WARN]  [GLOBAL_PE] 中美PE相同(75.34)，疑似数据源错误
[INFO]  [DATA_PROVIDER] 东方财富不可用，降级到新浪+雪球
[INFO]  [DATA_PROVIDER] 新浪+雪球 OK: 5324 stocks in 55.9s
[WARN]  [MONITOR] 600519/000858/300750 realtime fail (×3 each)
[WARN]  [MONITOR] 600519/000858/300750 indicators fail
```
