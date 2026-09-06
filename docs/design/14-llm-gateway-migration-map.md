# 14 - LLM Gateway 绞杀者迁移地图（strangler-fig）

> 关联：遗留项 #1（`infra/llm/gateway.py` 迁移未完成）。
> 状态：**盘点完成，未动代码**。本文档是把「深水区」变成「有地图的浅水区」的准备稿。
> 权威不变式 #3 出处：`docs/design/00-ANCHOR.md:63`（"所有 LLM 调用走 `infra/llm/gateway`"）+ `docs/design/04-ai-interface.md:30`。

---

## 一、现状与目标

### 当前依赖方向（反向适配器，待反转）

```
业务代码（api/ services/ scripts/ routers/ domain/ use_cases/）
   │  from services.llm_gateway import LLMGateway  （lazy import，约 35 处）
   ▼
services/llm_gateway.py  ← 1073 行真实实现（路由/降级/计费/熔断/多模态）
   ▲
   │  lazy import（反向依赖）
infra/llm/gateway.py     ← 112 行 LLMClient 薄适配器（委托 services）
```

### 目标方向（不变式 #3 达成）

```
业务代码
   │  from infra.llm import LLMClient  （或 domain.protocols.LLMClientProtocol）
   ▼
infra/llm/gateway.py     ← 1073 行实现本体搬到这里
   ▲
   │  deprecated 薄壳转发（观察一个版本周期后删除）
services/llm_gateway.py  ← 变成兼容层，转发 infra.llm（方向反转）
```

---

## 二、公共符号清单（`services/llm_gateway.py` 对外暴露）

| 符号 | 类型 | 迁移后的目标位置 | 备注 |
|------|------|-----------------|------|
| `LLMGateway` | 类 | `infra/llm/gateway.py`（实现本体）| 主入口，单例 `instance()` |
| `resolve_default_model` | 纯函数 | `infra/llm/gateway.py` | 路由解析，无状态 |
| `resolve_model_candidates` | 纯函数 | `infra/llm/gateway.py` | 候选链解析 |
| `llm_call` | 模块函数 | `infra/llm/gateway.py` | `LLMGateway.instance().call_sync()` 包装 |
| `llm_usage` | 模块函数 | `infra/llm/gateway.py` | `LLMGateway.instance().get_usage()` 包装 |
| `MODEL_ROUTING` | 常量 | `infra/llm/gateway.py` | 模型路由表 |
| `DOUBAO_MODEL_ROUTING` | 常量 | `infra/llm/gateway.py` | 豆包路由表 |
| `INTERACTIVE_AUTO_MODULES` | 常量 | `infra/llm/gateway.py` | 交互模块集合 |

> `domain/protocols/llm_client.py` 已定义 `LLMClientProtocol`（抽象），`infra/llm/gateway.py` 的 `LLMClient` 已实现其结构子类型。

---

## 三、调用点全量清单（按迁移风险分层）

### 🔴 A 层：模块级 import（迁移最需小心，2 处）

| 文件 | 行 | 导入符号 | 风险 |
|------|----|---------|------|
| `services/scenario_engine.py` | 32 | `MODEL_ROUTING` | 模块加载即绑定常量，改路径后需确认无循环依赖 |
| `api/steward.py` | 18 | `llm_usage` | api 层直接依赖 services 常量/函数，违反单向依赖，迁移时一并修正 |

### 🟡 B 层：函数内 lazy import `LLMGateway`（改路径即可，约 30 处）

按目录归类：

**api/（9 文件）**
- `api/chat.py`（8 处：32/62/114/364/615/813 + 42 行 `resolve_default_model`）
- `api/chat_fc.py`（2 处：644 `LLMGateway` + 720 `resolve_default_model`）
- `api/holdings.py`（3 处：324/1297/1419）
- `api/shared_helpers.py`（1 处：1398）
- `api/fund_detail.py`（1 处：1100）
- `api/dashboard.py`（1 处：86）

**services/（12 文件）**
- `services/portfolio.py`（331）
- `services/ds_enhance.py`（49）
- `services/recommend_engine.py`（838）
- `services/stock_screen.py`（118）
- `services/global_market.py`（443）
- `services/agent_engine.py`（189）
- `services/policy_data.py`（258）
- `services/multi_model_scorer.py`（143）
- `services/pipeline_runner.py`（191）
- `services/factor_data.py`（448）
- `services/llm_factor_gen.py`（48）
- `services/scenario_engine.py`（335，另见 A 层 32 行）

**scripts/（5 文件）**
- `scripts/night_worker.py`（76）
- `scripts/daily_reflection_cron.py`（164）
- `scripts/cache_warmer.py`（1779）
- `scripts/stock_monitor_cron.py`（1075）
- `scripts/weekend_push.py`（224）
- `scripts/prompt_ab_test.py`（33，模块级 `from backend.services...`）

**routers/（1 文件）**
- `routers/wxwork.py`（187）

**use_cases/（1 文件）**
- `use_cases/self_audit.py`（611）

**domain/（1 文件，⚠️ 违反不变式 #10 单向依赖）**
- `domain/rule_engine/decision_archive.py`（194 + 473，2 处）

### 🟢 C 层：测试 mock（迁移后需同步更新，4 文件）

| 文件 | mock 方式 | 迁移影响 |
|------|----------|---------|
| `tests/test_chat_model_routing.py` | `monkeypatch.setitem(sys.modules, "services.llm_gateway", fake)` | mock 的是模块路径，迁移后需改 mock `infra.llm` |
| `tests/test_regression_signal_and_cache.py` | 同上 | 同上 |
| `tests/test_llm_pricing_vision_fc.py` | `import services.llm_gateway as gw_mod` | 直接 import 模块，迁移后改 `infra.llm.gateway` |
| `tests/test_closing_review_leak_regression.py` | `from services.llm_gateway import LLMGateway` + `mock.patch` | 同上 |

---

## 四、迁移顺序建议（Strangler Fig，逐层替换）

> 核心原则（来自 `12-framework-refactor.md` §四）：**不推翻重写，边迁边验证，每迁一批跑一次回归**。

### 阶段 1：实现本体搬进 infra（核心，一次性，风险集中）
1. 把 `services/llm_gateway.py` 的 1073 行实现**整体搬运**到 `infra/llm/gateway.py`，替换现有 112 行 `LLMClient` 适配器。
2. 保留 `LLMClient` 作为兼容别名（或让它继承新实现）。
3. `services/llm_gateway.py` 改为 deprecated 薄壳：`from infra.llm.gateway import *`（转发所有符号）。
4. 验证：跑全量测试（`test_chat_model_routing.py` + `test_llm_pricing_vision_fc.py` + 其余 LLM 相关），确认转发无破坏。

### 阶段 2：业务调用点逐个改路径（低风险，可分批）
1. 先改 🟡 B 层的 lazy import：`from services.llm_gateway import LLMGateway` → `from infra.llm.gateway import LLMGateway`（或改走 `LLMClient`）。
2. 每改一批跑一次 smoke test。
3. 优先改 `api/` 层（消除 api→services 的反向依赖），再 `services/`，最后 `scripts/`。

### 阶段 3：修 A 层模块级 import + domain 越界（高价值，最后做）
1. `api/steward.py` 的 `llm_usage` → 走 `infra.llm.gateway.llm_usage`。
2. `services/scenario_engine.py` 的 `MODEL_ROUTING` → `infra.llm.gateway.MODEL_ROUTING`。
3. ⚠️ `domain/rule_engine/decision_archive.py` 直调 `services.llm_gateway` **违反不变式 #10**（domain 依赖 services），迁移时应改为依赖注入 `LLMClientProtocol`，而不是简单改路径。

### 阶段 4：测试 mock 同步（收尾）
1. 更新 🟢 C 层 4 个测试文件的 mock 目标路径。
2. 补一个「不变式 #3 AST 检查」测试（仿照已有的不变式 #6 检查）：grep `from services.llm_gateway import` 应只剩 `services/llm_gateway.py` 自身的转发。

### 阶段 5：退休旧 gateway（观察一个版本周期后）
1. `services/llm_gateway.py` 保留 deprecated 警告注释 + 转发，观察 1 个版本周期。
2. 确认无遗漏调用点后删除。

---

## 五、风险清单与兜底

| 风险 | 等级 | 兜底 |
|------|------|------|
| 循环依赖（infra.llm ↔ services）| 高 | 阶段 1 搬运后，`infra/llm/gateway.py` 不得再反向 import services；转发壳用单向 `from infra.llm.gateway import *` |
| 单例状态丢失 | 中 | `LLMGateway._instance` 在搬移时保持类定义不变，单例逻辑不受影响 |
| 测试 mock 失效 | 中 | 阶段 4 统一改 mock 路径，逐个验证 |
| domain 越界（不变式 #10）| 高 | 阶段 3 用依赖注入 `LLMClientProtocol` 修复，不简单改路径 |
| 计费/降级行为回归 | 中 | 已有 `test_llm_pricing_vision_fc.py` + `test_chat_model_routing.py` 覆盖，迁移后全量跑 |

---

## 六、不做的事（守住边界）

- ❌ 不搬 `services/llm_gateway.py` 之外的任何文件（本迁移只聚焦 LLM 网关）。
- ❌ 不动 `infra/llm/chat_guard.py` / `red_team_audit.py`（它们已在 infra 层，无需迁移）。
- ❌ 不重写业务逻辑（只搬路径，不优化实现）。
- ❌ 不借机改计费/降级/路由算法（这是另一件事，避免夹带）。
