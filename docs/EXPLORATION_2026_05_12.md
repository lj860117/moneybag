# MoneyBag 前端任务深度探索报告

**报告日期**: 2026-05-12  
**Git 基线**: M5 W3-W4（旧路由 410 Gone + 物理删除）  
**重点**: 4 个待完成前端任务的后端依赖分析

---

## 目录

1. [Task #5: api/behavior.py 行为风控路由](#task-5)
2. [Task #12-14: 移除已废弃前端 API 调用](#task-12-14)
3. [关键发现总结](#关键发现)
4. [实现建议](#建议)

---

<a name="task-5"></a>

## Task #5: api/behavior.py 行为风控路由

### 背景

**状态**: 🔄 代码基础已完成，API 路由待开发  
**文件位置**: 
- 后端规则层: `backend/domain/rule_engine/behavior_intervention_rules.py` (425 行)
- 后端检测层: `backend/domain/services/behavior_detector.py` (203 行)  
- 后端报告层: `backend/domain/services/behavior_reporter.py` (227 行)
- **API 路由**: ❌ `backend/api/behavior.py` **不存在**（需新建）

**关键 TODO** (见 `behavior_intervention_rules.py` 第 18-21 行):
```python
# TODO: 前端集成 — api/behavior.py 需新增路由：
#       GET /api/behavior/guard-status?userId=xxx
#       POST /api/behavior/guard-toggle (body: {enabled, reason})
#       GET /api/behavior/active-interventions?userId=xxx
```

---

### 子任务 5.1: 理解行为风控架构

#### 检测层 (`behavior_detector.py`)

**入口函数**: `detect_patterns()`
```python
def detect_patterns(
    transactions: list[SimpleTransaction],
    market_data: Optional[MarketDataProtocol] = None,
    dynamic_threshold: Optional[float] = None,
    lookback_months: int = 3,
) -> list[BehaviorPattern]:
```

**检测的 7 类行为偏差** (行 133-175):
1. `detect_chasing_high` — 追高倾向（RSI>70）
2. `detect_stop_loss_inconsistent` — 止损不一致
3. `detect_confirmation_bias` — 确认偏误（行业集中）
4. `detect_fomo` — FOMO 交易（大涨日买入）
5. `detect_over_trading` — 过度交易（月交易次数≥5）
6. `detect_high_pe_adding` — 高位加仓（PE 分位>70%）
7. `detect_anchoring` — 锚定效应（成本偏差）

**输出模型** (`BehaviorPattern` 数据类, 行 70-80):
```python
@dataclass
class BehaviorPattern:
    pattern_type: str                      # "chasing_high" | "stop_loss_inconsistent" | ...
    severity: str                          # "mild" | "moderate" | "severe"
    evidence_count: int                    # 符合该模式的交易次数
    total_relevant: int                    # 相关交易总次数
    ratio: float                           # evidence_count / total_relevant
    description: str                       # 人类可读的结论
    supporting_trades: list[str]           # 支撑证据的交易 ID
    market_context: Optional[str]          # 市场背景（波动率档位）
```

**限制**: 仅检测 `transaction_type="manual"` 的交易（行 101-106, 144）

---

#### 干预层 (`behavior_intervention_rules.py`)

**核心数据结构**:

1. **`InterventionRule`** (行 50-58) — 单条干预规则
   - 触发模式 → 对应的干预类型
   - 5 个规则对应 5 类偏差 (行 99-150):
     | 偏差类型 | 干预类型 | 作用字段 | 临时值 | 白名单 |
     |---------|---------|--------|-------|-------|
     | `chasing_high` | `cooldown` | 无 | — | `["rebalance", "auto_invest", "dividend"]` |
     | `over_trading` | `position_limit` | `position_pct_max` | 0.80 | 同上 |
     | `confirmation_bias` | `block` | 无 | — | 同上 |
     | `fomo` | `position_limit` | `position_pct_max` | 0.05 | 同上 |
     | `high_pe_adding` | `confirm` | 无 | — | 同上 |

2. **`ActiveIntervention`** (行 62-69) — 当前生效的干预
   ```python
   @dataclass
   class ActiveIntervention:
       user_id: str
       rule: InterventionRule
       triggered_at: datetime
       expires_at: Optional[datetime]      # "24h" | "end_of_month" | "until_resolved"
       status: str                         # "active" | "expired" | "overridden_by_user"
       trigger_evidence: BehaviorPattern
   ```

3. **`InterventionAction`** (行 73-79) — 最终输出的干预动作
   ```python
   @dataclass
   class InterventionAction:
       action_type: str                    # "prompt" | "limit" | "block"
       message: str                        # 用户可见的干预提示
       m3_overrides: Optional[dict]        # 对 M3 字段的临时覆盖（降级时为 None）
       requires_confirmation: bool
       is_degraded: bool                   # 验证 1 不支持时为 True
   ```

4. **全局开关** (行 158-192)
   ```python
   _guard_enabled_store: dict[str, bool] = {}  # 用户级别的开关状态
   
   def is_guard_enabled(user_id: str) -> bool          # 查询
   def set_guard_enabled(user_id: str, enabled: bool, reason: str) -> None  # 设置
   ```

5. **白名单** (行 198-209)
   - 永久白名单: `{"rebalance", "auto_invest", "dividend"}`
   - 不受任何干预限制

**关键流程**: 
- `evaluate_intervention()` (行 249-351) — 核心干预评估逻辑
- `get_active_interventions()` (行 358-376) — 查询当前生效干预
- `override_intervention()` (行 379-394) — 用户手动覆盖

**验证 1 结果** (行 42): `VERIFICATION_1_RESULT = "not_supported"`
→ 所有干预降级为"纯弹窗提醒"（`is_degraded=True`），M3 字段临时覆盖不支持

---

#### 报告层 (`behavior_reporter.py`)

**入口函数**: `generate_quarterly_report()`
```python
def generate_quarterly_report(
    patterns: list[BehaviorPattern],
    user_id: str,
    quarter: str,                         # "2025Q4"
    volatility_context: Optional[str] = None,
    has_enough_data: bool = True,
) -> BehaviorReport:
```

**输出模型** (行 28-40):
```python
@dataclass
class BehaviorReport:
    user_id: str
    quarter: str                          # "2025Q4"
    patterns_found: int
    report_markdown: str                  # Markdown 格式报告
    volatility_context: str               # 季度波动率背景
    generated_at: datetime
```

**报告结构** (行 47-80):
- 头部: 季度标识 + 波动率背景
- 每个模式一节: 严重程度 + 数据支撑 + 结论
- 尾部: 统计摘要 (显著/中等/轻微各几条) + 免责声明

**限制**: 数据不足 3 个月时返回空报告

---

### 子任务 5.2: 设计 api/behavior.py 路由

**需实现的 3 个路由**（见 `behavior_intervention_rules.py` 18-21 行 TODO）:

#### 路由 1: `GET /api/behavior/guard-status?userId=xxx`

**功能**: 查询用户行为风控总开关状态

**请求**:
```
GET /api/behavior/guard-status?userId=LeiJiang
```

**响应**:
```json
{
  "status": "ok",
  "user_id": "LeiJiang",
  "guard_enabled": true,
  "last_toggled_at": "2026-05-10T14:30:00Z",
  "last_toggle_reason": "用户主动启用",
  "active_intervention_count": 2
}
```

**数据来源**:
- `is_guard_enabled(user_id)` — 从 `_guard_enabled_store` 查询
- `get_active_interventions(user_id)` — 活跃干预列表长度

---

#### 路由 2: `POST /api/behavior/guard-toggle`

**功能**: 切换用户行为风控开关

**请求体**:
```json
{
  "user_id": "LeiJiang",
  "enabled": false,
  "reason": "暂时关闭行为约束以便测试"
}
```

**响应**:
```json
{
  "status": "ok",
  "user_id": "LeiJiang",
  "guard_enabled": false,
  "toggled_at": "2026-05-11T09:00:00Z",
  "warning": "⚠️ 行为风控已关闭，所有交易不受行为约束"
}
```

**副作用**:
- 调用 `set_guard_enabled(user_id, enabled, reason)`
- 记录变更到日志（生产应写 `decision_log`）
- 前端页面顶部常驻提示（关闭状态时）

**影响**: 
- 关闭时，`evaluate_intervention()` 返回 `None`（纯报告模式，检测照常但不触发限制）

---

#### 路由 3: `GET /api/behavior/active-interventions?userId=xxx`

**功能**: 查询当前生效的干预列表

**请求**:
```
GET /api/behavior/active-interventions?userId=LeiJiang
```

**响应**:
```json
{
  "status": "ok",
  "user_id": "LeiJiang",
  "active_interventions": [
    {
      "id": "intv_2026_05_11_001",
      "pattern_type": "chasing_high",
      "severity": "moderate",
      "action_type": "prompt",
      "message": "[仅提醒] 检测到追涨冲动信号（近20日涨幅>15%）。建议 24h 内谨慎操作同标的。",
      "triggered_at": "2026-05-10T14:22:00Z",
      "expires_at": "2026-05-11T14:22:00Z",
      "status": "active",
      "trigger_evidence": {
        "evidence_code": "600519",
        "evidence_name": "贵州茅台",
        "supporting_trades": ["txn_001", "txn_002"]
      },
      "can_override": true,
      "override_requires_confirmation": true
    },
    {
      "id": "intv_2026_05_11_002",
      "pattern_type": "over_trading",
      "severity": "mild",
      "action_type": "limit",
      "message": "[仅提醒] 检测到过度交易信号（本月 5 次手动交易）。建议控制本次交易仓位。",
      "triggered_at": "2026-05-08T10:00:00Z",
      "expires_at": "2026-06-01T00:00:00Z",
      "status": "active",
      "trigger_evidence": {
        "pattern_type": "over_trading",
        "ratio": 0.50
      },
      "can_override": true,
      "override_requires_confirmation": false
    }
  ],
  "total": 2,
  "guard_enabled": true
}
```

**数据来源**:
- `get_active_interventions(user_id)` — 返回 list[ActiveIntervention]
- 每个 intervention 的 `message` 由 `_build_message()` 生成（行 224-246）
- 有效期计算（行 326-333）:
  - `"24h"` → `triggered_at + 24h`
  - `"end_of_month"` → 当月最后一天午夜
  - `"until_resolved"` → `None`（长期有效）

---

### 子任务 5.3: 数据存储方案

**当前状态**: 内存存储（行 157-161）
```python
_guard_enabled_store: dict[str, bool] = {}
_active_interventions: dict[str, list[ActiveIntervention]] = {}
```

**问题**: 
- 应用重启后数据丢失
- 生产应持久化到数据库

**建议方案**:
1. **短期** (MVP): 使用现有 `FileStore` 协议持久化到 `data/users/{user_id}/behavior_interventions.json`
2. **长期** (需求): 迁移到数据库表（参考 M2 的 `BalanceSheetStore` 实现）

**参考实现**: `infra/store/balance_sheet_store.py`（已有模板）

---

### 子任务 5.4: 前端集成点

**预期前端调用**:

1. **登录/加载首页** → 获取 `guard_enabled` 状态
   ```javascript
   const guardStatus = await fetch('/api/behavior/guard-status?userId=' + uid).then(r => r.json());
   if (!guardStatus.guard_enabled) {
     showTopBanner('⚠️ 行为风控已关闭，所有交易不受行为约束');
   }
   ```

2. **用户设置页** → 显示开关，允许切换
   ```javascript
   async function toggleGuard() {
     const result = await fetch('/api/behavior/guard-toggle', {
       method: 'POST',
       body: JSON.stringify({
         user_id: getProfileId(),
         enabled: !currentState,
         reason: '用户主动操作'
       })
     }).then(r => r.json());
     if (result.status === 'ok') {
       showToast(result.warning || '已切换');
     }
   }
   ```

3. **交易/决策页** → 显示活跃干预
   ```javascript
   const interventions = await fetch('/api/behavior/active-interventions?userId=' + uid)
     .then(r => r.json());
   interventions.active_interventions.forEach(intv => {
     if (intv.requires_confirmation) {
       // 显示确认弹窗
       showConfirmDialog(intv.message, () => {
         // 覆盖该干预
         overrideIntervention(intv.id);
       });
     } else {
       // 仅显示提示，不阻挡操作
       showToast(intv.message);
     }
   });
   ```

---

### 关键代码位置

| 内容 | 文件 | 行号 |
|------|------|------|
| 全局开关状态 | `behavior_intervention_rules.py` | 157-192 |
| 白名单检查 | `behavior_intervention_rules.py` | 198-209 |
| 干预评估核心 | `behavior_intervention_rules.py` | 249-351 |
| 活跃干预查询 | `behavior_intervention_rules.py` | 358-376 |
| 用户覆盖 | `behavior_intervention_rules.py` | 379-394 |
| 消息构建 | `behavior_intervention_rules.py` | 224-246 |
| 验证 1 状态 | `behavior_intervention_rules.py` | 42 |

---

<a name="task-12-14"></a>

## Task #12-14: 移除已废弃前端 API 调用

### 背景

**状态**: ✅ 后端路由已返回 410 Gone，前端调用待清理  
**处理时间**: M5 W3（2026-05-10）

**后端状态** (参考 `REFACTOR_STATUS.md` 第 250-262 行):

| 路由 | 原模块 | 状态 | 前端调用位置 | 迁移目标 |
|------|--------|------|----------|---------|
| `GET /api/ai-predict/{code}` | `services/ai_predictor.py` | ✅ 410 Gone | app.js:2508 | `/api/decisions/review` |
| `GET /api/ai-predict/portfolio/{uid}` | `services/ai_predictor.py` | ✅ 410 Gone | app.js:2528 | `/api/decisions/review` |
| `POST /api/ai-predict/batch` | `services/ai_predictor.py` | ✅ 410 Gone | 无调用 | 同上 |
| `GET /api/decisions` | `services/decision_maker.py` | ✅ 410 Gone | app.js:4493 | `/api/decisions/review` + `/api/decisions/monthly-report` |

**关键发现**: 
- ❌ `app.js` 文件仅 759 行，**不存在** line 2508, 2528, 4493
- 这些 API 调用可能在 **`pages/*.js`** 或 **`frontend-patches/` **中
- 需要搜索整个前端目录

---

### 搜索前端死 API 调用

待执行的搜索（在计划阶段暂不执行）:

```bash
# 搜索所有三个废弃 API 的调用
grep -r "ai-predict" frontend-patches/ pages/ --include="*.js" 2>/dev/null
grep -r "/api/decisions" frontend-patches/ pages/ --include="*.js" 2>/dev/null | grep -v POST

# 显示上下文（±10 行）
grep -r -B5 -A5 "ai-predict" frontend-patches/ pages/ --include="*.js"
```

**预期结果**: 找到这 3 个已废弃 API 的前端调用点，理解：
1. 触发该调用的 UI 元素
2. 显示的内容
3. 应如何替换或删除

---

### 子任务 12.1: `/api/ai-predict/{code}` 调用 (app.js:2508)

**预期查找**:
```javascript
// 大概位置（需实际搜索确认）
fetch(`/api/ai-predict/${code}`)
  .then(r => r.json())
  .then(data => {
    // 显示 AI 预测结果？
  })
```

**迁移策略**:
- **选项 A**: 完全删除（如果功能不再需要）
- **选项 B**: 改调 `/api/decisions/review?code=...` → 查询该证券的决策复盘历史
- **选项 C**: 改调 `/api/decisions/monthly-report?user_id=...` → 显示月度决策质量报告

**建议**: 先确认这是什么 UI 功能，再决定迁移方式

---

### 子任务 12.2: `/api/ai-predict/portfolio/{uid}` 调用 (app.js:2528)

**预期查找**:
```javascript
fetch(`/api/ai-predict/portfolio/${uid}`)
  .then(r => r.json())
  .then(data => {
    // 显示投资组合 AI 预测？
  })
```

**迁移策略**:
- 同上，三选一
- 或改调 `/api/decisions/review?user_id=...` 获取该用户的全部决策复盘

---

### 子任务 12.3: `/api/decisions?userId=` 调用 (app.js:4493)

**预期查找**:
```javascript
fetch(`/api/decisions?userId=${uid}`)
  .then(r => r.json())
  .then(data => {
    // 显示决策相关内容
  })
```

**迁移策略** (优先级):
1. **最可能**: 改调 `/api/decisions/review/{user_id}` — 查询该用户的所有决策复盘
2. **次可能**: 改调 `/api/decisions/monthly-report?user_id=...` — 查询月度报告
3. **如果是查询列表**: 在新端点中分页实现

**关键信息**:
- 新的决策 API 已存在 (见 `REFACTOR_STATUS.md` 第 145 行)
- 8 个新路由已实现: POST /review, GET /review/{id}, GET /stats, GET /reasons 等

---

### 新的决策 API 文档

**已实现的替代路由** (见 `api/decisions.py`):

| 旧路由 | 新路由 | 说明 |
|-------|-------|------|
| `GET /api/ai-predict/{code}` | `GET /api/decisions/review/{user_id}?asset_code={code}` | 查询特定资产的决策复盘 |
| `GET /api/ai-predict/portfolio/{uid}` | `GET /api/decisions/review/{user_id}` | 查询用户全部决策复盘 |
| `GET /api/decisions` (旧决策系统 v1) | `GET /api/decisions/review/{user_id}` | 查询用户决策历史 |
| — | `GET /api/decisions/monthly-report?user_id=...` | 月度决策质量报告（新增） |
| — | `GET /api/decisions/monthly-report/summary?user_id=...` | 月度摘要（新增） |

**关键端点**:

```
GET /api/decisions/review/{user_id}
  查询用户的全部决策复盘记录
  返回: list[DecisionReview] + 分页信息
  
GET /api/decisions/review/{user_id}/stats
  查询用户的决策统计
  返回: { total, by_pattern, quality_score_dist, ... }
  
GET /api/decisions/monthly-report?user_id=...&month=2026-05
  查询月度决策质量报告
  返回: MonthlyReport (含 motivation_distribution, quality_trend 等)
```

---

### 实现步骤

#### 步骤 1: 完整搜索

```bash
# 在 frontend-patches/, pages/ 中搜索死 API 调用
cd /Users/leijiang/WorkBuddy/moneybag-for-claudecode

# 搜索 ai-predict 调用
find . -path ./node_modules -prune -o -path ./.venv -prune -o \
  -type f \( -name "*.js" -o -name "*.jsx" -o -name "*.html" \) -print \
  | xargs grep -l "ai-predict" 2>/dev/null

# 搜索 /api/decisions 旧调用（排除 POST /api/decisions/review）
find . -path ./node_modules -prune -o -path ./.venv -prune -o \
  -type f \( -name "*.js" -o -name "*.jsx" \) -print \
  | xargs grep "/api/decisions" 2>/dev/null | grep -v "decisions/review" | grep -v "decisions/monthly"
```

#### 步骤 2: 理解每个调用的上下文

对每个发现的调用，记录:
- 触发该调用的 UI 组件名称
- 显示的内容类型
- 用户操作流程

#### 步骤 3: 选择迁移策略

根据功能，选择新的 API 端点:
- 单证券分析 → `/api/decisions/review?asset_code=...`
- 投资组合分析 → `/api/decisions/review?user_id=...`
- 决策质量报告 → `/api/decisions/monthly-report?user_id=...`

#### 步骤 4: 替换/删除

- 若新 API 能完全替代 → 更新调用
- 若功能已不需要 → 删除整个调用 + 关联 UI
- 若需要混合多个新 API → 编排调用

#### 步骤 5: 测试

- 确认新端点返回的数据格式与前端期望一致
- 若格式不匹配，在新 API 中调整响应结构

---

<a name="关键发现"></a>

## 关键发现总结

### 1. 行为风控系统已成熟，API 层待开发

**现状**:
- ✅ Batch 5-6 完整实现：检测 (203 行) + 干预 (425 行) + 报告 (227 行)
- ✅ 内存存储 + 全局开关 + 白名单 + 活跃干预查询
- ❌ 前端 HTTP API (`api/behavior.py`) 未实现

**工作量**: ~100-150 行新代码（3 个路由 + Pydantic 模型）

**建议优先级**: 🔴 **高**（前端已在 REFACTOR_STATUS 第 423 行标记为 TODO）

---

### 2. 废弃 API 调用位置需全面搜索

**发现**:
- ❌ app.js 只有 759 行，不包含 line 2508/2528/4493 的调用
- 💡 调用可能在 `pages/*.js` 或 `frontend-patches/v6/*.js` 中

**工作量**: 搜索 + 替换（可能 3-5 个地方）

**建议优先级**: 🟡 **中** （后端已返回 410，用户会收到错误）

---

### 3. 新决策 API 已设计完整

**现状**:
- ✅ `api/decisions.py` 8 个路由已实现
- ✅ 支持单资产、投资组合、月度报告三个维度查询
- ✅ Pydantic 请求/响应模型已定义

**可复用**: 
- 响应模型可直接映射到前端
- 分页、排序、统计逻辑已内置

---

### 4. 数据可用性确认

| 数据类型 | 来源 | 可用性 |
|---------|------|-------|
| 行为模式 | `behavior_detector.detect_patterns()` | ✅ 已可生成 |
| 干预规则 | `INTERVENTION_RULES` 常量 | ✅ 已定义（5 种） |
| 全局开关 | `_guard_enabled_store` 内存 | ✅ 可用（需持久化） |
| 活跃干预 | `_active_interventions` 内存 | ✅ 可用（需持久化） |
| 决策复盘 | `decision_review` 存档 | ✅ 已实现（M3 W1） |
| 月度报告 | `generate_monthly_report()` | ✅ 已实现（M5 W1-2） |

---

<a name="建议"></a>

## 实现建议

### 建议 1: 快速实现 `api/behavior.py`

**模板参考**: `api/decisions.py` 前 100 行

**实现清单**:
- [ ] 创建 `backend/api/behavior.py`
- [ ] 定义 3 个 Pydantic 请求/响应模型
- [ ] 实现 3 个路由 (GET guard-status, POST guard-toggle, GET active-interventions)
- [ ] 导入必要的服务函数 (is_guard_enabled, set_guard_enabled, get_active_interventions)
- [ ] 添加到 `main.py` 路由注册
- [ ] 编写 5-10 个单元测试（参考 `tests/test_*.py` 模式）

**预期工作量**: 2-4 小时

---

### 建议 2: 系统搜索前端死 API 调用

**脚本**:
```bash
#!/bin/bash
cd /Users/leijiang/WorkBuddy/moneybag-for-claudecode

echo "=== 搜索 ai-predict 调用 ==="
find . -type f \( -name "*.js" -o -name "*.jsx" \) ! -path "./node_modules/*" ! -path "./.venv/*" \
  -exec grep -l "ai-predict" {} \; 2>/dev/null

echo -e "\n=== 搜索旧 /api/decisions 调用 ==="
find . -type f \( -name "*.js" -o -name "*.jsx" \) ! -path "./node_modules/*" ! -path "./.venv/*" \
  -exec grep -n "\/api\/decisions" {} + 2>/dev/null | grep -v "decisions/review" | grep -v "decisions/monthly"

echo -e "\n=== 详细上下文 ==="
grep -r -B3 -A3 "ai-predict\|\/api\/decisions[^/]" frontend-patches/ pages/ --include="*.js" 2>/dev/null
```

**预期工作量**: 1 小时（搜索 + 记录）

---

### 建议 3: 数据持久化迁移方案

**当前问题**: 
- `_guard_enabled_store` 和 `_active_interventions` 重启后丢失

**短期解决** (MVP):
```python
# 在 behavior_intervention_rules.py 添加
from infra.store import FileStore

_file_store = FileStore()  # 现有基础设施

def _save_guard_state(user_id: str):
    """将开关状态持久化到文件"""
    path = f"data/users/{user_id}/behavior_guard.json"
    _file_store.put(path, {"enabled": is_guard_enabled(user_id)})

def _load_guard_state(user_id: str):
    """从文件恢复开关状态"""
    path = f"data/users/{user_id}/behavior_guard.json"
    data = _file_store.get(path)
    if data:
        _guard_enabled_store[user_id] = data.get("enabled", True)
```

**长期方案** (后续):
- 参考 `M2 W3` 的 `BalanceSheetStore` 实现
- 创建 `BehaviorGuardStore` 协议 + 实现

**预期工作量**: 2-3 小时

---

### 建议 4: 前端集成检查清单

实现前检查:
- [ ] `api/behavior.py` 已创建 + 3 个路由已注册
- [ ] `/api/behavior/guard-status` 返回的数据结构前端可解析
- [ ] `/api/behavior/guard-toggle` 可正确切换开关
- [ ] `/api/behavior/active-interventions` 返回的干预消息格式合理
- [ ] 前端对应页面已准备好调用这 3 个 API
- [ ] 死 API 调用已识别 + 迁移计划已制定

---

## 文件树速查

```
backend/
├── api/
│   ├── decisions.py          ✅ 新决策 API（8 个路由）
│   └── behavior.py           ❌ 待实现（需 3 个路由）
├── domain/
│   ├── services/
│   │   ├── behavior_detector.py      ✅ 7 类检测（203 行）
│   │   ├── behavior_checks.py        ✅ 检测实现
│   │   └── behavior_reporter.py      ✅ 报告生成（227 行）
│   └── rule_engine/
│       └── behavior_intervention_rules.py  ✅ 干预规则（425 行）
├── use_cases/
│   ├── review_decision.py            ✅ 决策复盘编排
│   └── generate_monthly_report.py    ✅ 月度报告编排
└── main.py                           ✅ 路由注册

frontend/
├── app.js                     ✅ 759 行（无死 API 调用）
├── pages/
│   ├── *.js                  ❓ 可能包含死 API 调用
│   └── ...
└── frontend-patches/
    └── v6/
        ├── *.js              ❓ 可能包含死 API 调用
        └── ...

tests/
└── test_*.py                 ✅ 346 条测试全绿
```

---

## 参考资料

- **设计文档**: `docs/design/m7-plus/06-batch-m9-behavior-detection.md`
- **重构状态**: `docs/REFACTOR_STATUS.md` (第 250-262 行)
- **不变式**:
  - 不变式 #1: AI 不预测证券价格
  - 不变式 #8: domain/services 之间禁止互相 import
  - 不变式 #12: 所有硬阈值在 defaults.py 集中管理

---

**报告完成**  
下一步: 确认任务分配，开始实现
