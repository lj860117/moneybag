# 钱袋子 V4 — 完整设计方案 + 数据源诚实审计

> 2026-04-14 03:40 | 回答用户两个核心问题

---

## 一、数据源诚实审计：V4 路线图说的 vs 实际有的

> 用户质疑："你说的国务院/发改委、行业新闻、Tushare 公告/研报、美股/港股、北向资金/融资融券、社交媒体情绪——之前功能也没有这么多，对吧？"

### 逐项核实结果

| # | V4 路线图声称的数据源 | 实际状态 | 实际代码位置 | 诚实评价 |
|---|---------------------|---------|------------|---------|
| 1 | **国务院/发改委/证监会** | ✅ **有，但间接** | `news_data.py` 的 `POLICY_KEYWORDS` 含 "国务院/发改委" 关键词过滤；`policy_data.py` 有 5 个主题政策新闻 | 不是直接抓国务院官网，是从东方财富新闻里**按关键词过滤**政策类新闻。能用，但不等于"接了国务院数据源" |
| 2 | **行业新闻(东财/新浪)** | ✅ **有** | `news_data.py` — `stock_news_em("A股"/"财经")`；`holding_intelligence.py` — 个股/行业新闻；`policy_data.py` — 5 主题新闻 | 通过 AKShare 间接调用东方财富新闻 API，**数据确实有**。每次拉 10-30 条 |
| 3 | **Tushare 公告/研报摘要** | ❌ **没有！** | `tushare_data.py` 只用了 `daily_basic`（PE/PB）和 `fina_indicator`（ROE/毛利率） | Token 已有（2000 积分），但**从未调用过公告接口和研报接口**。路线图里写"已有 Token"是真的，但"公告/研报"数据完全没接 |
| 4 | **美股/港股前夜走势** | ⚠️ **美股有，港股没有** | `global_market.py` — 美股三大指数(道琼斯/标普/纳斯达克)通过 `index_us_stock_sina` | 美股 ✅ 道琼斯+标普+纳指。**港股完全没有**——代码里搜不到任何 "港股/hk/恒生" |
| 5 | **北向资金/融资融券** | ✅ **有，而且很全** | `factor_data.py` + `alt_data.py` — 北向净流入 + 持股排行 + 融资融券余额 | 这两个是最扎实的数据源，`alt_data.py` 有 6 个维度的资金数据。**路线图没吹牛** |
| 6 | **社交媒体情绪(雪球/东财)** | ⚠️ **名不副实** | `factor_data.py` 的 `get_news_sentiment_score()` — 用 LLM 给新闻标题打分 | 叫"社交媒体情绪"但**实际不是抓雪球帖子/东财股吧讨论**。是拿东方财富的新闻标题让 DeepSeek 打分。雪球只用来查个股行情（`stock_individual_spot_xq`），没有抓社交内容 |

### 诚实总结

```
路线图说了 6 项数据源：
  ✅ 完全有的：2 项（行业新闻、北向/融资融券）
  ⚠️ 有但不完整/名不副实：3 项（国务院间接过滤、美股有港股没、情绪用新闻代替社交）
  ❌ 完全没有：1 项（Tushare 公告/研报）

所以你的直觉是对的——之前的功能确实没有路线图写的那么多。
路线图里"已有"的标注有些地方夸大了实际状态。
```

### V4 实施时需要新增的数据源

| 需新增 | 来源 | 难度 | 预估 |
|--------|------|------|------|
| Tushare 公告接口 `anns_d` | Tushare Pro（Token 已有） | 🟢 低 | 1h |
| Tushare 研报摘要 `report_rc` | Tushare Pro | 🟢 低 | 1h |
| 港股恒生指数 | AKShare `stock_hk_index_daily_sina` | 🟢 低 | 30min |
| 雪球/东财股吧热帖 | AKShare `stock_hot_follow_xq` / 自建爬虫 | 🟡 中 | 2-3h |

---

## 二、现有数据源完整清单（审计实锤）

经过对 44 个后端 .py 文件的逐一扫描，实际数据架构如下：

### 数据源全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                       数据源层（4 大类）                          │
├──────────────┬─────────────┬──────────────┬────────────────────┤
│   AKShare    │ Tushare Pro │  DeepSeek    │   企业微信 API      │
│  (25个模块)   │  (1个模块)   │  (8个模块)    │   (1个模块)         │
│  55+接口调用  │  2个接口     │  httpx调用   │   推送+回调         │
│              │             │              │                    │
│ 底层数据来自：│ 底层数据来自：│ 用于：        │ 用于：              │
│ · 东方财富   │ · 交易所原始  │ · 情绪打分   │ · 异动推送          │
│ · 新浪财经   │   数据（PE/  │ · 政策分析   │ · 日报推送          │
│ · 雪球      │   PB/财务）  │ · 全球影响   │ · 聊天机器人        │
│ · 上交所    │              │ · 选基/选股  │                    │
│ · 巨潮资讯   │              │ · 因子生成   │                    │
│ · 上期所    │              │ · 资产诊断   │                    │
│ · 中证指数   │              │ · 管家问答   │                    │
└──────────────┴─────────────┴──────────────┴────────────────────┘
```

### AKShare 接口详细清单（55+ 个）

| 分类 | 接口 | 模块文件 | 数据说明 |
|------|------|---------|---------|
| **A股行情** | `stock_zh_a_spot_em` | stock_data_provider | 全量 A 股实时行情（东方财富，常反爬） |
| | `stock_zh_a_spot` | stock_data_provider | 新浪 A 股行情（降级源） |
| | `stock_individual_spot_xq` | stock_data_provider, stock_monitor | 雪球单股详情（PE/PB/市值） |
| | `stock_zh_a_hist` | stock_monitor, ai_predictor, genetic_factor, portfolio_optimizer, llm_factor_gen, backtest_engine | A 股日 K 线历史 |
| | `stock_info_a_code_name` | stock_monitor | A 股代码名称表 |
| **宏观经济** | `macro_china_cpi` | macro_data | CPI 消费者物价指数 |
| | `macro_china_pmi` | macro_data | PMI 采购经理指数 |
| | `macro_china_money_supply` | macro_data, macro_extended | M1/M2 货币供应 |
| | `macro_china_ppi` | macro_data | PPI 工业品出厂价 |
| | `macro_china_gdp` | macro_v8 | GDP 季度数据 |
| | `macro_china_gyzjz` | macro_v8 | 工业增加值 |
| | `macro_china_consumer_goods_retail` | macro_v8 | 社会消费品零售 |
| | `macro_china_gdzctz` | macro_v8 | 固定资产投资 |
| | `macro_china_shrzgm` | macro_extended | 社会融资规模 |
| | `macro_china_lpr` | macro_extended | LPR 贷款利率 |
| | `macro_china_real_estate` | policy_data | 房地产开发投资/销售 |
| | `macro_china_new_house_price` | policy_data | 70 城新房价格指数 |
| **资金流向** | `stock_hsgt_north_net_flow_in_em` | factor_data, alt_data | 北向资金净流入 |
| | `stock_hsgt_hold_stock_em` | alt_data | 北向持股排行 |
| | `stock_margin_sse` | factor_data, alt_data | 上交所融资融券余额 |
| | `stock_individual_fund_flow_rank` | factor_data | 个股资金流排行 |
| | `stock_individual_fund_flow` | holding_intelligence | 个股主力资金流 |
| | `stock_sector_fund_flow_rank` | alt_data | 行业板块资金流 |
| **另类数据** | `stock_lhb_detail_em` | alt_data, macro_v8 | 龙虎榜明细 |
| | `stock_dzjy_mrtj` | alt_data | 大宗交易 |
| | `stock_inner_trade_xq` | alt_data | 股东内部交易（雪球） |
| | `stock_hold_management_detail_cninfo` | macro_v8 | 管理层增减持（巨潮） |
| | `stock_restricted_release_summary_em` | market_factors | 限售股解禁日程 |
| **新闻资讯** | `stock_news_em` | news_data, holding_intelligence, policy_data | 东方财富分类新闻 |
| | `futures_news_shmet` | news_data | 上海金属网黄金新闻 |
| **全球市场** | `index_us_stock_sina` | global_market | 美股三大指数（新浪） |
| | `fx_spot_quote` | global_market | 外汇即时汇率 |
| | `macro_bank_usa_interest_rate` | global_market | 美联储利率历史 |
| | `stock_market_pe_lg` | global_market | 中美 PE 估值对比 |
| **基金相关** | `fund_open_fund_info_em` | market_data, fund_monitor, backtest_engine | 基金净值走势 |
| | `fund_value_estimation_em` | fund_monitor | 全市场基金估值 |
| | `fund_name_em` | fund_monitor | 基金名称列表 |
| | `fund_open_fund_rank_em` | fund_rank | 全量基金排行 |
| | `fund_portfolio_hold_em` | factor_data | 基金持仓明细 |
| | `fund_etf_fund_daily_em` | market_factors | ETF 资金流 |
| **估值指标** | `stock_index_pe_lg` | factor_data, market_data | 沪深 300 PE |
| | `stock_zh_index_value_csindex` | market_data | 中证指数估值 |
| | `bond_zh_us_rate` | factor_data | 中美国债收益率 |
| | `rate_interbank` | factor_data | SHIBOR 拆借利率 |
| **大宗商品** | `futures_main_sina("AU0")` | market_factors | 黄金期货（上期所） |
| | `futures_main_sina("CU0")` | market_factors | 铜期货（上期所） |
| **技术指标** | `stock_zh_index_daily` | technical, market_data, backtest | 指数日 K 线 |
| | `stock_market_activity_legu` | macro_extended | 涨跌家数/赚钱效应 |
| | `stock_individual_info_em` | holding_intelligence | 个股基本信息 |

---

## 三、V4 完整计划步骤（6 周 × 详细子任务）

### 总览

```
Week 1        Week 2        Week 3        Week 4        Week 5        Week 6
  │              │              │              │              │              │
  ▼              ▼              ▼              ▼              ▼              ▼
信号侦察兵    判断追踪器    持仓体检师    管家路由层    双模式UI     打磨+复盘
(~8h)         (~5h)         (~7h)         (~7h)         (~6h)        (~5h)
```

**总工时：~38h | 纯 AI 编码实际约 15-20h（因为很多模块可复用）**

---

### Phase 1（Week 1）：🔭 信号侦察兵 — 最大价值点

> 核心交付：每天早盘前企微收到 TOP 3 投资信号，带逻辑链

| 步骤 | 任务 | 具体做什么 | 依赖 | 预估 |
|------|------|----------|------|------|
| **1.1** | 数据源补齐 | ① 新增 Tushare `anns_d` 公告接口 ② 新增 Tushare `report_rc` 研报接口 ③ 新增港股恒生指数 `stock_hk_index_daily_sina` | Tushare Token（✅已有） | 2h |
| **1.2** | `signal_scout.py` 核心 Pipeline | 新建文件 ~400 行。实现 `collect()→extract()→match()→rank()→deliver()` 五步流程。`collect()` 并发拉取 6 大信息源（复用 policy_data + news_data + alt_data + global_market + 新增 Tushare 公告/研报） | 1.1 完成 | 2h |
| **1.3** | 信号提取 Prompt 设计 | 新建 `prompts/signal_extract.md`。核心问法："以下信息中，哪些会在未来 1-5 天实质影响 A 股某个板块/个股？" 输出结构化 JSON（signal/direction/magnitude/sectors/codes/logic_chain/confidence） | 无 | 1h |
| **1.4** | 持仓匹配引擎 | `match()` 实现：① 直接命中（信号 codes ∈ 持仓） ② 行业命中（信号 sectors ∩ 持仓行业） ③ 生成 `relevance_to_you` 个性化关联说明 | 1.2 完成 | 30min |
| **1.5** | `signal_scout_cron.py` | 新建 cron 脚本。三次调度：08:30 开盘前 / 12:30 午盘 / 15:30 收盘。遍历所有用户，按 userId 分别匹配持仓 | 1.2 完成 | 30min |
| **1.6** | 企微推送适配 | 信号格式 → 纯文本（企微不支持 Markdown）。TOP 3 信号 + 逻辑链 + 置信度 | 1.5 完成 | 30min |
| **1.7** | 前端「📡 今日信号」 | 首页新增信号板块：红/黄/绿三级信号卡片，点击展开逻辑链和关联持仓 | 1.2 完成 | 1h |
| **1.8** | 验证 & 部署 | 语法检查 → 腾讯云部署 → crontab 配置 → 实测一次信号扫描 | 全部完成 | 30min |

**Phase 1 小计：~8h**

---

### Phase 2（Week 2）：📊 AI 判断追踪器

> 核心交付：自动追踪每次 AI 判断对不对，周报推企微

| 步骤 | 任务 | 具体做什么 | 预估 |
|------|------|----------|------|
| **2.1** | `judgment_tracker.py` 核心 | 新建 ~300 行。`record_judgment()` 记录每次判断（JSON 存储）、`verify_pending()` 每日对比实际走势、`get_scorecard()` 生成成绩单 | 2h |
| **2.2** | 埋点：signal_scout | 信号侦察兵每次 `deliver()` 后自动调 `record_judgment()`，记录方向+置信度+目标代码+验证日期 | 30min |
| **2.3** | 埋点：agent_engine | Agent 分析引擎每次输出后自动记录判断 | 30min |
| **2.4** | 验证 cron | `judgment_verify_cron.py` — 每日 16:30 检查所有到期判断，拉实际价格对比 | 30min |
| **2.5** | 元学习 `learn_from_history()` | 分析哪类判断最准/最差 → 调整 signal_scout 的 confidence 权重 | 30min |
| **2.6** | 前端「🎯 AI 成绩单」 | 首页卡片：近 30 天准确率 + 趋势箭头 + 最准/最差领域。点击展开全部历史 | 1h |
| **2.7** | 企微周报 | 每周五自动推送成绩单（准确率+最佳/最差+洞察） | 30min |

**Phase 2 小计：~5.5h**

---

### Phase 3（Week 3）：🏥 持仓体检师

> 核心交付：持仓压力测试 + 健康度评分 + 调仓建议

| 步骤 | 任务 | 具体做什么 | 预估 |
|------|------|----------|------|
| **3.1** | `portfolio_doctor.py` 压力测试 | 新建 ~350 行。5 大内置情景（美联储加息/中美恶化/地产暴雷/疫情反复/系统性暴跌）。每个情景定义各行业 beta 冲击 → 计算组合预估损失 | 2h |
| **3.2** | 自定义情景接口 | 用户输入"如果 XX 发生"→ DeepSeek 拆解为行业 beta → 代入压力测试 | 1h |
| **3.3** | 集中度深度检查 | 增强现有纪律检查：+ 风格集中度（全成长/全价值）+ 市值集中度 + 持仓相关性（>0.8=假分散） | 1h |
| **3.4** | AI 综合诊断 | `prompts/portfolio_diagnosis.md` — 不是点评每只股，而是："基于持仓结构+市场环境，组合有什么健康问题？" 输出🟢🟡🔴💊四级结构 | 1h |
| **3.5** | 前端「🏥 持仓体检」 | 压力测试情景选择 → 结果图表（预估损失柱形图）+ 集中度雷达图 + AI 诊断处方 | 1.5h |
| **3.6** | 企微推送 | 每日收盘后自动体检，有🔴问题才推送（避免打扰） | 30min |

**Phase 3 小计：~7h**

---

### Phase 4（Week 4）：🐚 管家路由层

> 核心交付：问"茅台能买吗"不再随便说，而是调 5 个模块综合回答

| 步骤 | 任务 | 具体做什么 | 预估 |
|------|------|----------|------|
| **4.1** | `steward.py` 管家壳 | 新建 ~250 行。四大方法：`morning_briefing()`（早间简报）/ `closing_review()`（收盘复盘）/ `weekly_report()`（周报）/ `ask()`（问答） | 2h |
| **4.2** | 早间简报整合 | 并行调用：signal_scout TOP3 + portfolio_doctor 过夜风险 + global_market 美股走势 + macro_data 经济日历 → 压缩到 200 字 | 1h |
| **4.3** | "茅台能买吗"多模块联动 | `ask()` 内部：① stock_screen 看排名 ② ai_predictor 看预测 ③ signal_scout 看相关信号 ④ portfolio_doctor 看对持仓影响 ⑤ agent_engine 综合 → 输出完整报告 | 2h |
| **4.4** | 改造 chat.py → 走管家路由 | 现有聊天接口改为先经 steward 判断意图 → 路由到对应模块 → 汇总回答 | 1h |
| **4.5** | API 整合 | `/api/steward/briefing` / `/api/steward/review` / `/api/steward/ask` | 30min |
| **4.6** | 企微联动 | 企微发"简报"→ morning_briefing / 发"复盘"→ closing_review / 发其他 → steward.ask() | 30min |

**Phase 4 小计：~7h**

---

### Phase 5（Week 5）：🎨 双模式 UI

> 核心交付：老婆打开就是简洁管家界面，你切专业模式看全部工具

| 步骤 | 任务 | 具体做什么 | 预估 |
|------|------|----------|------|
| **5.1** | 小白模式首页 | 极简布局：总资产 + 今日盈亏 + TOP3 信号 + 持仓健康度 + AI 准确率 + "问问管家"输入框 | 3h |
| **5.2** | 模式切换 | `localStorage` 记住偏好。底部一个按钮切换。切到专业模式 = 现有所有 tab | 30min |
| **5.3** | 术语翻译层 | 专业术语 → 大白话（"北向资金净流入 50 亿" → "外国投资者今天买了 50 亿 A 股"） | 1h |
| **5.4** | 小白模式问答 | 问答框走 steward.ask()，结果用大白话呈现，隐藏技术细节 | 1h |
| **5.5** | 移动端适配 | 小白模式重点优化手机端（大字体、大按钮、减少滚动） | 30min |

**Phase 5 小计：~6h**

---

### Phase 6（Week 6）：🔧 打磨 + 复盘 + 逆向思维

| 步骤 | 任务 | 具体做什么 | 预估 |
|------|------|----------|------|
| **6.1** | Prompt 调优 | 基于 2 周的信号数据，分析哪些提取准/哪些离谱 → 调整 signal_extract.md | 2h |
| **6.2** | 权重自适应 | judgment_tracker 的元学习结果 → 自动调整各模块在 steward 中的权重 | 1h |
| **6.3** | 逆向思维原型 | 实验性功能：当 70%+ 模块看多时，主动找反面证据（"大家都看好的时候最危险"） | 1.5h |
| **6.4** | 端到端测试 | 完整走一遍：信号→判断→体检→管家→企微→验证闭环 | 30min |

**Phase 6 小计：~5h**

---

## 四、关键模块联动图

```
                    ┌──────────────┐
                    │  用户发问题    │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  steward.py   │ ← 管家路由层
                    │  意图识别      │
                    └──┬───┬───┬───┘
                       │   │   │
          ┌────────────┘   │   └────────────┐
          │                │                │
    ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
    │signal_scout│   │portfolio_ │   │ agent_    │
    │信号侦察兵  │   │doctor     │   │ engine    │
    │           │   │持仓体检师  │   │10Skill辩论│
    └─────┬─────┘   └─────┬─────┘   └─────┬─────┘
          │               │               │
          │         ┌─────▼─────┐         │
          │         │monte_carlo│         │
          │         │蒙特卡洛   │         │
          │         └───────────┘         │
          │                               │
    ┌─────▼───────────────────────────────▼─────┐
    │           judgment_tracker.py              │
    │           AI 记分员 — 记录每次判断            │
    │           N 天后验证 → 成绩单 → 权重调整      │
    └───────────────────┬───────────────────────┘
                        │
                 ┌──────▼──────┐
                 │ 企微推送     │
                 │ + 前端展示   │
                 └─────────────┘
```

---

## 五、改完后的核心差异

| 能力 | 东方财富 | 同花顺 | **钱袋子 V4** |
|------|---------|-------|:---:|
| 因子排名 | ✅ 更强 | ✅ 更强 | ⚠️ 够用 |
| 200条信息→3条信号 | ❌ 没有 | ❌ 没有 | **✅ 独家** |
| 信号→关联你的持仓 | ❌ | ❌ | **✅ 独家** |
| 持仓压力测试 | ❌ | ⚠️ 基础 | **✅ 深度** |
| AI 判断追踪复盘 | ❌ | ❌ | **✅ 独家** |
| 管家式综合问答 | ❌ | ❌ | **✅ 独家** |
| 企微即时推送 | ❌ | ❌ | **✅ 独家** |
| 小白模式 | ❌ 全是专业界面 | ❌ | **✅ 独家** |

**一句话：V4 之后，钱袋子做的事情是东方财富/同花顺做不到的——不是"同一个维度比不过"，而是"做的事情不一样"。**

---

## 六、DeepSeek 成本预估（V4 新增）

| 模块 | 调用频率 | Token/天 | 月成本 |
|------|---------|---------|--------|
| 信号提取 | 3 次/天 | ~6000 | ¥3-5 |
| 持仓诊断 | 1 次/天 | ~3000 | ¥1-2 |
| 判断验证 | 1 次/天 | ~1000 | ¥0.5 |
| 管家问答 | ~5 次/天 | ~5000 | ¥2-3 |
| **V4 新增合计** | | | **+¥6-10/月** |
| 现有（辩论等） | 不变 | 不变 | ~¥5/月 |
| **总计** | | | **~¥15/月** |

---

## 七、今晚就能开始

如果现在动手，推荐从 **Phase 1.1（数据源补齐）** 开始：
1. Tushare 公告接口接入（Token 已有，加 2 个函数）
2. 港股恒生指数接入（AKShare 一行代码）
3. 然后写 signal_scout.py 核心

**先让 AI 当上侦察兵——有了侦察情报，体检/复盘/管家都是水到渠成。**
