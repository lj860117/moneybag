# MoneyBag 重构状态追踪

> 最后更新：2026-04-26
> 对应设计文档：`docs/design/12-framework-refactor.md`
> Git tag 基线：`m1-w1-skeleton`

---

## 当前阶段：M1 W4 — 框架改造（方案 C 落地）

### 绞杀者模式 5 步进度

| 步骤 | 内容 | 状态 | Git Tag / Commit |
|------|------|------|-----------------|
| **1. 搭空骨架** | 四层目录 + 4 Protocol + 3 infra 最小实现 | ✅ 完成 | `m1-w1-skeleton` (29c4d18) |
| **2. 拆 main.py** | 199 路由按业务域切到 21 个 api/*.py | ✅ 完成 | 2026-04-26 |
| 3. 新模块走新架构 | M2 起家庭画像/资产负债表/7 点清单直接四层写 | ⬜ 未开始 | — |
| 4. 老服务按需迁移 | 改哪个顺手迁到 domain/services/ | ⬜ 未开始 | — |
| 5. 配额管理 | main.py < 200 行 linter + import 门禁 | ✅ 完成 | 2026-04-26 |

---

## 四层骨架文件清单

### domain/ — 领域层

```
domain/
├── __init__.py
├── models/
│   └── __init__.py          # LLMResponse dataclass
├── protocols/
│   ├── __init__.py          # 重导出 4 Protocol
│   ├── cache.py             # CacheProtocol
│   ├── store.py             # StoreProtocol
│   ├── llm_client.py        # LLMClientProtocol
│   └── data_source.py       # DataSourceProtocol
├── services/
│   └── __init__.py          # 占位（不变式 #9：禁止互 import）
└── rule_engine/
    └── __init__.py          # 占位
```

### infra/ — 基础设施层

```
infra/
├── __init__.py
├── cache/
│   ├── __init__.py          # 导出 MemoryCache
│   └── memory_cache.py      # CacheProtocol 实现（内存 + TTL + put/expire/keys）
├── store/
│   ├── __init__.py          # 导出 FileStore
│   └── file_store.py        # StoreProtocol 实现（原子写 + .bak 恢复）
├── llm/
│   ├── __init__.py          # 导出 LLMClient
│   └── gateway.py           # LLMClientProtocol 适配器（代理到老 LLMGateway）
├── data_source/             # M1 W3 五分法分桶（不变式 #6）
│   ├── __init__.py          # Facade：导出 get_stock_news / search_funds
│   ├── fallback.py          # 三级降级链占位（M1 W4 实现）
│   ├── market/              # 市场数据（已实现 search_funds）
│   ├── fundamental/         # 基本面数据（占位）
│   ├── macro/               # 宏观数据（占位）
│   ├── alt/                 # 另类数据（已实现 get_stock_news）
│   ├── synthetic/           # 合成/复合数据（占位）
│   └── providers/           # 原始提供商适配器（M1 W4 stub 完成）
│       ├── __init__.py      # 重导出 3 个 Provider
│       ├── tushare_provider.py   # TushareProvider stub
│       ├── akshare_provider.py   # AkshareProvider stub
│       └── baostock_provider.py  # BaostockProvider stub
├── knowledge/
│   └── __init__.py          # 占位
├── events/
│   └── __init__.py          # 占位
└── config/
    └── __init__.py          # 占位
```

### api/ — 路由层（M1 W2-W3 拆分成果）

```
api/
├── __init__.py
├── shared_helpers.py        # 公共辅助（_build_market_context / _build_portfolio_context / classify_chat_intent 等）
├── factors.py               # 8 路由 — 北向资金/融资融券/国债/SHIBOR/股息/情绪/主力流
├── macro.py                 # 9 路由 — M1/社融/LPR/涨跌/美林时钟/V8/GDP/龙虎榜
├── global_market.py         # 7 路由 — 美股指数/外汇/美联储/全球PE/快照/影响/决策包
├── policy.py                # 5 路由 — 房地产/房价/政策新闻/全主题/政策影响
├── market_factors.py        # 4 路由 — 大宗商品/解禁/ETF流/综合
├── alt_data.py              # 6 路由 — 北向详情/融资详情/龙虎榜/大宗交易/行业流
├── quant.py                 # 14 路由 — ML选股/因子IC/蒙特卡洛/AI预测/遗传因子/组合优化/RL仓位/LLM因子
├── broker.py                # 3 路由 — 券商共识/最新研报/个股研报
├── analysis.py              # 5 路由 — 分析历史/最新/详情/对比/外部接收
├── scenario.py              # 3 路由 — 情景列表/情景分析/自定义情景
├── holdings.py              # 19 路由 — 股票/基金持仓 CRUD + 盯盘预警 + 深度分析 + 持仓智能
├── portfolio.py             # 21 路由 — 交易流水/资产管理/净资产/盈亏/加仓/迁移/体检/风控/配置建议
├── signals.py               # 11 路由 — 买卖信号/入场时机/智能定投/止盈止损/每日信号/回测/筛选
├── news.py                  # 12 路由 — 新闻/基金信息/政策影响/技术指标/宏观/基金搜索
├── user.py                  # 15 路由 — 用户数据/偏好/家庭汇总/OCR记账/记账CRUD/收入源/数据审计
├── chat.py                  # 3 路由 — /chat（非流式）、/chat/stream（SSE 流式）、/models
├── dashboard.py             # 6 路由 — /dashboard（三级降级）、/nav、/market-status、/glossary、/health
├── agent.py                 # 18 路由 — Agent 记忆/画像/铁律/情绪/生活事件/待审/分析/信号
├── steward.py               # 7 路由 — 管家 ask/briefing/review、regime、llm-usage、weekly-report
├── enhance.py               # 5 路由 — AI 点评（股票/基金）、存款建议、资产诊断、今日关注
└── misc.py                  # 16 路由 — 决策日志/备份/信号侦察兵/判断追踪/盈利预测/估值/推荐/敞口
```

### use_cases/ — 用例层（M2 起填充）

```
use_cases/
└── __init__.py              # 占位
```

### 测试

```
tests/
├── conftest.py
├── README.md
├── smoke-snapshot-m1-w1.txt
├── test_account_isolation.py
├── test_consistency.py
├── test_data_honesty.py
├── test_memory_e2e.py
├── test_red_team.py
├── test_skeleton_m1.py      # 48 条冒烟测试（全绿）
└── test_trading_calendar.py
```

---

## Protocol → 实现 对照表

| Protocol | 定义位置 | 当前实现 | 计划实现 |
|----------|---------|---------|---------|
| `CacheProtocol` | `domain/protocols/cache.py` | `infra/cache/memory_cache.py` (MemoryCache) | DiskCache, PrecomputedCache (M1 W4+) |
| `StoreProtocol` | `domain/protocols/store.py` | `infra/store/file_store.py` (FileStore) | SqliteStore (post-M2) |
| `LLMClientProtocol` | `domain/protocols/llm_client.py` | `infra/llm/gateway.py` (LLMClient adapter) | 独立实现替代老 LLMGateway (M2) |
| `DataSourceProtocol` | `domain/protocols/data_source.py` | `infra/data_source/__init__.py` (Facade：search_funds / get_stock_news + 降级逻辑) | TushareProvider ✅stub, AkshareProvider ✅stub, BaostockProvider ✅stub (M2 填充实现) |

---

## 关键共存注意事项

1. **FileStore 与 persistence.py 共享 DATA_DIR** — 两者用相同的 SHA256[:16] 哈希，可同时操作 `data/users/`。改其中一个的哈希算法必须同步改另一个。
2. **LLMClient 通过 lazy import 代理到 services/llm_gateway.py** — 老 gateway 520 行代码不动，新代码 import `infra.llm.LLMClient`。M2 时 gateway 实现搬到 infra/。
3. **`_cache = {}` 已清零** — M1 W3 完成：47 处散装缓存全部迁移到 `infra.cache.MemoryCache`，api 层和 services 层 dict 式读写已归零。⚠️ services/llm_gateway.py 内部磁盘缓存持久化逻辑仍直接访问 `MemoryCache._data`（已知技术债）。
4. **api 层 akshare/tushare 直调已清零** — M1 W3 完成：`api/` 目录内 `import akshare` 归 0。⚠️ services/ 层仍有 30+ 文件直调 akshare（绞杀者模式，M1 W4 scope）。
5. **main.py 112 行** — 已达成 97% 降幅（4044 → 112），剩余 2 个路由为根路径健康检查。CI 红线 200 行已配置（scripts/lint_main_py.py + ci.yml lint-main-py job）。

---

## 方案 C 状态

| 检查项 | 状态 |
|--------|------|
| 4 个核心 Protocol 定义 | ✅ |
| import-linter 配置 | ✅ 已配（.importlinter，4 个 contract 全过） |
| mypy strict 配置 | ✅ 已配（pyproject.toml, domain/infra/use_cases strict, 17 文件 0 错误） |
| CI 集成 | ✅ 已配（.github/workflows/ci.yml, 4 个 job: import-linter + mypy-strict + lint-main-py + smoke-tests） |
| main.py 行数上限 linter | ✅ 已配（scripts/lint_main_py.py, 红线 200 行, CI 集成） |

---

## 变更日志

| 日期 | 内容 | Commit |
|------|------|--------|
| 2026-04-25 | M1 W1 骨架搭建完成：4 层目录 + 4 Protocol + MemoryCache + FileStore + LLMClient adapter + 28 测试全绿 | `29c4d18` |
| 2026-04-26 | M1 W2 main.py 拆分完成：199 路由 → 21 个 api/*.py + main.py 112 行，降幅 97%，零重复零遗漏 | — |
| 2026-04-26 | M1 W3 统一缓存层完成：47 处 `_cache = {}` → MemoryCache，~200+ 处 dict 式读写迁移，28/28 → 37/37 测试全绿 | — |
| 2026-04-26 | M1 W3 infra/data_source 五分法完成：6 个 bucket 包 + facade + fallback 占位，api 层 akshare 直调清零，新增 9 个测试 | — |
| 2026-04-26 | M1 W4 方案 C 落地：pyproject.toml mypy strict（17 文件 0 错误）+ scripts/lint_main_py.py（红线 200 行）+ CI 新增 mypy-strict / lint-main-py job + 3 个 provider adapter stubs + 11 个新测试（48 总数全绿） | — |
