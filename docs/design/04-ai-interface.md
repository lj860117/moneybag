# 04 - AI 调用规范 + 字段硬边界 + 对话受限

> **何时读**：写任何 LLM 调用代码、加新 prompt、做 AI 功能时。
> 对应主文档章节:§六

---

## 🔗 模块契约（M1 W1 前:文档约束 / M1 W1 后:见 Protocol）

**上游（本模块消费谁的输出）**：
- `03-rule-engine.md` → 规则引擎产出的"事实"（偏差、集中度、触发项）作为 prompt 输入
- `08-knowledge-rag.md` → RAG 检索结果（有观点输出必须引用）
- `infra/llm/prompt_templates/*.md` → prompt 模板库

**下游（谁消费本模块的输出）**：
- `05-scheduling.md` → 凌晨工厂批量调用 + C 层缓存
- `07-decision-guard.md` → 7 点清单红灯时的"金融常识解释"通过本模块生成
- `09-advisor-features.md` → 苏格拉底、三视角、周度小课全部走本网关
- 前端解读页面、对话页面

**改动本模块前必须评估**（见 ANCHOR 改动传播表）：
- **LLM 返回 JSON 结构** → `05` 缓存 key + `08` RAG 引用字段 + 前端渲染
- **禁用词正则** → `13-governance.md` CI grep 脚本必须同步
- **对话锚点规则** → 前端入口必须收敛（无锚点 chat M3 下线）
- **prompt 模板版本** → 新老并存 + `CHANGELOG.md` 记录
- **限额 / 分桶** → `05` 凌晨工厂调度节奏

**关键不变式（破则失去 AI 边界）**：
- AI 不预测证券（见 `00-ANCHOR.md` 不变式 1-2）
- 所有 LLM 调用必须走 `infra/llm/gateway`（不变式 3）
- "有观点"输出必须引用 RAG
- 禁用词正则和 CI 必须一致

**M1 W1 后**：接口变成 `domain/protocols/llm_client.py` 的 `LLMClientProtocol.call(prompt_id, vars)`。

---

## 一、Prompt 设计原则

- **只给必要数据**：不要塞 13 维全量，只给结论性指标
- **只要求翻译**："请用 1-2 句话说明为什么值得关注"
- **不要角色设定**："你是 20 年投资顾问" → 边际收益为零
- **格式约束即可**："输出 JSON" + 字段说明，足够

---

## 二、DeepSeek 两个模型的分工

| 模型 | 擅长 | 新架构角色 | 调用时机 |
|---|---|---|---|
| **V3 (deepseek-chat)** | 语言翻译、结构化输出、摘要 | 把规则引擎产出的事实翻译成人话 | 凌晨批量 / 必要时盘中 |
| **R1 (deepseek-reasoner)** | 给定清晰事实做对比、归因、仲裁 | 复盘归因 / 冲突信号仲裁 | 凌晨少量高价值调用 |

**结论**：让 V3 做翻译、R1 做归因 → 两个模型第一次用在擅长的地方。

---

## 三、Prompt 示例（改造后）

```python
# 资产配置体检 prompt
prompt = f"""
用户当前资产配置：
- 股票：{stock_pct}%，目标：{target_stock}%，偏差：{stock_pct - target_stock:+.0f}%
- 债券：{bond_pct}%，目标：{target_bond}%，偏差：{bond_pct - target_bond:+.0f}%
- 现金：{cash_pct}%

风险指标：
- 持仓集中度：{concentration}%
- 单票最大亏损：{max_loss}%
- 地缘风险等级：{geo_level}

请用 2-3 句话总结当前配置的主要问题和建议关注方向。
不要预测涨跌，不要给出具体仓位百分比。
"""
```

---

## 四、AI 解读层字段级硬边界

为防止"翻译事实"滑回"主观判断"，对 LLM 输出的每个字段加 schema 级约束：

| 字段 | 允许 | 禁止 |
|------|------|------|
| `market_environment` | 引用规则引擎已产出的 regime/资金/估值结论，2 句话内 | 出现"看好/看空/建议/加仓/减仓"等动词 |
| `portfolio_health[*].issue` | 描述已计算的偏差/浮亏事实 | 预测未来走势 |
| `risk_inventory[*].risk` | 从风险画像规则触发项中选择 | 自创风险（未经规则计算） |
| `direction_notes` | 引用候选池输出的方向 + 为什么入选的规则原因 | 推荐具体标的、给仓位 |

### 落地做法

1. `decision_maker_v2.py` 的 prompt 中明确列出禁止词表。
2. 输出后做一次正则校验（禁用词 + 禁用百分比格式 `\d+%仓位`）。
3. `red_team_audit.py` 每次调用都扫一遍，违规直接拒绝入库，拦截率目标 **>99%**。

### 禁用词正则

```python
# infra/llm/red_team_audit.py
BANNED_PATTERNS = [
    r"建议(你|您)?(现在|立即|马上)?(买|卖|加仓|减仓|调仓|清仓)",
    r"(应该|可以|需要)(买|卖|加仓|减仓)",
    r"我(的建议|建议)是",
    r"(预计|预测|将会|即将)(涨|跌|反弹|下跌)",
    r"(具体|精确)?\s*(仓位|比例|金额).{0,5}[:：]?\s*\d+",
    r"(未来|下周|下月|明天|近期)(会|将)\s*(涨|跌)",
]
```

---

## 五、对话场景的受限边界（防越界关键）

§四 只覆盖**结构化 JSON 输出**。但 `/api/chat` 这类自由对话是最大漏洞——用户一句"那我该怎么办"就能诱导 LLM 突破防线给出具体操作。大模型天生有"给解决方案"的倾向，光靠 prompt 约束拦不住。

### 5.1 核心改造：从"自由问答"到"受限追问"

| 维度 | 旧（自由问答）| 新（受限追问）|
|------|-------------|-------------|
| 入口 | 随时想聊就聊 | **必须从一个"数据锚点"进入**（某份体检报告 / 某条规则提醒 / 某个持仓）|
| 话题 | 任意 | **只能围绕数据锚点解释事实** |
| "我该怎么办" | AI 自由回答 | **固定话术**："我只能解释数据说明了什么，具体怎么做需要你自己决定。相关原理参考..." |
| 轮次 | 无限 | **每个锚点最多 5 轮**，超出引导回主页 |
| 上下文 | 累积对话历史 | **每轮重新注入锚点数据 + RAG 检索**，不累积 |

### 5.2 落地实现

```python
# infra/llm/chat_guard.py

FALLBACK_RESPONSE = """
我只能帮你解释数据说明了什么问题，具体怎么操作需要你自己决定。
关于这个偏差，可以先看看：
- 相关原理：{rag_link}
- 7 点决策检查清单：{checklist_url}
- 三视角评估：{multi_view_url}
"""

def chat_with_anchor(anchor_id, user_message, round_num):
    if round_num > 5:
        return "本轮追问已达上限，建议回到主页查看完整报告"
    anchor_data = load_anchor(anchor_id)
    rag_chunks = retrieve(user_message, top_k=3)
    # prompt 里强制只能引用 anchor_data 和 rag_chunks
    response = llm.call(prompt_id="chat_guarded", vars={...})
    if any(re.search(p, response) for p in BANNED_PATTERNS):
        return FALLBACK_RESPONSE.format(...)
    return response
```

### 5.3 入口管控

- 🚫 **旧的无锚点 `/api/chat` 在 M3 下线**
- ✅ 所有对话必须从报告/提醒/持仓"**点进来**"

### 5.4 效果目标

- 对话场景禁用词出现率 **<3%**（比结构化的 1% 宽，因为自由对话表达更多）
- 用户"那我该怎么办"的诱导场景 → **100% 走固定话术**

---

## 六、LLM 调用统一网关

**所有** LLM 调用必须走 `infra/llm/gateway`，禁止业务代码直调 httpx / deepseek SDK。

```python
# infra/llm/gateway.py

class LLMGateway:
    def call(
        self,
        prompt_id: str,        # prompt 模板 ID（必须对应 prompt_templates/ 下的文件）
        vars: dict,            # 模板变量
        model_tier: Literal["light", "heavy"] = "light",  # V3 / R1
        user_id: str = "",
        module: str = "",      # 调用来源，计费标签
    ) -> dict:
        # 1. 读取 prompt 模板（带版本号）
        # 2. 填入 vars
        # 3. 查缓存
        # 4. 速率限制（V3 日限 200 / R1 日限 20）
        # 5. 调用 DeepSeek
        # 6. red_team_audit 扫禁用词
        # 7. 计费记账
        # 8. 写缓存 + 返回
```

**业务代码只能这样用**：

```python
result = gateway.call(
    prompt_id="allocation_checkup",
    vars={"stock_pct": 45, "target": 35, ...},
    model_tier="light",
    user_id="LeiJiang",
    module="decision_maker",
)
```

---

## 七、Prompt 模板管理

所有 prompt 存在 `infra/llm/prompt_templates/*.md`：

```
infra/llm/prompt_templates/
├── allocation_checkup.md       # 资产配置体检
├── portfolio_health.md         # 持仓健康度
├── risk_inventory.md           # 风险清单
├── attribution_report.md       # 复盘归因
├── chat_guarded.md             # 受限对话
└── socratic_question.md        # 苏格拉底提问
```

每个模板文件头加元数据：

```markdown
---
prompt_id: allocation_checkup
version: v2
model_tier: light
max_tokens: 500
banned_output_patterns: [default]
---

# Prompt 内容
```

**业务代码里硬编码 prompt 字符串 → CI 拒绝**（见 `13-governance.md` §15.1.3）。

---

## 八、限额与分桶

按模型分桶限制（旧为单桶 DAILY_LIMIT=100）：

| 模型 | 日限 | 5 分钟爆发 | 用途 |
|------|------|-----------|------|
| V3 (deepseek-chat) | 200 | 20 | 翻译、摘要、大量场景 |
| R1 (deepseek-reasoner) | 20 | 3 | 归因、仲裁 |

**超限行为**：排队（凌晨场景）或降级到规则引擎兜底（白天场景）。

---

## 九、V4 及未来模型

- **Prompt 层**：继续裸奔，极简调用
- **系统工程层**：继续厚代码，不能裸奔
- **能力边界判断**：除非 V4 变成非 Transformer 架构 + 内置统计引擎，否则本规范继续适用

---

## 📎 相关文件

- **LLM 调度策略（凌晨 vs 白天）** → `05-scheduling.md`
- **RAG 知识库（对话必须引用）** → `08-knowledge-rag.md`
- **AI 解读字段的规则引擎输入** → `03-rule-engine.md`
- **CI 对硬编码 prompt 的拦截** → `13-governance.md`
