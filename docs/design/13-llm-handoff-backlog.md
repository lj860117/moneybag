# LLM 供应商降级与成本记账 — 交接遗留项排期

> 关联提交：`322b04d` fix(llm): 补齐 LLM 供应商降级与成本记账盲区 (P1/P2/P3)
> 状态：本次改动已提交并 push，本地/服务器 git 均干净。以下为刻意未动的深水区 + 收尾项。

---

## 遗留项总览（按优先级）

| # | 遗留项 | 优先级 | 类型 | 风险 | 建议排期 |
|---|--------|--------|------|------|---------|
| 1 | `infra/llm/gateway.py` strangler-fig 迁移 | P2 | 结构性重构 | 高 | 单独排期 |
| 2 | 缺回归测试（定价/降级/计费回填） | P2 | 补测试 | 低 | 近期（建议先做） |
| 3 | 豆包视觉模型选型未实锤 | P3 | 实测验证 | 中 | 择机 |
| 4 | 服务器 `.env` 未显式配豆包视觉变量 | P3 | 配置 | 极低 | 立即（5 分钟） |

---

## #1 — strangler-fig 迁移（P2 深水区）

### 现状
- `backend/services/llm_gateway.py`：**1073 行**，真实实现（路由、降级、计费、熔断、多模态）。
- `backend/infra/llm/gateway.py`：**112 行**，`LLMClient` 薄适配器，注释写明「最终要退休旧 gateway」。
- `backend/infra/llm/` 另含：`chat_guard.py`(4.6KB)、`red_team_audit.py`(10.2KB)、`__init__.py`。

### 目标
新代码走 `infra.llm`，最终退休 `services/llm_gateway`。

### 为什么本次没做
结构性重构，风险 > 收益。动了它等于动所有 LLM 调用路径（chat、计费、降级、多模态），需要全量回归。

### 交接提示（CRITICAL）
做之前**必先读** `docs/design/12-framework-refactor.md`，其中 **Invariant #3：所有 LLM 调用经 `infra/llm/gateway`**。

### 建议实施步骤（后续）
1. 先读 12-framework-refactor.md 全量迁移设计。
2. 盘点 `services/llm_gateway.py` 的调用方清单（`grep "llm_gateway" backend/ --include="*.py"`）。
3. 按「无状态纯函数 → 有状态实例」分层迁移，每迁一个调用方跑一次回归。
4. 全部迁完后，`services/llm_gateway.py` 转为 deprecated 薄壳（转发到 infra），观察一个版本周期再删除。

---

## #2 — 缺回归测试（P2，建议近期先做）

### 现状
- 测试目录 `backend/tests/` 共 30 个测试文件。
- `test_chat_model_routing.py` **只覆盖模型路由**。
- 本次改动的 4 个文件**无对应新测试**：
  - `config.py`（豆包价目）
  - `services/llm_gateway.py`（OCR 降级 + 定价识别）
  - `api/chat_fc.py`（FC 计费）
  - `services/multi_model_scorer.py`（计费）

### 建议补测点
1. **`_pricing_key_from_model` 豆包三档判定**
   - `doubao-seed-2-0-pro-*` → `doubao-pro`
   - `doubao-seed-2-0-lite-*` → `doubao-lite`
   - `doubao-seed-2-0-mini-*` → `doubao-mini`
   - `ep-*` 前缀（ARK 接入点模型）同样走 doubao 档位
   - 无法识别 → 保守 `doubao-pro`
2. **`call_multimodal` 降级链**
   - DeepSeek vision 成功 → 直接返回
   - DeepSeek vision 失败 → 降级到豆包（`LLM_VISION_MODEL_DOUBAO`）
   - 主模型本身是豆包时 → 候选去重，不重复请求
   - 无 key → `source=no_key`
3. **`record_external_call` 回填**（FC / multi_model_scorer）
   - FC 计费走 gateway 记账，验证 cost/tokens 正确回填
   - multi_model_scorer 的计费字段正确

> 参考现有 `test_chat_model_routing.py` 的 mock 方式（httpx mock + 环境变量注入）。

---

## #3 — 豆包视觉模型选型未实锤（P3）

### 现状
- OCR 降级链默认用 `doubao-seed-2-0-pro-260215` 做视觉兜底。
- 已确认 Seed 2.0 全系支持图文输入，但**通用 Pro 模型对图片输入的实际效果未实测**。
- 豆包是否有专门视觉模型（如 Seed-Vision 系列）未调研确认。

### 建议动作
1. 实测一次豆包视觉对**基金截图 / 账单截图**的识别准确率。
2. 与 DeepSeek vision 结果对比（结构化字段：金额、代码、日期）。
3. 若准确率不达标 → 调研豆包专用视觉模型并替换。

### 验证命令（示例）
```bash
# 用一张真实基金/账单截图，走 call_multimodal 强制指定豆包模型
# 对比 LLM_VISION_MODEL 与 LLM_VISION_MODEL_DOUBAO 两个模型的输出
```

---

## #4 — 服务器 `.env` 未显式配豆包视觉变量（P3，立即做）

### 现状（已核实）
服务器 `/opt/moneybag/backend/.env`：
- ✅ `DOUBAO_API_KEY=ark-****`（第 13 行，已配）
- ✅ `LLM_VISION_MODEL=deep****`（第 17 行，DeepSeek 视觉已配）
- ❌ `LLM_VISION_MODEL_DOUBAO` 未配
- ❌ `DOUBAO_VISION_MODEL` 未配

### 环境变量名说明（⚠️ 纠偏交接原文）
代码里是**双名兜底**，优先级：
```python
os.environ.get("LLM_VISION_MODEL_DOUBAO",
               os.environ.get("DOUBAO_VISION_MODEL", "doubao-seed-2-0-pro-260215"))
```
- 首选 `LLM_VISION_MODEL_DOUBAO`（新）
- 兜底 `DOUBAO_VISION_MODEL`（旧）
- 最终硬编码默认 `doubao-seed-2-0-pro-260215`

**结论**：即使不配，降级也能生效（有代码默认值）。但显式配置更清晰，便于后续换专用视觉模型时无需改代码。

### 建议动作（5 分钟）
在服务器 `.env` 追加：
```bash
LLM_VISION_MODEL_DOUBAO=doubao-seed-2-0-pro-260215
```
（同步更新 `backend/.env.example`，避免本地/服务器配置漂移。）

---

## 交接关键上下文（一句话版）

- **项目路径**：本地 `~/WorkBuddy/moneybag-for-claudecode/`，服务器 `ubuntu@150.158.47.189:/opt/moneybag/`，GitHub `lj860117/moneybag`
- **部署纪律**：本地开发 → push GitHub → 服务器 pull → `systemctl restart moneybag` → 验证 PID 变化 + 三方 hash 一致
- **数据源铁律**：Tushare 主、AKShare 降级；LLM 供应商 DeepSeek 主、豆包兜底（千问已下线）
