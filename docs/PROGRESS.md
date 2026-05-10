# MoneyBag 重构进度追踪

> 由 Claude Code 每次会话结束时更新。
> 格式：日期 + 完成的任务 + 当前阶段 + 阻塞项

---

## 当前阶段
M3 W2 — 模式 A 事前提示 + 7 点清单完整计算（✅ 完成）

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
- [x] 2026-04-26: **M1 W3 infra/data_source 五分法完成：消除不变式 #6 违反（数据源越界）**
  - 创建 6 个五分法 bucket 包：market/ fundamental/ macro/ alt/ synthetic/ providers/
  - 创建 fallback.py 占位（M1 W4 实现三级降级链）
  - market/ 实现 search_funds()：基金搜索，委托 fund_monitor._load_fund_names() + 降级 fund_rank
  - alt/ 实现 get_stock_news()：个股新闻，委托 news_data.get_stock_news_by_code()
  - infra/data_source/__init__.py 门面导出 get_stock_news + search_funds
  - 修复 api/chat.py：akshare.stock_news_em 直调 → infra.data_source.get_stock_news
  - 修复 api/news.py：akshare.fund_name_em 直调 → infra.data_source.search_funds（含降级逻辑迁移）
  - 新增 9 个测试（6 bucket importable + facade exports + fallback module + AST 不变式 #6 检查）
  - `grep -rn 'import akshare' backend/api/` 输出为空 ✅（不变式 #6 api 层达成）
  - 37 个 M1 骨架测试全绿 ✅
- [x] 2026-04-26: **M1 W4 mypy strict 配置 + CI 集成（方案 C 落地）**
  - 创建 pyproject.toml：mypy strict 配置，覆盖 domain/ infra/ use_cases/
  - domain/ infra/ use_cases/ 启用 disallow_untyped_defs + disallow_any_generics + strict_equality 等
  - 老代码（services/ api/）ignore_errors = true（绞杀者过渡）
  - 第三方库（akshare/tushare/baostock/fastapi 等）ignore_missing_imports
  - 修复 19 处类型注解问题（Dict → Dict[str, object]、dict → dict[str, _Entry] 等）
  - mypy 通过 17 个源文件，0 错误 ✅
  - requirements-dev.txt 增加 mypy>=1.8.0
  - CI 新增 mypy-strict job（方案 C 硬约束）
- [x] 2026-04-26: **M1 W4 main.py 行数上限 linter（不变式 #8）**
  - 创建 scripts/lint_main_py.py：红线 200 行，超过 = exit 1
  - CI 新增 lint-main-py job
  - 当前 main.py 112 行，通过 ✅
- [x] 2026-04-26: **M1 W4 infra/data_source/providers/ 三级适配器 stub**
  - TushareProvider：market + fundamental（需 TUSHARE_TOKEN）
  - AkshareProvider：macro + alt（爬虫式，可能断裂）
  - BaostockProvider：market 末级降级（免费，最稳定）
  - 三个 stub 均实现 DataSourceProtocol 接口（fetch/is_available/provider_name）
  - providers/__init__.py 重导出三个 Provider
  - 新增 11 个测试（48 总数），48/48 全绿 ✅
- [x] 2026-04-26: **M2 W1 Agent Memory 拆三处完成：agent_memory.py 1277 行 → 3 个目标模块**
  - domain/services/user_preference_service.py（~340 行）— 用户偏好/画像/铁律/情绪/生活事件/待审洞察/审批工作流
  - domain/rule_engine/decision_archive.py（~380 行）— 决策档案(热/冷分层)/自定义规则/规则检查/上下文接力/自动提取队列/批量提取
  - services/agent_memory.py 改为薄 re-export shim（~95 行）— 保持 9+ 处调用方向后兼容
  - build_memory_summary() 降级为 stub（返回空串）— LLM 记忆注入删除（过拟合温床，设计决策 02-code-audit §4.2）
  - 9+ 处 import 站点零改动（shim 重导出保证向后兼容）
  - 新增 7 个测试（48 → 55），55/55 全绿 ✅
- [x] 2026-04-26: **M2 W2 家庭画像问卷完成：FamilyProfile domain model + 问卷页 API + 首个 use_case**
  - domain/models/family.py（~250 行）— FamilyProfile / Member / SubAccount frozen dataclass，含 to_dict/from_dict 往返、computed properties（total_debt / insurance_count / primary_member）
  - domain/protocols/family_profile.py — FamilyProfileProtocol（load/save/exists/list_families），不变式 #11 达成
  - infra/store/family_profile_store.py — 委托 FileStore（collection="profiles"），不变式 #5 达成
  - domain/services/family_profile_service.py（~200 行）— build_profile_from_questionnaire / validate_profile / derive_family_stage / compute_primary_member
  - use_cases/submit_family_questionnaire.py（~120 行）— **首个 use_case 文件**，编排 domain service + infra store
  - api/family_profile.py（~180 行，6 路由）— POST questionnaire / GET profile / GET+POST members / GET+POST sub-accounts
  - main.py 注册 family_profile_router（112 → 116 行，仍远低于 200 行上限）
  - 新增 15 个测试（55 → 70），70/70 全绿 ✅
  - 新文件 mypy strict 0 错误 ✅
  - domain 层零 infra import ✅（不变式 #9 + #10 达成）
- [x] 2026-04-26: **M2 W3 家庭资产负债表 MVP 完成：BalanceSheet domain model + 过期检测 + /api/family/balance-sheet 路由**
  - domain/models/balance_sheet.py（~230 行）— BalanceSheet / BalanceSheetItem frozen dataclass，含 Tier 1 四类（cash_deposits / investments / real_estate / liabilities），每项 value/currency/last_updated/data_source，过期检测 is_stale()，staleness_report()，computed properties（total_assets / total_liabilities / net_worth）
  - domain/protocols/balance_sheet.py — BalanceSheetProtocol（load/save/exists/list_families），不变式 #11 达成
  - infra/store/balance_sheet_store.py — 委托 FileStore（collection="balance_sheets"），不变式 #5 达成
  - domain/services/balance_sheet_service.py（~180 行）— build_balance_sheet / validate_balance_sheet / detect_stale_items / compute_summary，过期阈值 30 天（STALE_THRESHOLD_DAYS）
  - use_cases/manage_balance_sheet.py（~120 行）— submit_balance_sheet / get_balance_sheet / get_balance_sheet_summary / update_category_items
  - api/balance_sheet.py（~170 行，4 路由）— POST balance-sheet / GET balance-sheet/{id} / GET balance-sheet/{id}/summary / PUT balance-sheet/{id}/{category}
  - main.py 注册 balance_sheet_router（116 → 120 行，仍远低于 200 行上限）
  - 更新 3 个 __init__.py 导出（domain/models + domain/protocols + infra/store）
  - 新增 24 个测试（70 → 94），94/94 全绿 ✅
  - 新文件 domain 层零 infra import ✅（不变式 #9 + #10 达成）
- [x] 2026-04-26: **M2 W4 资产配置框架 + 再平衡提醒完成：配比矩阵 + 偏离度规则 + 再平衡触发 + /api/family/allocation 路由**
  - domain/rule_engine/defaults.py（~117 行）— AllocationDefaults 12 格配比矩阵（保守/平衡/进取 × 4 家庭阶段）、年龄微调参数、偏离度三档阈值（3/7/15%）及 hard limits、RiskDefaults（集中度/止损阈值）、ScoringDefaults（5 维评分）、RebalanceDefaults（季度检查/半年执行/紧急偏离）、StaleDataDefaults（过期天数）
  - domain/models/allocation.py（~175 行）— AllocationTarget / AllocationState / DeviationAnalysis frozen dataclass，含 to_dict/from_dict 往返、total_pct 计算属性、severity 四档分类（normal/mild/moderate/high）
  - domain/services/allocation_service.py（~220 行）— compute_target_allocation()（矩阵查找+年龄微调+保守档下限）、analyze_deviation()（四档偏离度分类+中文建议文案）、detect_rebalance_trigger()（紧急>15%/时间型≥7%+180天）、validate_allocation()（百分比范围+总和校验）
  - use_cases/manage_allocation.py（~150 行）— compute_allocation_target / analyze_allocation_deviation / check_rebalance_need / save_allocation_override
  - api/allocation.py（~216 行，4 路由）— POST /api/family/{id}/allocation/target + POST /analyze + GET /rebalance-check + PUT /target-override，含 Pydantic schema 校验
  - main.py 注册 allocation_router（120 → 124 行，仍远低于 200 行上限）
  - 更新 2 个 __init__.py 导出（domain/models + domain/rule_engine）
  - 修复偏离度文案格式 bug（%.1% → %.1f%，百分比点位正确显示）
  - 新增 23 个测试（94 → 117），117/117 全绿 ✅
  - 新文件 domain 层零 infra import ✅（不变式 #9 + #10 达成）
  - allocation_service.py 全部纯函数，无 I/O 无副作用
- [x] 2026-05-10: **M3 W1 决策复盘（模式 B 事后复盘先上）完成：买入理由多选 + 决策质量分 + /api/decisions/review 路由**
  - domain/models/decision.py（~240 行）— BuyReasonCategory（5 分类：基本面/技术面/情绪面/跟风/其他）、SignalLevel（green/yellow/red）、PredefinedReason（8 条预设：4 green + 1 yellow + 3 red）、BuyReason（from_predefined / from_custom）、DecisionQualityScore（四维 0-25 × 4 = 0-100）、DecisionReview（完整复盘记录）
  - domain/protocols/decision_guard.py — DecisionGuardProtocol（save_review / get_reviews / get_review_by_id / get_review_stats），不变式 #11 达成
  - domain/services/decision_guard_service.py（~210 行）— compute_quality_score()（四维纯函数：理由清晰度 / 信息来源 / 风险意识 / 时间跨度 + 红黄灯惩罚）、evaluate_reasons()（信号分类+消息生成）、get_predefined_reasons()（UI 渲染数据）
  - use_cases/review_decision.py（~175 行）— submit_decision_review（解析理由+计算分数+构建记录）/ save_review_to_archive（接入 decision_archive）/ get_user_reviews / get_review_statistics
  - api/decisions.py（~160 行，4 路由）— POST /api/decisions/review + GET /review/{user_id} + GET /review/{user_id}/stats + GET /reasons
  - main.py 注册 decisions_router（124 → 128 行，仍远低于 200 行上限）
  - 更新 2 个 __init__.py 导出（domain/models + domain/protocols）
  - 新增 24 个测试（117 → 141），141/141 全绿 ✅
  - 新文件 domain 层零 infra import ✅（不变式 #9 + #10 达成）
  - decision_guard_service.py 全部纯函数，无 I/O 无副作用
  - 决策质量分公式：score = (clarity + source + risk + horizon) - red*10 - yellow*5, floor 10
- [x] 2026-05-10: **M3 W2 模式 A 事前提示 + 7 点清单完整计算完成：checklist engine + /api/decisions/checklist 路由**
  - domain/models/checklist.py（~120 行）— ChecklistItem（item_id / label_zh / score 0-10 / passed / is_red_light / detail）+ ChecklistResult（7 items / total_score / max_score=70 / passed / blocked / red_light_count / recommendation）+ CHECKLIST_PASS_THRESHOLD=42
  - domain/rule_engine/checklist.py（~250 行）— run_checklist()：7 项纯函数评分引擎（应急金/四大险/集中度/3年期限/理由理性/仓位控制/冷静期），消费 RiskDefaults 阈值
  - use_cases/run_checklist.py（~140 行）— run_pre_trade_checklist（解析理由→evaluate_reasons→run_checklist→生成警告）/ get_checklist_items_description（UI 数据）
  - api/decisions.py 新增 2 路由（4→6 路由）— POST /api/decisions/checklist + GET /api/decisions/checklist/items
  - main.py 128 行不变（复用已注册的 decisions_router）
  - 更新 2 个 __init__.py 导出（domain/models + domain/rule_engine）
  - 消费 M3 W1 的 evaluate_reasons() 红灯计数 → 注入清单第 5 项评分
  - 新增 20 个测试（141 → 161），161/161 全绿 ✅
  - 新文件 domain 层零 infra import ✅（不变式 #9 + #10 达成）
  - checklist.py 全部纯函数，仅引用 defaults.py 阈值常量（不变式 #12）

## 进行中
- 无

## 阻塞项
- 无

## 下次会话计划
- M3 W3: 凌晨工厂接入决策质量分月度报告 — 读 05 + 07
- M3 W4: 前端交易录入表单集成复盘 + 清单入口
- 持续: services/ 层 akshare 直调逐步迁入 infra/data_source（绞杀者模式）
- 持续: 调用方逐步直接 import 新模块（替代 shim 重导出）后删除 services/agent_memory.py

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

**会话 7**（M1 W3 下半 — infra/data_source 五分法）
- 任务：创建 infra/data_source 五分法分桶接口，消除不变式 #6 违反（api 层数据源越界）
- 产出：
  - 8 个新文件：6 个 bucket 包（market/fundamental/macro/alt/synthetic/providers）+ fallback.py + 更新 __init__.py
  - market/search_funds()：基金搜索，委托 fund_monitor + 降级 fund_rank，完整逻辑从 api/news.py 迁入
  - alt/get_stock_news()：个股新闻，委托 news_data.get_stock_news_by_code()
  - 修复 api/chat.py（line 176）：akshare.stock_news_em 直调 → infra.data_source.get_stock_news
  - 修复 api/news.py（line 149）：akshare.fund_name_em 直调 → infra.data_source.search_funds
  - 新增 9 个测试（37 总数）：bucket importable ×6 + facade exports + fallback module + AST 不变式 #6 检查
  - api 层 akshare/tushare 直调：2 → 0（不变式 #6 api 层达成）
  - 37/37 骨架测试全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 37/37 通过
  - 🟢 确认无影响：API URL 路径不变，前端零改动
  - 🟢 确认无影响：数据返回格式不变（chat 新闻注入格式 / fund search 返回结构）
  - 🟡 建议评估：services/ 层仍有 30+ 文件直调 akshare（绞杀者模式，M1 W4 scope）
  - 🟡 建议评估：api/chat.py httpx 直调（不变式 3 违反，待 W4 修复）

**会话 8**（M1 W4 方案 C 落地）
- 任务：mypy strict 配置 + CI 集成 + main.py 行数 linter + provider adapter stubs
- 产出：
  - pyproject.toml（mypy strict 配置：domain/ infra/ use_cases/ 严格模式，services/ api/ 宽松模式）
  - 修复 19 处类型注解（7 文件：protocols/cache/store/llm_client/data_source + models + memory_cache + file_store + gateway）
  - mypy 17 源文件通过，0 错误
  - scripts/lint_main_py.py（不变式 #8 红线 200 行）
  - CI 新增 2 个 job（mypy-strict + lint-main-py），现有 4 个 job
  - 3 个 provider adapter stubs（TushareProvider / AkshareProvider / BaostockProvider）
  - 新增 11 个测试（37 → 48），48/48 全绿
  - requirements-dev.txt 增加 mypy>=1.8.0
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 48/48 通过
  - 🟢 确认无影响：API URL 路径不变，前端零改动
  - 🟢 确认无影响：类型注解改动不影响运行时行为（from __future__ annotations 延迟求值）
  - 🟡 建议评估：services/ 层仍有 30+ 文件直调 akshare（绞杀者模式继续）
  - 🟡 建议评估：api/chat.py httpx 直调（不变式 3 违反，待 M2 修复）

**会话 9**（M2 W1 Agent Memory 拆三处）
- 任务：agent_memory.py 1277 行拆分为 3 个目标模块
- 产出：
  - domain/services/user_preference_service.py（~340 行）— 偏好/画像/铁律/情绪/生活事件/待审洞察/审批工作流
  - domain/rule_engine/decision_archive.py（~380 行）— 决策热/冷分层/自定义规则/规则检查/上下文接力/自动提取队列/批量提取
  - services/agent_memory.py 改为薄 re-export shim（~95 行）— 保持 9+ 处调用方向后兼容
  - build_memory_summary() 降级为 stub 返回空串（LLM 记忆注入删除）
  - 新增 7 个测试（48 → 55），55/55 全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 55/55 通过
  - 🟢 确认无影响：API URL 路径不变，前端零改动
  - 🟢 确认无影响：9+ 处 `from services.agent_memory import ...` 全部通过 shim 重导出正常工作
  - 🟡 建议评估：build_memory_summary 返回空串后，api/chat.py 和 routers/wxwork.py 注入 prompt 的内容变空（设计目标，但需确认前端无异常）
  - 🟡 建议评估：decision_archive.py 从 user_preference_service.py 导入 _route_to_admin / _user_memory_dir / add_pending_insight（不变式 #9 临时例外，M3 抽出为共享 domain utility）
  - 🟡 建议评估：api/chat.py httpx 直调（不变式 3 违反，已知遗留）

**会话 10**（M2 W2 家庭画像问卷）
- 任务：家庭画像问卷（含成员/子账户维度）— FamilyProfile domain model + 问卷页 API + 首个 use_case
- 产出：
  - domain/models/family.py（~250 行）— FamilyProfile / Member / SubAccount frozen dataclass
  - domain/protocols/family_profile.py — FamilyProfileProtocol（不变式 #11）
  - infra/store/family_profile_store.py — 委托 FileStore（不变式 #5）
  - domain/services/family_profile_service.py（~200 行）— 问卷解析/校验/推导/成员查找
  - use_cases/submit_family_questionnaire.py（~120 行）— 首个 use_case，编排 domain + infra
  - api/family_profile.py（~180 行，6 路由）— 问卷提交/画像读取/成员CRUD/子账户CRUD
  - main.py 116 行（+4 行 include_router）
  - 更新 3 个 __init__.py 导出
  - 新增 15 个测试（55 → 70），70/70 全绿
  - 新文件 mypy strict 0 错误
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 70/70 通过
  - 🟢 确认无影响：原有 199 路由 URL 路径不变，前端零改动
  - 🟢 确认无影响：domain 层零 infra import，不变式 #9/#10 保持
  - 🔴 必须同步评估：无（本次为全新模块，无下游消费方）
  - 🟡 建议评估：M2 W3 资产负债表将消费 FamilyProfile.family_id 关联，预留 profile_id 查找
  - 🟡 建议评估：M2 W4 规则引擎将消费 FamilyProfile 字段（risk_preference / family_stage / emergency_months 等）作为输入

**会话 11**（M2 W3 家庭资产负债表 MVP）
- 任务：家庭资产负债表 MVP — BalanceSheet domain model + 过期检测 + /api/family/balance-sheet 路由
- 产出：
  - domain/models/balance_sheet.py（~230 行）— BalanceSheet / BalanceSheetItem frozen dataclass，Tier 1 四类必填（cash_deposits / investments / real_estate / liabilities），每项含 value/currency/last_updated/data_source，过期检测 is_stale() + staleness_report()，computed properties（total_assets / total_liabilities / net_worth / all_items / stale_items / fresh_items）
  - domain/protocols/balance_sheet.py — BalanceSheetProtocol（不变式 #11）
  - infra/store/balance_sheet_store.py — 委托 FileStore collection="balance_sheets"（不变式 #5）
  - domain/services/balance_sheet_service.py（~180 行）— build_balance_sheet / validate_balance_sheet / detect_stale_items / compute_summary，过期阈值 STALE_THRESHOLD_DAYS=30
  - use_cases/manage_balance_sheet.py（~120 行）— submit_balance_sheet / get_balance_sheet / get_balance_sheet_summary / update_category_items
  - api/balance_sheet.py（~170 行，4 路由）— POST /api/family/balance-sheet + GET /{id} + GET /{id}/summary + PUT /{id}/{category}
  - main.py 120 行（+4 行 include_router，仍远低于 200 行上限）
  - 更新 3 个 __init__.py 导出（domain/models + domain/protocols + infra/store）
  - 新增 24 个测试（70 → 94），94/94 全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 94/94 通过（原 70 + 新 24）
  - 🟢 确认无影响：原有 199+6=205 路由 URL 路径不变，前端零改动
  - 🟢 确认无影响：domain 层零 infra import，不变式 #9/#10 保持
  - 🔴 必须同步评估：无（本次为全新模块，无下游消费方）
  - 🟡 建议评估：M2 W4 规则引擎将消费 BalanceSheet 字段（cash_deposits / liabilities / staleness）作为应急金/资产配比规则输入
  - 🟡 建议评估：07 决策清单第 1 条（应急金 ≥ 6 个月）将从 BalanceSheet.cash_deposits 取值
  - 🟡 建议评估：05 凌晨工厂 A 层实时事实计算将从 BalanceSheet 取总资产/净值

**会话 12**（M2 W4 资产配置框架 + 再平衡提醒）
- 任务：资产配置框架 — 配比矩阵 / 偏离度计算 / 再平衡触发 / allocation API
- 产出：
  - domain/rule_engine/defaults.py（~117 行）— AllocationDefaults 12 格矩阵 + 偏离度三档阈值 + RiskDefaults + ScoringDefaults + RebalanceDefaults + StaleDataDefaults
  - domain/models/allocation.py（~175 行）— AllocationTarget / AllocationState / DeviationAnalysis frozen dataclass
  - domain/services/allocation_service.py（~220 行）— compute_target_allocation + analyze_deviation + detect_rebalance_trigger + validate_allocation（全部纯函数）
  - use_cases/manage_allocation.py（~150 行）— compute_allocation_target + analyze_allocation_deviation + check_rebalance_need + save_allocation_override
  - api/allocation.py（~216 行，4 路由）— /allocation/target + /analyze + /rebalance-check + /target-override
  - main.py 124 行（+4 行 include_router，仍远低于 200 行上限）
  - 更新 2 个 __init__.py 导出
  - 修复偏离度文案格式 bug（%.1% → %.1f%）
  - 新增 23 个测试（94 → 117），117/117 全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 117/117 通过（原 94 + 新 23）
  - 🟢 确认无影响：原有 209 路由 URL 路径不变，前端零改动
  - 🟢 确认无影响：domain 层零 infra import，不变式 #9/#10 保持
  - 🟢 确认无影响：allocation_service.py 纯函数无副作用，不影响任何现有服务
  - 🔴 必须同步评估：无（本次为全新模块，消费 W2/W3 的 FamilyProfile + BalanceSheet，无下游消费方）
  - 🟡 建议评估：M3 W1 七点清单第 7 条（配比偏离 >10%）将直接消费 allocation_service.analyze_deviation()
  - 🟡 建议评估：07 决策清单第 3 条（集中度 >25%/40%）将消费 RiskDefaults 阈值
  - 🟡 建议评估：05 凌晨工厂 night_worker_pipeline 将调用 compute_target_allocation + analyze_deviation 出报告

### 2026-05-10
**会话 13**（M3 W1 决策复盘 — 模式 B 事后复盘先上）
- 任务：买入理由多选组件 + 决策质量分计算 + /api/decisions/review 路由
- 产出：
  - domain/models/decision.py（~240 行）— BuyReasonCategory（5 分类）+ SignalLevel（3 级）+ PredefinedReason（8 条预设）+ BuyReason（from_predefined/from_custom）+ DecisionQualityScore（四维 0-25）+ DecisionReview（完整复盘记录）
  - domain/protocols/decision_guard.py — DecisionGuardProtocol（save_review / get_reviews / get_review_by_id / get_review_stats）
  - domain/services/decision_guard_service.py（~210 行）— compute_quality_score / evaluate_reasons / get_predefined_reasons，全部纯函数
  - use_cases/review_decision.py（~175 行）— submit_decision_review / save_review_to_archive / get_user_reviews / get_review_statistics
  - api/decisions.py（~160 行，4 路由）— POST /api/decisions/review + GET /review/{user_id} + GET /review/{user_id}/stats + GET /reasons
  - main.py 128 行（+4 行 include_router，仍远低于 200 行上限）
  - 更新 2 个 __init__.py 导出（domain/models + domain/protocols）
  - 新增 24 个测试（117 → 141），141/141 全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 141/141 通过（原 117 + 新 24）
  - 🟢 确认无影响：原有 213 路由 URL 路径不变，前端零改动
  - 🟢 确认无影响：domain 层零 infra import，不变式 #9/#10 保持
  - 🟢 确认无影响：decision_guard_service.py 纯函数无副作用
  - 🟢 确认无影响：接入现有 decision_archive.add_decision()，格式兼容（新增 type="review" 字段）
  - 🔴 必须同步评估：无（本次为全新模块，下游 M5 归因统计依赖 PREDEFINED_REASONS 枚举）
  - 🟡 建议评估：M3 W2 模式 A（事前提示）将消费 decision_guard_service.evaluate_reasons() 的红灯计数
  - 🟡 建议评估：M5 复盘归因将读 decision_archive 中 type="review" 记录，依赖 quality_score 和 reasons 结构
  - 🟡 建议评估：07 买入理由选项如有增删 → 决策日志 schema 需同步（见 ANCHOR 改动传播表）

**会话 14**（M3 W2 模式 A 事前提示 + 7 点清单完整计算）
- 任务：7 点清单评分引擎 + /api/decisions/checklist 路由 + 消费 evaluate_reasons() 红灯计数
- 产出：
  - domain/models/checklist.py（~120 行）— ChecklistItem + ChecklistResult frozen dataclass + CHECKLIST_PASS_THRESHOLD=42
  - domain/rule_engine/checklist.py（~250 行）— run_checklist()：7 项纯函数评分引擎
  - use_cases/run_checklist.py（~140 行）— run_pre_trade_checklist + get_checklist_items_description
  - api/decisions.py 新增 2 路由（4→6 路由）— POST /api/decisions/checklist + GET /checklist/items
  - 更新 2 个 __init__.py 导出（domain/models + domain/rule_engine）
  - 新增 20 个测试（141 → 161），161/161 全绿
- 状态：✅ 完成
- 影响面：
  - 🟢 确认无影响：tests/test_skeleton_m1.py 161/161 通过（原 141 + 新 20）
  - 🟢 确认无影响：原有 217 路由 URL 路径不变（新增 2 路由无冲突）
  - 🟢 确认无影响：domain 层零 infra import，不变式 #9/#10 保持
  - 🟢 确认无影响：main.py 128 行不变（复用 decisions_router，无新注册）
  - 🟢 确认无影响：消费 M3 W1 的 evaluate_reasons() 是正常 use_case→domain 调用，无 cross-import
  - 🔴 必须同步评估：无（本次为全新模块，下游暂无消费方）
  - 🟡 建议评估：M3 W3 凌晨工厂将调用 run_checklist 生成月度报告
  - 🟡 建议评估：清单阈值来自 RiskDefaults（03 defaults.py），如改 SINGLE_STOCK_MAX 等需重跑清单测试
  - 🟡 建议评估：07 清单新增/删除一条 → checklist.py 需同步改（当前 7 条写死，未来可配置化）
