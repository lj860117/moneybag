# MoneyBag 重构进度追踪

> 由 Claude Code 每次会话结束时更新。
> 格式：日期 + 完成的任务 + 当前阶段 + 阻塞项

---

## 当前阶段
M1 W3 — 统一缓存层（✅ 全部完成，47 处散装缓存 → infra/cache.MemoryCache）

## 已完成
- [x] 2026-04-25: 四层目录树（api/ use_cases/ domain/ infra/）
- [x] 2026-04-25: 4 个核心 Protocol（Cache/Store/LLM/DataSource）
- [x] 2026-04-25: 3 个 infra 最小实现（cache/store/llm.gateway）
- [x] 2026-04-25: 28 个 smoke test 全绿
- [x] 2026-04-25: Git commit + tag m1-w1-skeleton
- [x] 2026-04-25: REFACTOR_STATUS.md 创建
- [x] 2026-04-26: 分析路由依赖（199 路由，按 35 组前缀分类，P1/P2/P3 三级）
- [x] 2026-04-26: **第一批拆分完成：64 个路由 → 10 个 Router 文件**
  - api/factors.py（8 路由）— 北向资金/融资融券/国债/SHIBOR/股息/情绪/主力流
  - api/macro.py（9 路由）— M1/社融/LPR/涨跌/美林时钟/V8/GDP/龙虎榜
  - api/global_market.py（7 路由）— 美股指数/外汇/美联储/全球PE/快照/影响/决策包
  - api/policy.py（5 路由）— 房地产/房价/政策新闻/全主题/政策影响
  - api/market_factors.py（4 路由）— 大宗商品/解禁/ETF流/综合
  - api/alt_data.py（6 路由）— 北向详情/融资详情/龙虎榜/大宗交易/行业流
  - api/quant.py（14 路由）— ML选股/因子IC/蒙特卡洛/AI预测/遗传因子/组合优化/RL仓位/LLM因子
  - api/broker.py（3 路由）— 券商共识/最新研报/个股研报
  - api/analysis.py（5 路由）— 分析历史/最新/详情/对比/外部接收
  - api/scenario.py（3 路由）— 情景列表/情景分析/自定义情景
  - main.py: 4044 → 3598 行（减少 446 行）
  - 路由总数保持 199 不变（main.py 135 + routers 64）
  - 28 个 M1 骨架测试全绿 ✅
- [x] 2026-04-26: **第二批拆分完成：78 个路由 → 5 个 Router 文件 + 1 个共享模块**
  - api/shared_helpers.py（673 行）— _build_market_context / _build_portfolio_context / _rule_based_reply / classify_chat_intent / _do_ocr / USER_DEFAULTS / AVAILABLE_MODELS 等公共辅助
  - api/holdings.py（19 路由，426 行）— 股票持仓CRUD/基金持仓CRUD/盯盘预警/深度分析/持仓智能/财务数据
  - api/portfolio.py（21 路由，568 行）— 交易流水CRUD/资产管理/净资产/盈亏/加仓/迁移/体检/风控/配置建议
  - api/signals.py（11 路由，338 行）— 买卖信号/入场时机/智能定投/止盈止损/每日信号/回测/筛选
  - api/news.py（12 路由，185 行）— 新闻/基金信息/政策影响/技术指标/宏观/基金搜索
  - api/user.py（15 路由，493 行）— 用户数据/偏好/家庭汇总/OCR记账/记账CRUD/收入源/数据审计
  - main.py: 3598 → 1071 行（减少 2527 行，累计从 4044 减少 2973 行，降幅 73%）
  - 路由总数保持 199 不变（main.py 57 + P1 routers 64 + P2 routers 78）
  - 28 个 M1 骨架测试全绿 ✅
  - 零路由重复，零路由遗漏
- [x] 2026-04-26: **第三批拆分完成（最终批）：55 个路由 → 6 个 Router 文件**
  - api/chat.py（3 路由，277 行）— /chat（非流式）、/chat/stream（SSE 流式）、/models
  - api/dashboard.py（6 路由，215 行）— /dashboard（三级降级）、/nav/all、/nav/{code}、/market-status、/glossary、/health
  - api/agent.py（18 路由，271 行）— Agent 记忆/画像/铁律/情绪/生活事件/待审/分析/信号
  - api/steward.py（7 路由，79 行）— 管家 ask/briefing/review、regime、llm-usage、weekly-report
  - api/enhance.py（5 路由，72 行）— AI 点评（股票/基金）、存款建议、资产诊断、今日关注
  - api/misc.py（16 路由，184 行）— 决策日志、备份、信号侦察兵、判断追踪、盈利预测、估值、推荐、敞口
  - main.py: 1071 → 112 行（减少 959 行，**累计从 4044 减少 3932 行，降幅 97%**）
  - 路由总数保持 199 不变（main.py 2 + P1 64 + P2 78 + P3 55 = 199）
  - 28 个 M1 骨架测试全绿 ✅
  - 零路由重复，零路由遗漏
- [x] 2026-04-26: **M1 W3 统一缓存层完成：47 处散装缓存 → infra/cache.MemoryCache**
  - MemoryCache 增加 put()（set 别名）、expire()、keys() 方法
  - CacheProtocol 同步增加 put() 和 expire() 接口
  - 迁移范围：38 个 services/*.py + 3 个 api/*.py（shared_helpers/signals/news）+ 1 个 services/llm_gateway.py（实例属性）
  - 散装 `_cache = {}` 定义：47 → 0（含追加发现的 stock_price_provider / technical / fund_screen）
  - dict 式读写模式（`cache[key] = {"data": ..., "ts": ...}`）：~200+ 处 → 0
  - 修复 stock_screen.py 4 处 TTL 常量前向引用 bug
  - 修复 stock_screen.py MemoryCache.items() 迭代→改为 .keys() + .get()
  - 修复 technical.py 跨模块共享 _nav_cache→改为独立 _tech_cache
  - 修复 fund_screen.py 跨模块共享 _fund_rank_cache→改为独立 _fund_screen_cache
  - 28 个 M1 骨架测试全绿 ✅
  - `grep -rn 'cache\s*=\s*{}' backend/services/` 输出为空 ✅（不变式 #4 达成）

## 进行中
- 无

## 阻塞项
- 无

## 下次会话计划
- M1 W3: infra/data_source 五分法 — 数据源按 market/fundamental/macro/alt 分桶
- M1 W4: 配 import-linter + mypy CI（方案 C 落地）
- M1 W4: main.py 行数上限 linter（CI 红线 200 行）

---

## 历史记录

### 2026-04-25
**会话 1**（M1 W1）
- 任务：搭四层骨架 + 4 Protocol + 3 infra
- 产出：23 文件，1049 行新增
- 状态：✅ 完成
- 影响面：零改动现有文件

### 2026-04-26
**会话 2**（M1 W2 准备）
- 任务：分析 main.py 199 个路由依赖关系
- 产出：完整路由依赖分析报告
  - 199 路由按 35 组前缀分类
  - 每路由标注依赖模块、全局变量、跨路由调用
  - P1 独立路由 120+、P2 中等耦合 50+、P3 高耦合 7 个
  - 第一批拆分目标：10 个 Router 文件，61 路由，main.py 立减 ~500 行
  - 发现 4 类不变式违反（直调 httpx ×3、散装缓存 ×3、akshare 越界 ×2、内嵌逻辑 ×5+）
- 状态：✅ 完成（纯分析，零改动代码）
- 影响面：无（仅更新 PROGRESS.md）

**会话 3**（M1 W2 第一批拆分）
- 任务：创建 10 个 api/*.py Router 文件，提取 64 个 P1 独立路由
- 产出：
  - 10 个新文件（api/factors.py, macro.py, global_market.py, policy.py, market_factors.py, alt_data.py, quant.py, broker.py, analysis.py, scenario.py）
  - main.py 注册 10 个 include_router()
  - main.py 删除 64 个路由 + 6 个 inline import 块
  - 修复 1 个断引用（_build_market_context 中的 get_commodity_prices/get_etf_fund_flow 改为 lazy import）
  - main.py: 4044 → 3598 行（-446 行）
  - 路由总数 199 保持不变，零重复
  - 28 个 M1 骨架测试全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：所有 URL 路径不变，前端零改动
  - 🟢 确认无影响：tests/test_skeleton_m1.py 28/28 通过
  - 🟡 建议评估：services/ 中的 import 链（本次仅移动路由层，不动 service 层）

**会话 4**（M1 W2 第二批拆分）
- 任务：提取公共辅助模块 + 创建 5 个 api/*.py Router 文件，提取 78 个 P2 中等耦合路由
- 产出：
  - api/shared_helpers.py（673 行）— 从 main.py 提取 _build_market_context（164行）、_build_portfolio_context（78行）、_rule_based_reply（107行）、classify_chat_intent、_do_ocr（119行）、USER_DEFAULTS/OVERRIDES、AVAILABLE_MODELS、_cached_file_response 等公共函数和常量
  - api/holdings.py（19 路由，426 行）— 股票/基金持仓 CRUD + 盯盘预警 + 深度分析 + 持仓智能
  - api/portfolio.py（21 路由，568 行）— 交易流水 + 资产管理 + 净资产 + 盈亏 + 体检 + 风控 + 配置
  - api/signals.py（11 路由，338 行）— 买卖信号 + 时机 + 定投 + 止盈 + 每日信号 + 回测 + 筛选
  - api/news.py（12 路由，185 行）— 新闻 + 基金信息 + 政策 + 技术指标 + 宏观 + 搜索
  - api/user.py（15 路由，493 行）— 用户数据 + 偏好 + 家庭 + OCR + 记账 + 收入源 + 审计
  - main.py: 3598 → 1071 行（-2527 行，累计 -2973 行，降幅 73%）
  - 路由总数 199 保持不变（main.py 57 + P1 64 + P2 78），零重复零遗漏
  - 28 个 M1 骨架测试全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：所有 URL 路径不变，前端零改动
  - 🟢 确认无影响：tests/test_skeleton_m1.py 28/28 通过
  - 🟡 建议评估：services/ 中的 import 链（本次仅移动路由层，不动 service 层）
  - 🟡 建议评估：api/portfolio.py 568 行略超 400 行限制（21 个路由均属同一业务域，可接受）

**会话 5**（M1 W2 第三批拆分 — 最终批）
- 任务：提取 P3 高耦合路由 → 6 个 api/*.py Router 文件
- 产出：
  - api/chat.py（3 路由，277 行）— /chat + /chat/stream（SSE 流式）+ /models，最高耦合度：httpx 直调 + agent_memory + steward + shared_helpers
  - api/dashboard.py（6 路由，215 行）— /dashboard（三级降级异步）+ /nav + /market-status + /glossary + /health
  - api/agent.py（18 路由，271 行）— Agent 完整 CRUD：记忆/画像/铁律/情绪/生活事件/待审/分析/信号
  - api/steward.py（7 路由，79 行）— 管家 Pipeline + regime + llm-usage + weekly-report
  - api/enhance.py（5 路由，72 行）— DeepSeek 智能增强：AI 点评/存款建议/资产诊断/今日关注
  - api/misc.py（16 路由，184 行）— 决策日志/备份/信号侦察兵/判断追踪/盈利预测/估值/推荐/敞口/基金份额
  - main.py: 1071 → 112 行（-959 行，**累计 4044 → 112 行，降幅 97%**）
  - 路由总数 199 保持不变（main.py 2 + P1 64 + P2 78 + P3 55），零重复零遗漏
  - 28 个 M1 骨架测试全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：所有 URL 路径不变，前端零改动
  - 🟢 确认无影响：tests/test_skeleton_m1.py 28/28 通过
  - 🟡 建议评估：services/ 中的 import 链（本次仅移动路由层，不动 service 层）
  - 🟡 建议评估：api/chat.py 277 行含 httpx 直调（不变式 3 违反，M1 W3 迁到 infra/llm/gateway 时修复）

### 2026-04-26
**会话 6**（M1 W3 统一缓存层）
- 任务：把代码中所有 `_cache = {}` 迁移到 infra/cache.MemoryCache
- 产出：
  - MemoryCache 增加 put()、expire()、keys() 方法；CacheProtocol 同步更新
  - 迁移 47 处散装缓存定义 → 0 处（覆盖 38 个 services/*.py + 3 个 api/*.py + 1 个 llm_gateway 实例属性）
  - 重写 ~200+ 处 dict 式读写模式为 .get()/.set() API
  - 修复 stock_screen.py 4 处 TTL 常量前向引用 bug
  - 修复 3 处跨模块缓存共享问题（technical.py / fund_screen.py / stock_screen.py 迭代）
  - 修复 api 层（signals.py / news.py / shared_helpers.py）残留 dict 式访问
  - 28/28 骨架测试全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 28/28 通过
  - 🟢 确认无影响：所有缓存 key 和 TTL 保持不变
  - 🟢 确认无影响：API URL 路径不变，前端零改动
  - 🟡 建议评估：services/llm_gateway.py 内部磁盘缓存持久化逻辑（用了 MemoryCache._data 内部结构）
  - 🟡 建议评估：api/chat.py httpx 直调（不变式 3 违反，待 W4 修复）
