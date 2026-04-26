# MoneyBag 重构进度追踪

> 由 Claude Code 每次会话结束时更新。
> 格式：日期 + 完成的任务 + 当前阶段 + 阻塞项

---

## 当前阶段
M1 W2 — 拆 main.py（第一批完成）

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

## 进行中
- [ ] M1 W2 — 拆 main.py（4044 行 → <150 行）
  - [x] 分析路由依赖（199 路由，按 35 组前缀分类，P1/P2/P3 三级）
  - [x] 第一批拆分：64 个路由 → 10 个 Router 文件 ✅
  - [ ] 第二批拆分：P2 中等耦合路由（提取公共辅助模块后拆）
  - [ ] 第三批拆分：P3 高耦合路由（chat/analyze/ocr/dashboard）

## 阻塞项
- 无

## 下次会话计划
- 执行第二批拆分：P2 中等耦合路由
- 需要先提取 `_build_market_context()` 等公共辅助函数到 shared helpers
- 拆 holdings / portfolio / signals / news / ds-enhance / user 等路由组
- 目标：main.py 再减 ~1500 行

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
