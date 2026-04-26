# 08 - RAG 金融常识知识库 + 内容生产规范

> **何时读**：做 RAG、写知识库内容、加 AI 解释层、苏格拉底提问、周度教育时。
> 对应主文档章节：§12.3

---

## 🔗 模块契约（M1 W1 前：文档约束 / M1 W1 后：见 Protocol）

**上游（本模块消费谁的输出）**：
- 人工审核的 Markdown 文章（`backend/knowledge/content/*.md`）
- `infra/knowledge/chromadb_impl.py` → 向量库实现

**下游（谁消费本模块的输出）**：
- `04-ai-interface.md` → AI 解读必须引用 RAG 检索结果
- `07-decision-guard.md` → 红灯解释文案 = RAG 检索
- `09-advisor-features.md` → 三视角话术模板、周度小课内容源

**改动本模块前必须评估**（见 ANCHOR 改动传播表）：
- **RAG 文档前置 meta 格式** → `04` prompt 模板 + `09` 小课推送引用字段
- **文章增删** → `07` 延伸阅读 + `09` 周度小课推送池
- **来源等级** （A/B/C/D）→ 内容审核流程
- **向量库实现** → `infra/knowledge/retriever.py` 接口

**关键不变式**：
- **宁可少，不能错**（10 篇扎实 >> 50 篇水的）
- **D 级来源禁止**（自媒体 / 公众号 / AI 自由创作）
- 数字必须标年份和数据源

**M1 W1 后**：接口走 `KnowledgeRetrieverProtocol.retrieve(query, top_k)`。

---

## 一、为什么要做

**AI 每个建议都该有"为什么"**，但 AI 自己编容易错。

不懂金融的用户需要：
- 提醒后能点进去看原理
- 知道建议出自何处，不是瞎编
- 用半年后自己也能看懂报告

---

## 二、知识库内容范围

新增 `backend/knowledge/` 目录，放 30–50 篇权威短文（每篇 500–1500 字）：

| 分类 | 示例主题 |
|------|---------|
| 家庭财务基础 | 家庭财务金字塔 / 应急金的 6 个月法则 / 4% 法则 |
| 资产配置 | 现代组合理论 / 生命周期投资 / 股债再平衡原理 |
| 单类资产 | 为什么黄金占 5-10% / REITs 的角色 / ETF vs 主动基金 |
| 行为金融 | 锚定效应 / 损失厌恶 / 处置效应 |
| 常见陷阱 | 为什么热门基金买完就跌 / 追涨杀跌的数学 |
| 数学常识 | 72 法则 / 复利的力量 / 手续费吃掉多少收益 |

---

## 三、落地技术

1. **向量库**：chromadb / faiss（本地单机够用）
2. **检索**：AI 每次给结论，必须检索 top-3 相关知识段
3. **prompt 硬约束**：prompt 里强制"**只能引用以下资料**"
4. **输出附延伸**：回答末尾附 1-2 条"延伸阅读"链接

---

## 四、内容生产规范（最大的隐藏工作量）

RAG 是个"坑"：AI 生成初稿**必出错**（作者、年份、数据出处会张冠李戴），金融内容错了坑用户。所以 RAG 的内容生产必须单独管理。

### 4.1 来源优先级（从高到低）

| 等级 | 来源 | 举例 |
|------|------|------|
| A（首选）| 公开权威机构教育内容 | 招商银行私行 / 中信私行 / 中金财富的客户教育文章；Bogleheads wiki（英文）|
| B（可用）| 长寿投资经典著作摘录 | 《漫步华尔街》《投资最重要的事》《穷查理宝典》《共同基金常识》 |
| C（谨慎）| 持仓公开 + 长期口碑好的雪球大 V **科普专栏**（非个股分析）| 银行螺丝钉、雪球私募大 V 等 |
| D（**禁止**）| 自媒体/公众号热文、短视频文案、AI 自由创作 | — |

### 4.2 内容选题硬规则

| 能收录 | 不能收录 |
|-------|---------|
| 原理性内容（10 年不变）| "现在该买什么" |
| 数学常识（72 法则/复利）| 市场观点 / 价格预测 |
| 工具对比（ETF vs 主动基金）| 具体产品推荐 |
| 行为偏误提醒 | 大 V 投资组合抄作业 |

### 4.3 生产流程

1. **AI 出初稿 → 人审核 → 标注出处 → 入库**。初稿不是最终稿。
2. 每篇必须写在 `backend/knowledge/` Markdown 文件头标注：

```markdown
---
title: 应急金的 6 个月法则
category: 家庭财务基础
source: 招商银行私人银行《家庭财务健康白皮书》2024
source_url: https://...
reviewer: LeiJiang
reviewed_at: 2026-05-12
version: v1
---

# 正文
...
```

3. 涉及具体数字（如"美股历史年化 10%"）必须给年份范围和数据源。

### 4.4 分批上线

| 批次 | 篇数 | 内容 | 投入 |
|------|------|------|------|
| 核心 10 篇（必做）| 10 | 家庭财务金字塔 / 应急金 6 月法则 / 4% 法则 / 股债再平衡 / 黄金对冲 / 保险优先级 / 复利 / 指数投资 / 定投 / 生命周期投资 | 2-3 周 |
| 扩展 20 篇（上线后 3 个月内）| 20 | 单类资产（REITs/债券/可转债）、行为金融、常见陷阱 | 每周 2 篇 |
| 长尾 20 篇（随用随加）| 20 | 税务、遗产、提前还贷、换房决策等细分场景 | 按需 |

### 4.5 黄金原则

> **宁可少，不能错。10 篇扎实 >> 50 篇水的**。RAG 上线时只要有 10 篇核心就够开跑。

---

## 五、与 AI 解读层的集成

### 5.1 AI 解读必须引用 RAG

`04-ai-interface.md` §4 定的字段硬边界里，`direction_notes`、`market_environment` 等字段如果涉及"有观点"的输出，**必须引用 RAG 检索结果**。

```python
# use_cases/allocation_checkup.py
def generate_checkup(user_id):
    facts = rule_engine.compute(user_id)
    # 根据 facts 关键词检索 RAG
    rag_chunks = knowledge.retrieve(
        query=facts_to_query(facts),
        top_k=3,
        category_hint="家庭财务基础"
    )
    # LLM prompt 强制只能引用 rag_chunks
    interpretation = gateway.call(
        prompt_id="allocation_checkup",
        vars={"facts": facts, "rag": rag_chunks},
        model_tier="light",
    )
    # 附延伸阅读
    interpretation["further_reading"] = [c.title for c in rag_chunks]
    return interpretation
```

### 5.2 对话场景的 RAG 约束

见 `04-ai-interface.md` §5.2：所有对话每轮重新注入 RAG 检索，不累积对话历史。

---

## 六、代码结构

```
infra/
└── knowledge/
    ├── retriever.py                # 向量检索接口
    ├── indexer.py                  # 索引构建（启动时加载）
    ├── chromadb_impl.py            # chromadb 实现
    └── content/                    # Markdown 文章
        ├── family-pyramid.md
        ├── emergency-fund-6-months.md
        ├── 4pct-rule.md
        ├── stock-bond-rebalance.md
        ├── gold-hedge.md
        ├── insurance-priority.md
        ├── compound-interest.md
        ├── index-investing.md
        ├── dca-strategy.md
        └── lifecycle-investing.md

domain/
└── protocols/
    └── knowledge_retriever.py      # 检索接口
```

---

## 七、RAG 检索的降级

| 情况 | 降级 |
|------|------|
| 向量库挂了 | 用关键词匹配（SQL `LIKE`）|
| 相关度都 <0.5 | 返回空，AI 不强凑引用 |
| 索引未构建 | 启动时自动构建，构建中提示"知识库加载中" |

---

## 八、治理

- **每季度抽检 10% 已入库内容**，过期/错误的修订或下架
- **RAG 命中率监控**：每周看"AI 回答附引用率"，目标 >90%
- **每月新增 1-2 篇**（长期运营）

---

## 📎 相关文件

- **AI 解读如何引用 RAG** → `04-ai-interface.md` §4.1
- **对话场景的 RAG 注入** → `04-ai-interface.md` §5.2
- **苏格拉底提问模板来源** → `09-advisor-features.md`
- **周度小课内容来源** → `09-advisor-features.md`
