# Batch 7：事件解读（M9）

> 来源：`14-m7-plus-enhancement-for-claude.md` §五「市场事件定时解读」
>
> **本文件为独立批次文档，包含开工所需全部信息。不需要翻阅原文。**

---

## 批次概览

| 属性 | 值 |
|---|---|
| 批次编号 | Batch 7 |
| 里程碑 | M9 |
| 产出文件 | `event_template_library.py`、`event_matcher.py` |
| 需读文档 | 本文件 + `08-knowledge-rag.md`（M4 RAG 知识库） |
| 📋 前置依赖 | 无（独立模块） |
| 可并行 | 与 Batch 6 + Batch 8 独立并行 |

---

## 详细设计

### 1. 问题与目标

**问题**：用户看到新闻（加息/降准/政策变化）不知道对自己持仓意味什么。

**目标**：不预测涨跌，只解释"这类事件通常对市场有什么影响，你的配置是否需要关注"。

**命名说明**：不叫"实时解读"，因为数据来源是 Tushare + 凌晨工厂批量调度，不可能做到实时。改为"定时解读"——随凌晨工厂每日批量跑，或用户主动点击"解读今日事件"按钮触发。

### 2. 事件模板库（预定义 20-30 类）

| 事件类型 | 模板内容 | 关联资产 |
|---|---|---|
| 美联储加息 | "加息通常提升美元资产收益率，对新兴市场资金有压力。历史看，加息周期中债券价格波动加大。" | 债券、跨境资产 |
| 央行降准 | "降准增加银行可贷资金，通常利好债市。对股市影响取决于资金是否流入实体经济。" | 债券、股票 |
| 行业政策变化 | "政策变化可能影响行业盈利预期。建议关注该行业在你配置中的占比是否超过目标。" | 相关行业 |
| 地缘事件 | "地缘不确定性通常提升避险资产需求。你的黄金/现金配置是否充足？" | 黄金、现金 |

### 3. 事件检测链路

1. **数据源**：Tushare `major_news` 接口获取每日财经新闻标题/摘要（非全文）
2. **事件识别**：关键词匹配（非 NLP/AI），例如：
   - 标题含"加息"+"美联储" → 匹配"美联储加息"事件
   - 标题含"降准" → 匹配"央行降准"事件
   - 标题含"政策"+行业名 → 匹配"行业政策变化"事件
3. **事件匹配**：`event_matcher.py` 用预定义关键词表匹配，命中后调用对应模板
4. **模板填充**：填充模板中的"你的配置"字段（从规则引擎读取当前配置）
5. **输出**：事件解释 + 对你配置的影响提示 + 相关 RAG 文章链接

### 4. 误报控制

- 使用"关键词组合"而非单关键词（如"美联储"+"加息"而非仅"加息"）
- 每个事件类型定义 2-3 组等价关键词组合，降低单点误报
- 未分类事件用兜底模板，不强行匹配
- 人工巡检：M9 上线首月每周抽查 10 条解读，误报率>20% 则回退调整关键词

### 5. 关键约束

- 每篇解读结尾必须带："历史统计不代表未来，不构成投资建议"
- 单条事件解读**正文 ≤120 字**（不含免责声明和 RAG 链接）
- 不输出 action/position_pct
- 不关联具体标的（只说"你的债券配置"，不说"买 XX 债券基金"）

### 6. 未分类事件兜底

预留"未分类事件"通用模板：
- 内容："检测到 [事件类型] 相关动态。该类事件历史上一方面可能影响 [关联资产大类] 的波动率，另一方面具体影响程度取决于事件后续发展。建议关注你的 [相关配置] 偏离度是否处于目标区间。"
- 触发：当事件关键词匹配不到任何预定义模板时

### 7. 凌晨工厂接入

- 事件解读挂在**阶段 2（规则引擎）和阶段 3（LLM 翻译）之间**，作为规则引擎输出后的内容生成步骤
- 步骤级错误隔离：单步骤失败记录日志并跳过

### 8. 用户可见产出

首页增加"市场动态"卡片，显示最近 3 个事件及对你配置的影响提示；可点击展开详细解读。

### 9. 验收标准

5 类常见事件触发后，系统能在凌晨工厂批量调度周期内（或用户手动点击后 5s 内）生成符合约束的解读文案。

模板库 ≥20 类事件。

---

## 接口契约占位

### `EventTemplate` 数据结构

```python
@dataclass
class EventTemplate:
    """单个事件模板"""
    event_type: str              # 事件类型标识（如 "fed_rate_hike"）
    display_name: str            # 显示名（如 "美联储加息"）
    keyword_groups: list[list[str]]  # 关键词组列表，每组内用 AND 连接，组间用 OR
                                     # 如 [["美联储", "加息"], ["Fed", "加息"]]
    template_text: str           # 模板正文（含占位符 {related_allocation}）
    related_asset_classes: list[str]  # 关联资产大类 ["bond", "cross_border"]
    disclaimer: str = "历史统计不代表未来，不构成投资建议"

@dataclass
class MatchedEvent:
    """匹配到的单个事件"""
    event_type: str
    source_title: str            # 原始新闻标题
    source_date: date
    matched_keywords: list[str]  # 命中的关键词
    filled_text: str             # 填充后的正文（≤120 字）
    disclaimer: str
    rag_links: list[str]         # 相关 RAG 文章链接
    is_fallback: bool            # 是否使用兜底模板
```

### `event_template_library.py` 核心函数

```python
def get_all_templates() -> list[EventTemplate]:
    """返回所有预定义事件模板（20-30 类）。"""

def get_template(event_type: str) -> Optional[EventTemplate]:
    """按事件类型获取模板。"""

def get_fallback_template() -> EventTemplate:
    """返回兜底模板。"""

# 模板注册（文件顶部常量区）
TEMPLATES: list[EventTemplate] = [
    EventTemplate(
        event_type="fed_rate_hike",
        display_name="美联储加息",
        keyword_groups=[["美联储", "加息"], ["Fed", "加息"], ["联储", "升息"]],
        template_text="加息通常提升美元资产收益率，对新兴市场资金有压力。你的{related_allocation}配置是否需要关注。",
        related_asset_classes=["bond", "cross_border"],
    ),
    # ... 20-30 类
]
```

### `event_matcher.py` 核心函数

```python
def match_events(
    news_list: list[NewsItem],
    user_allocation: dict,
    templates: Optional[list[EventTemplate]] = None,
) -> list[MatchedEvent]:
    """
    批量匹配事件。
    参数:
        news_list: Tushare major_news 接口返回的新闻列表
        user_allocation: 用户当前资产配置（从规则引擎读取）
        templates: 事件模板列表（默认使用 get_all_templates()）
    返回:
        MatchedEvent 列表
    """

def match_single_news(
    news: NewsItem,
    templates: list[EventTemplate],
) -> Optional[str]:
    """
    单条新闻匹配事件类型。
    返回: event_type 或 None（未匹配到任何模板，将使用兜底）
    """

def fill_template(
    template: EventTemplate,
    user_allocation: dict,
    news: NewsItem,
) -> str:
    """
    填充模板占位符，生成最终文案。
    约束: 输出正文 ≤120 字。
    """
```

---

## 文件落位

| 文件路径 | 职责 | 预估行数 |
|---|---|---|
| `infra/knowledge/events/event_template_library.py` | 20-30 类事件模板定义 | <300 |
| `infra/knowledge/events/event_matcher.py` | 事件匹配 + 模板填充 | <150 |

> **目录说明**：`infra/knowledge/events/` 是 M7+ 新增子目录，用于存放静态事件模板。与 M4 的 RAG 向量知识库功能分离。

---

## 🔗 跨批次耦合

| 被哪个批次引用 | 引用内容 | 说明 |
|---|---|---|
| 凌晨工厂（M5 已有） | `match_events()` 作为新步骤接入 | 在阶段 2-3 之间调用 |

本批次引用了：
| 引用哪个批次/模块 | 引用内容 |
|---|---|
| M4 RAG 知识库 | 事件解读末尾附加相关 RAG 文章链接 |
| M3 规则引擎 | 读取用户当前资产配置（填充模板占位符） |

---

## 📋 前置依赖

无。本批次为独立模块。

可选增强：M4 RAG 知识库可用时，事件解读末尾可附加相关文章链接。若 RAG 不可用，该字段留空。

需验证：§九 验证 2（凌晨工厂阶段内扩展能力）——影响接入方式但不阻塞模板/匹配逻辑开发。

---

## 🚫 禁止假设

1. **不能假设有 NLP/AI 能力**——事件识别用关键词匹配，不用 NLP
2. **不能输出投资建议**——只解释事件影响，不说"应该买/卖什么"
3. **不能关联具体标的**——只说"你的债券配置"，不说"XX 债券基金"
4. **不能超过 120 字正文限制**——这是防止滑向"变相行情分析"的硬护栏
5. **不能假设 Tushare `major_news` 接口格式**——需在实现时确认返回字段
6. **不能假设凌晨工厂支持步骤扩展**——模板和匹配逻辑独立开发，接入方式取决于验证 2

---

## ⚙️ 全局契约引用

- **凌晨工厂接入**：本批次的 `event_matcher.py` 需实现 `NightWorkerStep` Protocol → 详见 [00-interface-contracts.md](00-interface-contracts.md) §一
- **冲突处理**：实现过程中遇到需要修改凌晨工厂主流程、RAG 接口不够用等情况时 → **停止编码，按 [README.md](README.md) 冲突处理 SOP 记录冲突，等人工确认**
