# 钱袋子 V4 — 从"量化排名器"到"AI 投资管家"

> 技术方案 & 迭代路线图 | 2026-04-14
> 基于：用户灵魂拷问 + 幻方量化对标分析 + 之前"管家壳"建议

---

## 一、问题诊断：为什么现在的钱袋子≈带嘴的东方财富

| 现状能力 | 本质 | 散户痛点解决了吗 |
|----------|------|:---:|
| 30 因子选股 V3 | 规则排名 + AI 调权重 | ❌ 东方财富/同花顺更强 |
| 10 Skill 多空辩论 | 数据灌入 → LLM 写分析文 | ⚠️ 有点用但属于"解说员" |
| AI 预测引擎(MLP+GBM) | 5 天涨跌概率 | ⚠️ 孤立预测，没进决策链 |
| 遗传因子/蒙特卡洛/RL | 高级量化工具 | ⚠️ 有但没串联，各自为政 |
| 盯盘 cron + 企微推送 | 异动检测→推消息 | ✅ 有用，但只报"发生了什么" |
| 配资建议(基金/股票/混合) | 固定模板 + AI 选基 | ⚠️ 还是模板思维 |

**核心问题：AI 是"解说员"而非"侦察兵"——用户自己去东方财富看排名效果差不多。**

---

## 二、V4 核心定位：AI 做三件散户做不到的事

结合之前"管家壳"建议和幻方对标，V4 的差异化定位：

```
┌─────────────────────────────────────────────────────┐
│                  🧠 AI 管家层（新增）                   │
│                                                     │
│  ① 侦察兵：每日扫描信息→提取投资信号（散户看不完）      │
│  ② 体检师：持仓压力测试→风险预警（散户不会算）          │
│  ③ 记分员：追踪AI判断→准确率复盘（没人做这件事）        │
│                                                     │
├─────────────────────────────────────────────────────┤
│              📊 现有量化工具层（保留）                   │
│                                                     │
│  30因子选股 | 多空辩论 | AI预测 | 蒙特卡洛 | 回测     │
│  遗传因子 | 组合优化 | RL仓位 | 另类数据 | 盯盘cron   │
│                                                     │
├─────────────────────────────────────────────────────┤
│              🎨 双模式 UI（改造）                       │
│                                                     │
│  专业模式（你用）: 所有工具全暴露                       │
│  小白模式（老婆用）: 只看 AI 管家的结论和建议           │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## 三、四大升级模块详细技术方案

### 模块 A：🔭 信号侦察兵（Signal Scout）— P0 最高优先级

> **核心价值**：散户每天看不完 200 篇研报 + 50 条政策 + 全球新闻，AI 帮你扫完并提炼出"对你的持仓意味着什么"

#### A1. 多源信息采集管道

```
信息源                    采集方式              频率         成本
─────────────────────────────────────────────────────────
国务院/发改委/证监会      AKShare政策新闻        每30分钟     免费
行业新闻(东财/新浪)       AKShare + 新闻API      每30分钟     免费
Tushare 公告/研报摘要     Tushare Pro            每日         ✅已有Token
美股/港股前夜走势         AKShare global_market   每日开盘前   免费
北向资金/融资融券         alt_data.py(已有)       盘中实时     免费
社交媒体情绪(雪球/东财)   AKShare股吧热度        每小时       免费
```

#### A2. AI 信号提取 Pipeline

```python
# 新文件：services/signal_scout.py (~400行)

class SignalScout:
    """
    信号侦察兵 — 每日扫描多源信息，提取投资信号
    
    Pipeline:
    1. collect()  — 并发拉取 6 大信息源
    2. extract()  — DeepSeek 从原始信息中提取结构化信号
    3. match()    — 将信号匹配到用户持仓 & 关注板块
    4. rank()     — 按影响力 × 置信度排序
    5. deliver()  — 推送给用户（企微 + 前端首页）
    """
    
    def collect(self) -> dict:
        """并发采集，返回 {source: [原始新闻/数据]}"""
        # 复用现有: policy_data.py, news_data.py, alt_data.py, global_market.py
        # 新增: tushare_data.py 的公告/研报接口
    
    def extract(self, raw_data: dict) -> list[Signal]:
        """
        DeepSeek 提取结构化信号
        
        Prompt 设计（关键）:
        - 不是让 AI "总结新闻"，而是问：
          "以下信息中，哪些会在未来1-5天实质影响A股某个板块/个股？"
        - 输出格式：
          {
            "signal": "发改委发布新能源汽车下乡补贴延期至2027年",
            "impact_direction": "positive",
            "impact_magnitude": "medium",   # low/medium/high
            "affected_sectors": ["新能源汽车", "锂电池", "充电桩"],
            "affected_codes": ["300750", "002594", "300014"],
            "logic_chain": "补贴延期→销量预期上修→锂电池需求增→相关龙头受益",
            "confidence": 0.75,
            "time_horizon": "1-5天",
            "source": "发改委官网 2026-04-14"
          }
        """
    
    def match(self, signals: list, user_holdings: list) -> list:
        """
        将信号匹配到用户实际持仓
        
        - 直接命中：信号的 affected_codes 在用户持仓中
        - 行业命中：信号的 affected_sectors 与持仓行业重叠
        - 间接影响：上下游产业链关系（后续迭代）
        
        输出增加字段：
          "relevance_to_you": "你持有宁德时代(300750)，属于锂电池板块，直接受益"
        """
    
    def rank(self, matched_signals: list) -> list:
        """按 impact_magnitude × confidence × relevance 排序"""
    
    def deliver(self, user_id: str, signals: list):
        """
        推送：
        - 企微：每日早盘前推送 TOP 3 信号（简洁文本）
        - 前端：首页新增"📡 今日信号"板块
        - 存档：signals/{user_id}/{date}.json（供复盘用）
        """
```

#### A3. Cron 调度

```
# 在现有 crontab 基础上增加：
# 每日 8:30 开盘前信号扫描
30 8 * * 1-5 cd /opt/moneybag/backend && python scripts/signal_scout_cron.py

# 每日 12:30 午盘更新
30 12 * * 1-5 cd /opt/moneybag/backend && python scripts/signal_scout_cron.py --midday

# 每日 15:30 收盘信号 + 次日展望
30 15 * * 1-5 cd /opt/moneybag/backend && python scripts/signal_scout_cron.py --close
```

#### A4. 企微推送示例

```
📡 今日信号 | 2026-04-14 08:30

🔴 高影响 | 置信度 85%
发改委：新能源汽车下乡补贴延期至2027年
→ 你持有宁德时代(300750)，锂电池板块直接受益
→ 逻辑：补贴延期→销量预期上修→锂电池需求增

🟡 中影响 | 置信度 70%  
美联储鸽派言论，美元走弱
→ 你持有标普500基金(050025)，短期利好
→ 逻辑：美元弱→资金回流新兴市场→A股外资流入

🟢 低影响 | 置信度 60%
白酒行业Q1数据：高端酒增速放缓
→ 你关注板块，暂未持仓

—— 钱袋子 AI 管家
```

**与现有系统的关系**：
- 复用 `policy_data.py` 的政策采集
- 复用 `news_data.py` 的新闻采集
- 复用 `alt_data.py` 的北向/融资融券
- 复用 `global_market.py` 的美股数据
- 新增 `tushare_data.py` 的公告/研报（Token 已就绪）
- 信号结果注入 `agent_engine.py` 的决策循环

---

### 模块 B：🏥 持仓体检师（Portfolio Doctor）— P1

> **核心价值**：不只告诉你"涨了跌了"，而是"如果 XX 发生，你的组合会怎样"

#### B1. 压力测试引擎

```python
# 新文件：services/portfolio_doctor.py (~350行)

class PortfolioDoctor:
    """
    持仓体检师 — 情景模拟 + 风险预警 + 调仓建议
    """
    
    def stress_test(self, holdings: list, scenarios: list) -> dict:
        """
        多情景压力测试
        
        内置情景（可扩展）：
        - "美联储加息50bp"    → 成长股-8%, 银行+3%, 债券-5%
        - "中美关系恶化"       → 科技-15%, 内需+2%, 黄金+5%
        - "房地产暴雷"         → 地产-20%, 银行-10%, 基建+5%
        - "疫情反复"           → 医药+10%, 消费-8%, 线上+5%
        - "A股系统性暴跌20%"   → 全市场 beta 冲击
        
        输出：
        {
          "scenario": "中美关系恶化",
          "portfolio_impact": -8.5%,
          "worst_holdings": [
            {"code": "300750", "name": "宁德时代", "expected_loss": -15%},
          ],
          "hedging_suggestion": "建议增配黄金ETF(518880)对冲",
          "max_drawdown_estimate": -12.3%
        }
        """
    
    def concentration_check(self, holdings: list) -> dict:
        """
        集中度深度检查（增强现有 stock_monitor 的纪律检查）
        
        - 单股集中度
        - 行业集中度（现有）
        - 风格集中度（全是成长 or 全是价值？）
        - 市值集中度（全是大盘 or 全是小盘？）
        - 相关性检查（持仓之间相关系数>0.8 = 假分散）
        """
    
    def ai_diagnosis(self, holdings: list, market_context: str) -> str:
        """
        DeepSeek 综合诊断
        
        不是"点评每只股票"，而是：
        "基于你的持仓结构 + 当前市场环境，你的组合有以下健康问题和调仓建议"
        
        输出结构：
        - 🟢 健康指标（做得好的地方）
        - 🟡 注意事项（潜在风险）
        - 🔴 紧急调仓建议（必须处理的问题）
        - 💊 处方：具体调仓操作建议
        """
```

#### B2. 与现有系统集成

```
现有盯盘 cron（每10分钟）         →  只检测实时异动（保留）
新增体检 cron（每日收盘后 16:00） →  压力测试 + 诊断 + 推送

集成点：
- 复用 monte_carlo.py 的模拟能力
- 复用 portfolio_optimizer.py 的优化算法
- 复用 ai_predictor.py 的预测结果
- stock_monitor.py 的纪律检查作为体检的子项
```

---

### 模块 C：📊 AI 记分员（Judgment Tracker）— P1

> **核心价值**：追踪 AI 每次判断是否正确，建立信任 + 改进模型

#### C1. 判断记录 & 追踪

```python
# 新文件：services/judgment_tracker.py (~300行)

class JudgmentTracker:
    """
    AI 记分员 — 记录每次 AI 判断，N天后验证准确性
    """
    
    def record_judgment(self, judgment: dict):
        """
        记录一次 AI 判断
        
        judgment = {
          "id": "J20260414_001",
          "timestamp": "2026-04-14T08:30:00",
          "type": "signal",           # signal/prediction/recommendation
          "content": "发改委补贴延期利好宁德时代",
          "direction": "bullish",     # bullish/bearish/neutral
          "target_code": "300750",
          "target_price_at_judgment": 198.50,
          "confidence": 0.75,
          "time_horizon_days": 5,
          "source_module": "signal_scout",
          "verify_date": "2026-04-19",
          "status": "pending"         # pending/correct/wrong/mixed
        }
        
        存储：data/judgments/{YYYY-MM}.json
        """
    
    def verify_pending(self):
        """
        每日运行：检查到期的判断，对比实际走势
        
        判断标准：
        - bullish + 实际涨幅 > 0% = correct
        - bullish + 实际涨幅 > 2% = strong_correct
        - bullish + 实际跌幅 > 2% = wrong
        - 其余 = mixed
        """
    
    def get_scorecard(self, period: str = "30d") -> dict:
        """
        生成成绩单
        
        {
          "period": "近30天",
          "total_judgments": 45,
          "accuracy": 62.2%,
          "by_type": {
            "signal": {"total": 20, "accuracy": 78%},   ← 信号侦察最准
            "prediction": {"total": 15, "accuracy": 53%}, ← AI预测一般
            "recommendation": {"total": 10, "accuracy": 60%}
          },
          "by_direction": {
            "bullish": {"total": 30, "accuracy": 65%},
            "bearish": {"total": 15, "accuracy": 55%}
          },
          "best_calls": [...],    ← 最准确的 3 次判断
          "worst_calls": [...],   ← 最离谱的 3 次误判
          "trend": "准确率从上月55%提升至62%"
        }
        """
    
    def learn_from_history(self) -> dict:
        """
        元学习：分析哪类判断最准/最差
        
        → 调整 signal_scout 的 confidence 权重
        → 调整 agent_engine 对不同模块的信任度
        → 这就是"AI 自我进化"的基础
        """
```

#### C2. 前端展示

```
首页新增「🎯 AI 成绩单」卡片：
- 近30天准确率：62% (↑7%)
- 最准领域：政策信号 78%
- 最差领域：短期预测 53%
- 点击展开：所有历史判断 + 验证结果
```

#### C3. 企微推送（每周一次）

```
📊 AI 周度成绩单 | 第16周

准确率：65% (上周 58% ↑)
本周 12 次判断，8 次正确

✅ 最佳：周二预测光伏板块反弹，实际+4.2%
❌ 最差：周四预测银行走弱，实际+1.8%

💡 洞察：政策类信号准确率最高(80%)，
建议更多关注政策驱动的投资机会

—— 钱袋子 AI 管家
```

---

### 模块 D：🐚 管家壳 + 双模式 UI — P1

> 来源：之前的建议"加一层管家壳封装现有模块，双模式UI"

#### D1. 管家路由层（Steward Router）

```python
# 新文件：services/steward.py (~250行)

class Steward:
    """
    管家壳 — 统一编排所有模块，输出用户能理解的结论
    
    不新建功能，而是"串联"现有所有工具的结果
    """
    
    def morning_briefing(self, user_id: str) -> dict:
        """
        早间简报（每日 8:30）
        
        整合：
        - signal_scout → 今日 TOP 3 信号
        - portfolio_doctor → 持仓过夜风险
        - global_market → 美股/港股前夜走势
        - macro_data → 今日重要经济日历
        
        输出：不超过 200 字的"今天你需要知道的事"
        """
    
    def closing_review(self, user_id: str) -> dict:
        """
        收盘复盘（每日 15:30）
        
        整合：
        - stock_monitor → 今日异动汇总
        - signal_scout → 早间信号验证（预判 vs 实际）
        - judgment_tracker → 记录今日所有判断
        - agent_engine → DeepSeek 综合分析
        
        输出：今日盈亏 + 信号命中情况 + 明日关注
        """
    
    def weekly_report(self, user_id: str) -> dict:
        """
        周报（每周五 16:00）
        
        整合所有子模块的周度数据
        """
    
    def ask(self, user_id: str, question: str) -> dict:
        """
        自然语言问答（替代现有 chat.py 的通用问答）
        
        用户说"茅台能买吗" →
        管家自动：
        1. 调 stock_screen 看排名
        2. 调 ai_predictor 看预测
        3. 调 signal_scout 看有无相关信号
        4. 调 portfolio_doctor 看对现有持仓的影响
        5. 调 agent_engine 综合分析
        6. 输出一份完整的"能不能买"报告
        
        而不是像现在一样只让 DeepSeek "根据数据随便说几句"
        """
```

#### D2. 双模式 UI

```
小白模式（默认，给老婆用）：
┌──────────────────────────────┐
│  🏠 首页                      │
│                              │
│  💰 总资产 ¥520,000          │
│  📈 今日盈亏 +¥1,200 (+0.23%) │
│                              │
│  📡 今日信号（TOP 3）          │
│  🔴 新能源补贴延期 → 宁德+     │
│  🟡 美联储鸽派 → 标普500+     │
│                              │
│  🏥 持仓健康度：🟢 良好        │
│  🎯 AI 准确率：65% ↑         │
│                              │
│  💬 问问管家                  │
│  [茅台能买吗？]  [发送]       │
│                              │
│  [📊 专业模式]               │
└──────────────────────────────┘

专业模式（你用）：
  原有所有 tab 保留，新增信号/体检/成绩单 tab
```

---

## 四、迭代路线图（6 周计划）

```
Week 1 ──── Week 2 ──── Week 3 ──── Week 4 ──── Week 5 ──── Week 6
  │            │            │            │            │            │
  ▼            ▼            ▼            ▼            ▼            ▼
Phase 1      Phase 2      Phase 3      Phase 4      Phase 5      Phase 6
信号侦察兵   判断追踪器   持仓体检师   管家路由层   双模式UI     打磨+复盘
```

### Phase 1（Week 1）：信号侦察兵 ← 最大价值点

| 任务 | 具体内容 | 预估 |
|------|---------|------|
| 1.1 | `signal_scout.py` 核心 Pipeline（collect/extract/match/rank） | 2h |
| 1.2 | 信号提取 Prompt 设计 & 调优 | 1h |
| 1.3 | `signal_scout_cron.py` 定时任务（早/午/收盘三次） | 30min |
| 1.4 | 企微推送适配（信号格式 → 纯文本） | 30min |
| 1.5 | 前端首页「📡 今日信号」板块 | 1h |
| 1.6 | Tushare 公告/研报数据接入 | 1h |
| **小计** | | **~6h** |

**交付物**：每天早盘前企微收到 TOP 3 投资信号，带逻辑链

### Phase 2（Week 2）：AI 判断追踪器

| 任务 | 具体内容 | 预估 |
|------|---------|------|
| 2.1 | `judgment_tracker.py` 核心（record/verify/scorecard） | 2h |
| 2.2 | 在 signal_scout / agent_engine 中埋点记录 | 1h |
| 2.3 | 自动验证 cron（每日 16:30 检查到期判断） | 30min |
| 2.4 | 前端「🎯 AI 成绩单」卡片 | 1h |
| 2.5 | 企微周报推送 | 30min |
| **小计** | | **~5h** |

**交付物**：自动追踪每次 AI 判断对不对，周报推企微

### Phase 3（Week 3）：持仓体检师

| 任务 | 具体内容 | 预估 |
|------|---------|------|
| 3.1 | `portfolio_doctor.py` 压力测试引擎 | 2h |
| 3.2 | 5 大内置情景 + 自定义情景接口 | 1h |
| 3.3 | 集中度深度检查（风格/市值/相关性） | 1h |
| 3.4 | DeepSeek 综合诊断 Prompt | 1h |
| 3.5 | 前端「🏥 持仓体检」页面 | 1.5h |
| 3.6 | 企微推送异常体检结果 | 30min |
| **小计** | | **~7h** |

**交付物**：持仓压力测试 + 健康度评分 + 调仓建议

### Phase 4（Week 4）：管家路由层

| 任务 | 具体内容 | 预估 |
|------|---------|------|
| 4.1 | `steward.py` 管家壳（morning/closing/weekly/ask） | 3h |
| 4.2 | 改造现有 `chat.py` → 走管家路由 | 1h |
| 4.3 | "茅台能买吗" 类问题的多模块联动 | 2h |
| 4.4 | API 路由整合（`/api/steward/*`） | 1h |
| **小计** | | **~7h** |

**交付物**：问"茅台能买吗"不再是 DeepSeek 随便说，而是调 5 个模块后综合回答

### Phase 5（Week 5）：双模式 UI

| 任务 | 具体内容 | 预估 |
|------|---------|------|
| 5.1 | 小白模式首页（资产+信号+健康度+成绩单+问答） | 3h |
| 5.2 | 专业/小白模式切换 + 用户偏好记忆 | 1h |
| 5.3 | 术语翻译层（专业术语→大白话） | 1h |
| 5.4 | 移动端适配优化 | 1h |
| **小计** | | **~6h** |

**交付物**：老婆打开就是简洁的管家界面，你切换到专业模式看全部工具

### Phase 6（Week 6）：打磨 + 复盘 + 逆向思维

| 任务 | 具体内容 | 预估 |
|------|---------|------|
| 6.1 | 信号侦察 Prompt 基于 2 周数据调优 | 2h |
| 6.2 | 准确率分析 → 调整各模块权重 | 1h |
| 6.3 | 逆向思维模块原型（非共识发现） | 2h |
| 6.4 | 全流程端到端测试 | 1h |
| 6.5 | 文档 + 架构图更新 | 1h |
| **小计** | | **~7h** |

---

## 五、技术实现约束

### 5.1 与现有系统的关系（只加不砍）

```
新增文件（4个核心）：
  services/signal_scout.py     ← 信号侦察兵
  services/portfolio_doctor.py ← 持仓体检师
  services/judgment_tracker.py ← AI 记分员
  services/steward.py          ← 管家路由层

新增脚本（2个）：
  scripts/signal_scout_cron.py ← 信号定时任务
  scripts/judgment_verify_cron.py ← 判断验证定时任务

新增 Prompt（2个）：
  prompts/signal_extract.md    ← 信号提取专用 Prompt
  prompts/portfolio_diagnosis.md ← 持仓诊断专用 Prompt

改造文件：
  main.py  → 增加 ~10 个 API 路由
  app.js   → 增加信号/体检/成绩单/管家 UI
  agent_engine.py → 注入信号数据 + 判断记录埋点
  stock_monitor_cron.py → 收盘后触发体检 + 判断验证
```

### 5.2 DeepSeek Token 成本控制

| 模块 | 调用频率 | 预估 Token/天 | 月成本 |
|------|---------|-------------|--------|
| 信号提取 | 3次/天 | ~6000 | ¥3-5 |
| 持仓诊断 | 1次/天 | ~3000 | ¥1-2 |
| 管家问答 | ~5次/天 | ~5000 | ¥2-3 |
| 现有(多空辩论等) | 不变 | 不变 | 不变 |
| **合计** | | | **+¥6-10/月** |

### 5.3 数据存储

```
data/
  signals/
    {user_id}/
      2026-04-14.json    ← 每日信号存档
  judgments/
    2026-04.json          ← 月度判断记录
  diagnosis/
    {user_id}/
      latest.json         ← 最新体检报告
      history/
        2026-04-14.json   ← 历史体检
```

### 5.4 腾讯云 2C2G 性能评估

| 操作 | 预估耗时 | 内存占用 |
|------|---------|---------|
| 信号采集(并发6源) | 3-5s | ~50MB |
| DeepSeek信号提取 | 5-8s | ~10MB(网络调用) |
| 压力测试(5情景) | 2-3s | ~30MB |
| 判断验证(批量) | 1-2s | ~10MB |

**结论**：2C2G 完全够用，瓶颈在 DeepSeek API 延迟而非本地计算。

---

## 六、核心差异化对比

### 改完后 vs 东方财富/同花顺

| 能力 | 东方财富 | 同花顺 | **钱袋子 V4** |
|------|---------|-------|:---:|
| 因子排名 | ✅ 更强 | ✅ 更强 | ⚠️ 够用 |
| 海量信息→信号 | ❌ | ❌ | **✅ 独家** |
| 信号→你的持仓 | ❌ | ❌ | **✅ 独家** |
| 持仓压力测试 | ❌ | ⚠️基础 | **✅ 深度** |
| AI判断追踪复盘 | ❌ | ❌ | **✅ 独家** |
| 管家式问答 | ❌ | ❌ | **✅ 独家** |
| 企微即时推送 | ❌ | ❌ | **✅ 独家** |
| 小白模式 | ❌ 专业界面 | ❌ 专业界面 | **✅ 独家** |

### 改完后 vs 幻方量化

| 能力 | 幻方 | **钱袋子 V4** | 差距 |
|------|------|:---:|------|
| 深度学习预测 | ✅ 千GPU | ⚠️ MLP+GBM | 大（但散户够用） |
| 多因子模型 | ✅ 500+因子 | ⚠️ 30因子 | 大（但够散户用） |
| **信息信号提取** | ✅ 内部 | **✅ DeepSeek** | **接近** |
| **风险预警** | ✅ 实时 | **✅ 日度** | 中等 |
| **判断追踪** | ✅ 内部 | **✅ 自动** | **接近** |
| 高频交易 | ✅ | ❌ 不需要 | N/A |

---

## 七、成功标准

| 指标 | 目标 | 衡量方式 |
|------|------|---------|
| 信号命中率 | >60%（1个月后） | judgment_tracker 自动统计 |
| 每日推送有效性 | 至少 1 条信号与持仓相关 | 信号 match 率 |
| 老婆使用频率 | 每周至少看 3 次 | 前端访问日志 |
| AI 问答质量 | 比 V3 多空辩论更具体 | 主观评估 |
| 系统稳定性 | 零宕机 | systemctl 监控 |

---

## 八、今晚就能开始的事

如果现在就想动手，**Phase 1.1（signal_scout.py 核心）** 可以直接开始：
1. 复用现有的 6 个数据源采集函数
2. 写信号提取 Prompt
3. 跑一次看效果
4. 效果 OK → 加 cron + 企微推送

**一句话：先让 AI 当上侦察兵，有了侦察情报，后面的体检/复盘/管家都是水到渠成。**
