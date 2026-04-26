# 12 - 框架性改造（完整四层架构）

> **何时读**：M1 阶段所有工作。拆 main.py、搭 infra、写 Protocol、迁移老代码时。
> 对应主文档章节：§十三
>
> 📌 **这是 M1 整个月的核心施工图**。文件编号虽然是 12，实施时排在最前。

---

## 🔗 模块契约（⭐ 最重要）

**本文件在 M1 W1 会落地成真正的代码目录结构**，届时所有下游文件都依赖本文件定义的接口。

**上游（本文件依据谁）**：
- `01-core-principles.md` § 三 四层业务架构
- `02-code-audit.md` → 现有代码痛点清单
- `11-risk-metrics.md` → 开工 Checklist + 风险兜底

**下游（谁依赖本文件）**：
- **所有业务模块** → 都按四层落位、接口走 Protocol
- `13-governance.md` CI 规则 → 根据本文件的目录/层级制定 import-linter 规则
- `10-roadmap.md` M1 W1-W4 → 本文件的 5 步迁移

**改动本模块前必须评估**（见 ANCHOR 改动传播表 "12 四层目录结构"）：
- **四层目录结构变动** → 影响所有模块的 import 路径
- **Protocol 签名变动** → M1 W1 后等于接口大改，所有实现类必须同步
- **infra/cache 或 store 接口变动** → 迁移期双写 1 周
- **绞杀者步骤调整** → 同步 `10-roadmap.md` M1 周排

### 🔔 方案 C 钉子

**M1 W1 = 方案 C 启动点**。本文件 §四 "第 1 步：搭空骨架"要求定义 4 个核心 Protocol：
- `CacheProtocol`
- `StoreProtocol`
- `LLMClientProtocol`
- `DataSourceProtocol`

**落地这 4 个 Protocol 的同时**，业务模块文件（03/04/05/06/07/08/09）的"模块契约"头部**开始逐步瘦身**——从文字描述改为"见 XxxProtocol"。

详见 `00-ANCHOR.md` "方案 C 激活提醒"。

**关键不变式**：
- **单向依赖**：`api/` → `use_cases/` → `domain/` → `infra/`
- **domain/services 互相不 import**（只走 protocols）
- **绞杀者模式不推翻重写**

---

## 一、当前框架健康度体检

| 指标 | 当前值 | 健康线 | 诊断 |
|---|---|---|---|
| `main.py` 行数 | **4044** | <500 | 🔴 god file，任何修改都可能 break |
| `main.py` 路由数 | **199 个全挤在一个文件** | 每 router <20 条 | 🔴 没有按业务域拆分 |
| `routers/` 目录 | 只有 2 个文件（profiles / wxwork）| — | 🟠 有架子没用上 |
| `routes/` 目录 | **空壳**（只有 `__init__.py`）| — | 🟠 半成品，之前想做没做完 |
| `services/` 文件数 | 75 个 | — | ⚪ 数量本身没问题 |
| `services/` 内部互相 import | **179 次**（decision_maker 被 15 处引用）| 应走接口层 | 🔴 深度耦合 |
| 各 service 里模块级 `_cache = {}` | **46 个文件各自造轮子** | 统一缓存层 | 🔴 三套缓存并存 |
| 直接读 `DATA_DIR` | **16 个文件** | 统一持久化层 | 🟠 换 DB 要改 16 处 |
| 领域模型 | dict 到处传 | 独立 domain 层 | 🟠 |
| `config.py` | 258 行单文件 | 按环境拆分 | 🟡 |

**一句话**：不改底座，后续新增功能会把 `main.py` 推到 5500 行、service 耦合 230+ 次。

---

## 二、四层目标架构

```
┌─────────────────────────────────────────────────────────┐
│  API 层 (api/)                                            │
│  按业务域拆的 router，只做路由 + 参数校验                   │
│  - family.py        家庭画像/资产负债表                     │
│  - portfolio.py     持仓/交易                              │
│  - allocation.py    资产配置/偏离度                         │
│  - advisor.py       7 点清单/苏格拉底/三视角                │
│  - signals.py       13 维信号/技术指标                      │
│  - analysis.py      解读报告/复盘归因                       │
│  - llm.py           LLM 代理接口                           │
│  - data.py          行情/宏观/资金面数据                    │
│  - admin.py         健康检查/用户管理                       │
│  - cron.py          定时任务触发                           │
│  每个 <400 行，总计 10-12 个文件                            │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  用例层 (use_cases/)                                      │
│  编排 services，一个用户动作/一个凌晨任务 = 一个用例         │
│  - generate_morning_brief.py     早安简报生成              │
│  - run_decision_checklist.py     7 点决策检查              │
│  - rebalance_check.py            再平衡提醒                │
│  - allocation_checkup.py         资产配置体检              │
│  - attribution_analysis.py       复盘归因                  │
│  - night_worker_pipeline.py      凌晨工厂主流程            │
│  - candidate_pool_build.py       候选池构建                │
│  每个用例 <300 行，不含业务规则，只编排                      │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  领域层 (domain/)                                         │
│  ├── models/    业务对象（dataclass / Pydantic）          │
│  │   - portfolio.py   Portfolio / Holding / Transaction  │
│  │   - family.py      FamilyProfile / Member / SubAccount│
│  │   - advice.py      Advice / Signal / Interpretation   │
│  │   - allocation.py  Allocation / Deviation             │
│  ├── services/  单一职责的领域服务（无互相 import）         │
│  │   - signal_service.py                                 │
│  │   - valuation_service.py                              │
│  │   - recommend_service.py                              │
│  │   - risk_service.py                                   │
│  │   - balance_sheet_service.py                          │
│  ├── rule_engine/  规则引擎（对应 03-rule-engine.md）     │
│  │   - allocation_rules.py    目标配比 / 偏离度阈值        │
│  │   - checklist_rules.py     7 点清单                   │
│  │   - risk_rules.py          集中度/止损规则             │
│  │   - defaults.py            所有硬阈值常量              │
│  └── protocols/  接口定义（Protocol / ABC）               │
│      - data_source.py         数据源接口                  │
│      - llm_client.py          LLM 客户端接口              │
│      - cache.py               缓存接口                    │
│      - store.py               持久化接口                  │
│  服务间**只依赖接口**，不直接 import 其他 service           │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  基础设施层 (infra/)                                       │
│  ├── cache/                                               │
│  │   - base.py              Cache 接口实现                 │
│  │   - memory_cache.py      进程内                         │
│  │   - disk_cache.py        磁盘持久化                      │
│  │   - precomputed.py       凌晨工厂产出的预算缓存           │
│  ├── store/                                               │
│  │   - file_store.py        基于 DATA_DIR 的文件实现        │
│  │   - (未来) sqlite_store.py                             │
│  ├── llm/                                                 │
│  │   - gateway.py           统一 LLM 入口（路由 V3/R1）     │
│  │   - rate_limiter.py      按模型分桶                     │
│  │   - prompt_templates/    prompt 版本管理                │
│  │   - red_team_audit.py    输出硬边界                     │
│  ├── data_source/           按数据类别分桶（五分法，见 §六）│
│  │   ├── market/            基础行情（日线为主）            │
│  │   ├── fundamental/       基本面（估值、财务、ROE）       │
│  │   ├── macro/             宏观（利率/CPI/M2/少数几个）    │
│  │   ├── alt/               另类（北向/融资融券/舆情）      │
│  │   ├── _derivatives/      衍生品（留空，家庭资管不碰）    │
│  │   ├── providers/         Tushare / AKShare / baostock 适配│
│  │   └── fallback.py        三级降级链                     │
│  ├── knowledge/             RAG 知识库                     │
│  │   - retriever.py         向量检索                      │
│  │   - content/             30-50 篇 Markdown 文章        │
│  ├── events/                Event bus（A 层实时重算）     │
│  └── config/                                              │
│      - base.py              通用配置                      │
│      - dev.py / prod.py     环境差异                      │
│      - thresholds.py        业务阈值（从 config.py 拆出）  │
└─────────────────────────────────────────────────────────┘
```

---

## 三、5 大原则

1. **单向依赖**：上层可依赖下层，下层禁止依赖上层；同层之间通过 `domain/protocols/` 定义的接口通信。
2. **领域服务无横向 import**：所有 `from services.X import Y` 改成依赖注入或 `protocols/` 抽象。
3. **基础设施可替换**：cache / store / llm / data_source 全部接口化，本地 Mock 跑单元测试、生产用真实实现。
4. **用例层不写业务规则**：所有阈值/权重/条件判断在 `domain/rule_engine/defaults.py` 或规则引擎里，用例只编排。
5. **config 分层**：阈值从 `config.py` 挪到 `infra/config/thresholds.py`；路径/端口/密钥按环境拆。

---

## 四、旧代码迁移策略（绞杀者模式，不是推翻重写）

**不要**停下所有开发来大重构 6 周。用 **Strangler Fig（绞杀者）模式**：

### 4.1 第 1 步：搭空骨架（3-5 天）

- 创建 `api/ use_cases/ domain/ infra/` 目录树
- 定义 4 个核心 Protocol：`CacheProtocol` / `StoreProtocol` / `LLMClientProtocol` / `DataSourceProtocol`
- 写 `infra/cache`, `infra/store`, `infra/llm/gateway` 三个最基础的实现（包掉现有散装逻辑）

### 4.2 第 2 步：拆 `main.py`（1 周）

- **按 `@app.get("/api/XXX")` 的业务前缀批量切**：
  - `/api/stock-holdings/*` `/api/fund-holdings/*` → `api/portfolio.py`
  - `/api/agent/*` → `api/advisor.py`
  - `/api/macro/*` `/api/factors/*` → `api/data.py`
  - `/api/signals` `/api/daily-signal/*` → `api/signals.py`
  - `/api/analysis/*` `/api/ai-comment/*` → `api/analysis.py`
  - `/api/steward/*` `/api/chat/*` → `api/llm.py`
  - `/api/allocation-*` `/api/recommend-*` → `api/allocation.py`
  - `/api/household/*` `/api/user/*` → `api/family.py`
  - `/api/health/*` `/api/admin/*` → `api/admin.py`
  - `/api/ledger/*` `/api/income-sources/*` → `api/family.py`
- 每拆一组跑一次 smoke test（就算 service 还没清理）
- 目标：`main.py` 剩 < 150 行（只留 FastAPI 初始化 + CORS + 中间件 + include_router）

### 4.3 第 3 步：新模块直接走新架构（从 M2 开始）

- 家庭画像问卷、资产负债表、7 点清单——**这些本来就是新模块，直接按四层写**
- 旧模块先不动

### 4.4 第 4 步：老服务按需重构（随 M2-M5 推进）

- 改动哪个老 service，顺手把它迁到 `domain/services/`，把它的 `_cache = {}` 换成 `CacheProtocol`
- 不改动的老 service 保持原样，不碰
- 典型路径：decision_maker_v2 切换生效时，顺手迁到 domain 层

### 4.5 第 5 步：配额管理（持续）

- 给 `main.py` 加一个 **行数上限 linter**：超过 200 行 CI 挂掉
- 给 `from services.X` 加 import 门禁：只有 `api/ + use_cases/` 能跨目录 import

**关键**：**不允许为了重构而停下业务开发 1 个月**。边做其他功能边迁，绞杀式替换。

但 **M1 四周是功能冻结期**，只改架构不加新业务。

---

## 五、迁移对照表

| 当前位置 | 目标位置 | 切分时机 |
|----|----|----|
| `main.py` 第 80-1600 行路由 | `api/data.py`、`api/signals.py`、`api/portfolio.py` 等 | M1 第 1-2 周 |
| `services/llm_gateway.py` + 46 处 `_cache={}` | `infra/cache/` + `infra/llm/gateway.py` | M1 第 3 周 |
| `services/data_layer.py` + Tushare/AKShare 散装调用 | `infra/data_source/{tushare,akshare}_source.py` | M1 第 4 周 |
| `services/agent_memory.py` 1277 行 | 按 `02-code-audit.md` §四 拆三处 | M2 第 1 周 |
| `services/decision_maker.py` 509 行 | `use_cases/generate_interpretation.py` + `domain/services/interpretation_service.py` | M2 第 2 周 |
| `services/recommend_engine.py` 572 行 | `domain/services/recommend_service.py` + 规则挪到 `domain/rule_engine/allocation_rules.py` | M3 |
| 新增家庭画像 / 资产负债表 / 7 点清单 | 直接按新架构写，不走老路 | M2-M3 |

---

## 六、infra/data_source 五分法（数据源分桶）

当前 `services/` 下数据模块乱成一锅（40+ 个文件：`market_data`/`factor_data`/`macro_data`/`macro_extended`/`macro_v8`/`alt_data`/`news_data`/`policy_data`/`broker_research` ……）。

M1 W4 迁移时**按量化行业通用五分法归位**（衍生品留空，家庭资管不碰）：

| 类别 | 你项目需要 | 现有模块 → 新位置 | 主数据源 |
|------|-----------|-----------------|---------|
| **基础行情** | 日线为主（K 线 / 成交量）| `market_data` + `stock_data_provider` + `fund_rank` → `infra/data_source/market/` | Tushare |
| **基本面** | 估值分位 / PE / PB / ROE | `valuation_engine`（业务逻辑保留 domain）+ 数据采集 → `infra/data_source/fundamental/` | Tushare |
| **宏观** | 利率 / CPI / M2 / LPR（几个核心即可）| `macro_data` + `macro_extended` + `macro_v8` → **合并为 `infra/data_source/macro/`**（3 变 1）| AKShare |
| **另类** | 北向 / 融资融券 / 舆情 / 龙虎榜 | `factor_data` + `alt_data` + `news_data` + `policy_data` → `infra/data_source/alt/` | AKShare（失效监控见 `05-scheduling.md` §6.1）|
| **衍生品** | ❌ **不做** | — | — |

### 6.1 数据源厂商适配层

三级降级链统一实现在 `providers/`：

```
infra/data_source/providers/
├── tushare_provider.py      # 主源：规范 / 稳定 / 基本面全
├── akshare_provider.py      # 第一降级：另类数据覆盖广（⚠️ 爬虫聚合易失效）
├── baostock_provider.py     # 第二降级：纯 A 股日线 / 免费免注册 / 最稳
└── base.py                  # DataSourceProtocol
```

五分法的每个类别（market / fundamental / macro / alt）**都调同一组 providers**，不同类别**优先级可能不同**：
- 基础行情：Tushare > baostock > AKShare
- 基本面：Tushare > AKShare（baostock 基本面弱）
- 宏观：AKShare > Tushare
- 另类：AKShare 唯一

### 6.2 明确不做的部分

| 诱惑 | 为什么不做 |
|------|---------|
| tick / L2 / WebSocket | 日线级规则判断足够，你不做盘中实时 |
| 期货 / 期权 / 可转债 | 家庭资管不碰，碰了违反"别犯蠢"原则 |
| pytdx（通达信逆向）| 合规风险 + Tushare 够用 |
| 高频数据 | DeepSeek 翻译 + 规则引擎完全不需要 |
| 付费版 Tushare 的深度数据 | 家庭自用基础版够 |

**守住"家庭资产管理教练"定位**。数据能支持日线级规则判断就够，不要被"量化 alpha"视角带偏。

---

## 七、收益（为什么一次到位值得）

| 维度 | 当前 | 完成后 |
|---|---|---|
| `main.py` | 4044 行 | <150 行 |
| 新增功能触达的文件数 | 平均 5-8 个（被 services 耦合拖累）| 平均 2-3 个 |
| 单元测试覆盖可能性 | 极难（全是外部 IO）| 容易（接口 Mock）|
| 换数据源 / 换 LLM / 加 Redis | 改 16+ 处 | 改 1 个实现类 |
| 新人接手（如果未来有）| 两周 onboard | 三天 onboard |
| 新增 7 模块 | 代码耦合恶化 | 清爽落地 |

---

## 八、与其他文件的关系

- **执行节奏** → `10-roadmap.md` M1 W1-W4
- **开工前 Checklist** → `11-risk-metrics.md` §七
- **长期维持不腐化** → `13-governance.md`
- **业务模块怎么落在四层里** → `06-family-profile.md` / `07-decision-guard.md` / `09-advisor-features.md`

---

## 📎 相关文件

- **M1 详细周排** → `10-roadmap.md`
- **W2 止损点 / 风险兜底** → `11-risk-metrics.md` §三-六
- **CI / Linter 规则** → `13-governance.md`
- **Agent Memory 迁移细节** → `02-code-audit.md` §四
