# 🪙 钱袋子 (MoneyBag) — 完整功能清单

> **版本**: V5.5 | **更新日期**: 2026-04-14
> **架构**: Python FastAPI 后端 + 原生 JS SPA 前端 + DeepSeek LLM + AKShare/Tushare 数据源
> **部署**: 腾讯云轻量 `http://150.158.47.189:8000`（上海 2C2G）
> **用户**: 邀请码制，白名单准入（LeiJiang / BuLuoGeLi）

---

## 📱 前端页面结构

底部 5 个主 Tab + 隐藏功能页：

| Tab | 图标 | 渲染函数 | 说明 |
|-----|------|---------|------|
| **首页** | 🏠 | `renderLanding()` | 净资产仪表盘 / 风险评测向导 / 今日关注 |
| **持仓** | 📈 | `renderStocks()` | 股票持仓 + 基金持仓（双子 Tab） |
| **资讯** | 📰 | `renderInsight()` | 16 个子 Tab 的市场情报中心 |
| **AI分析** | 🤖 | `renderChat()` | AI 对话（SSE 流式 + 规则引擎降级） |
| **资产** | 🏦 | `renderAssets()` | 全资产管理 + 记账 + 收入源 |

**隐藏页面**: 配置方案详情(`renderPortfolio`)、记账本(`renderLedger`)、风险评测问卷(`renderQuiz`)、配置结果(`renderResult`)

---

## 一、用户与身份管理

### 1.1 用户注册与身份验证
| 项目 | 内容 |
|------|------|
| **后端文件** | `routers/profiles.py` |
| **API** | `POST /api/profiles` — 创建 Profile（需邀请码）<br>`GET /api/profiles` — 获取所有 Profile<br>`PUT /api/profiles/{id}` — 更新 Profile |
| **前端入口** | 首次打开的 `ensureProfile()` 弹窗 |
| **设计意图** | 白名单用户制，防止外部滥用。每个用户有独立 profileId，支持绑定企业微信 userId。名字=企微 userid，注册即绑定，实现一步到位的身份打通。 |

### 1.2 管理员工具
| 项目 | 内容 |
|------|------|
| **API** | `POST /api/admin/invite-codes` — 生成邀请码<br>`GET /api/admin/invite-codes` — 查看邀请码列表<br>`POST /api/admin/kick` — 踢出用户 |
| **设计意图** | 管理员通过 adminKey 鉴权，可以批量生成邀请码、查看使用情况、踢出异常用户。所有操作有审计记录。 |

---

## 二、风险评测与资产配置

### 2.1 风险评测问卷
| 项目 | 内容 |
|------|------|
| **后端文件** | `services/portfolio.py`, `config.py` |
| **前端入口** | 首页 → `renderQuiz()` → `renderAmountInput()` → `renderResult()` |
| **设计意图** | 5 题快速问卷评估用户风险偏好，映射到 5 种风险画像（保守🐢 / 稳健🐰 / 平衡🦊 / 进取🦁 / 激进🦅）。每种画像对应不同的股债比和推荐基金组合。问卷 UX 参考支付宝风险评测，但更简洁有趣。 |

### 2.2 智能资产配置方案
| 项目 | 内容 |
|------|------|
| **API** | `GET /api/recommend-alloc` — 动态配置建议（基于估值/恐贪实时调整）<br>`POST /api/allocation-advice` — 配置偏离度分析与再平衡建议 |
| **推荐基金** | 沪深300(110020) / 标普500(050025) / 债券(217022) / 黄金(000216) / 红利低波(008114) / 余额宝 |
| **设计意图** | 不是静态模板，而是根据**实时估值百分位**动态调整股债比。低估时加大股票仓位，高估时增配债券。参考桥水全天候策略的简化版，让普通人也能享受机构级的动态配置。生成"购物清单"告诉用户每只基金买多少钱，附带购买渠道指引。 |

### 2.3 估值动态调整参数
| 估值区间 | 股票占比 | 债券占比 | 现金占比 |
|---------|---------|---------|---------|
| 低估(<20%) | 75% | 15% | 10% |
| 适中(20-80%) | 65% | 25% | 10% |
| 高估(>80%) | 45% | 35% | 20% |

---

## 三、基金持仓管理（V4 交易流水制）

### 3.1 交易流水 CRUD
| 项目 | 内容 |
|------|------|
| **后端文件** | `services/portfolio_calc.py`, `models/schemas.py` |
| **API** | `POST /api/portfolio/transaction` — 添加交易记录<br>`PUT /api/portfolio/transaction/{tx_id}` — 修改<br>`DELETE /api/portfolio/transaction/{tx_id}` — 删除<br>`GET /api/portfolio/history` — 流水历史<br>`POST /api/portfolio/holdings` — 从流水计算持仓 |
| **设计意图** | V3 用"快照"记持仓，无法追溯历史。V4 改为**交易流水制**（BUY/SELL/DIVIDEND），用加权平均成本法自动计算持仓。这样能精确算出每笔盈亏、已实现收益、分红回报率。支持 V3→V4 自动迁移兼容。 |

### 3.2 加仓 & 批量交易
| 项目 | 内容 |
|------|------|
| **API** | `POST /api/portfolio/topup` — 批量加仓<br>`POST /api/portfolio/migrate` — V3→V4 迁移 |
| **设计意图** | 用户选好配置方案后一键加仓，系统自动按比例拆分为多笔 BUY 交易。获取实时净值计算份额，生成完整交易记录。 |

---

## 四、股票持仓盯盘

### 4.1 股票持仓 CRUD + 实时行情
| 项目 | 内容 |
|------|------|
| **后端文件** | `services/stock_monitor.py`, `services/stock_data_provider.py` |
| **API** | `GET/POST/PUT/DELETE /api/stock-holdings` — 持仓 CRUD<br>`GET /api/stock-holdings/realtime/{code}` — 实时行情<br>`GET /api/stock-holdings/scan` — 全持仓扫描 |
| **前端入口** | 持仓 Tab → 股票子 Tab |
| **设计意图** | 多源行情数据自动降级：东方财富(全字段) → 新浪基础行情+雪球补充(PE/PB/换手率/市值)。解决东方财富频繁反爬的问题。每只股票实时显示价格、涨跌幅、成本盈亏、技术指标。 |

### 4.2 技术指标 + 异动检测
| 项目 | 内容 |
|------|------|
| **后端文件** | `services/stock_monitor.py` (3-5节) |
| **技术指标** | RSI(14) / MACD / 均线(MA5/MA10/MA20/MA60) / 量比 / 换手率 |
| **异动检测** | 涨跌停 / 放量(量比>3) / RSI超买超卖 / 连涨连跌 / 大幅偏离均线 |
| **设计意图** | 散户最缺的是"什么时候该注意"。异动检测自动扫描所有持仓，发现异常立即标红预警，不用盯盘也能第一时间知道。 |

### 4.3 股票纪律检查
| 配置项 | 值 | 说明 |
|--------|-----|------|
| 单只最大仓位 | 20% | 超过提醒分散 |
| 最低持仓只数 | 5 | 低于此数警告 |
| 单行业最大占比 | 30% | 防行业集中 |
| 止损线 | -8% | 触发强制提醒 |
| 止盈线 | +20% | 触发分批卖出提醒 |
| 集中度预警 | 30% | 单只占总市值超标 |

### 4.4 DeepSeek 深度分析
| 项目 | 内容 |
|------|------|
| **API** | `POST /api/stock-holdings/analyze` — 7 Skill 框架深度分析全持仓 |
| **设计意图** | 收盘后一键触发，系统自动汇总所有持仓的行情+技术指标+异动信号，喂给 DeepSeek 做全面深度分析。输出格式：总体评估 → 逐只分析 → 风控总结。 |

---

## 五、基金持仓盯盘

### 5.1 基金持仓 CRUD + 实时估值
| 项目 | 内容 |
|------|------|
| **后端文件** | `services/fund_monitor.py` |
| **API** | `GET/POST/PUT/DELETE /api/fund-holdings` — 持仓 CRUD<br>`GET /api/fund-holdings/realtime/{code}` — 实时估值<br>`GET /api/fund-holdings/scan` — 全基金扫描 |
| **前端入口** | 持仓 Tab → 基金子 Tab |
| **设计意图** | 全市场基金估值(5min缓存)，净值历史+回撤+波动率计算，连跌/大跌/回撤超限异动预警。同样支持 DeepSeek 深度分析。 |

---

## 六、统一净资产仪表盘

### 6.1 全资产汇总
| 项目 | 内容 |
|------|------|
| **后端文件** | `services/unified_networth.py`, `services/portfolio_overview.py` |
| **API** | `GET /api/unified-networth` — 合并所有数据源<br>`GET /api/portfolio/overview` — 全资产概览 |
| **设计意图** | 一个数字看全貌：股票持仓市值 + 基金持仓市值 + 手动资产（存款/房产/车/保险） + 记账净现金流 - 负债 = **统一净资产**。2分钟缓存避免频繁计算。首页大字显示，一目了然。 |

---

## 七、AI 对话分析

### 7.1 DeepSeek 10 Skill 多空辩论框架
| 项目 | 内容 |
|------|------|
| **后端文件** | `services/chat.py`, `prompts/system_prompt.md`, `prompts/skills/` |
| **API** | `POST /api/chat` — 普通对话<br>`POST /api/chat/stream` — SSE 流式对话 |
| **前端入口** | AI分析 Tab |
| **设计意图** | 核心创新——不是简单问答，而是内置 **10 个投资分析 Skill**，模拟专业投资委员会的辩论机制。 |

### 10 个 Skill 角色：

| # | 角色 | 职责 |
|---|------|------|
| 1 | 🎩 巴菲特 | 价值投资：ROE/PE/FCF/护城河 |
| 2 | 📚 格雷厄姆 | 安全边际：PE<15/PB<1.5/流动比率 |
| 3 | 🚀 彼得·林奇 | 成长投资：PEG/十倍股/6类公司分类 |
| 4 | 🦢 塔勒布 | 反脆弱：尾部风险/杠铃策略/≥20%现金 |
| 5 | 📊 技术面分析师 | RSI/MACD/布林带技术指标 |
| 6 | 📰 新闻情绪分析师 | 政策/贸易/科技事件→行业影响映射 |
| 7 | 🛡️ 风控经理 | **一票否决权**：仓位/回撤/止盈硬阈值 |
| 8 | 🟢 多头研究员 | 找 2-3 个最强买入理由（数据支撑） |
| 9 | 🔴 空头研究员 | 找 2-3 个最大风险/卖出理由 |
| 10 | ⚖️ 辩论室仲裁 | 评判多空论据强度，给出综合方向+置信度 |

### 7.2 SSE 流式输出
| 项目 | 内容 |
|------|------|
| **设计意图** | 逐字输出回答，支持 DeepSeek R1 的"思考过程"和"正式回答"双阶段展示。无 API Key 时自动降级为规则引擎回答（内置针对入场时机、止盈止损、定投策略、特定资产等的规则模板）。 |

### 7.3 模型支持
- DeepSeek V3 (deepseek-chat) — 默认，快速
- DeepSeek R1 (deepseek-reasoner) — 深度推理，显示思考过程

---

## 八、市场资讯中心（16 个子 Tab）

资讯页是整个系统的情报中枢，包含 16 个子 Tab：

### 8.1 📊 总览
| 内容 | 说明 |
|------|------|
| 恐惧贪婪指数 | 0-100 仪表盘，6 维细分 |
| 估值百分位 | 沪深300 PE-TTM 百分位 + 历史分位 |
| 技术指标 | RSI/MACD/布林带 |
| 新闻摘要 | TOP 5 新闻 + 情绪标签 |
| 事件影响 | 新闻→行业→持仓影响链 |

### 8.2 🔍 选基
| 后端文件 | `services/fund_screen.py`, `services/fund_rank.py` |
| 设计意图 | 全量基金排行多维打分筛选 TOP 推荐，支持按类型/排序/数量筛选。DeepSeek 一句话点评每只基金。 |

### 8.3 🧠 选股（30 因子体系）
| 后端文件 | `services/stock_screen.py`, `services/ml_stock_screen.py` |
| 因子体系 | **7 维 30 因子**：价值(6)/成长(5)/质量(6)/动量(4)/风险(4)/流动性(3)/舆情(2) |
| ML 增强 | LightGBM 多因子选股模型，参考 Qlib Alpha158 |
| 设计意图 | 对标 Zen Ratings 115 因子 + AI Hedge Fund 17 Agent + 幻方量化多因子框架的散户简化版。用 Tushare Pro 获取 PE/PB/财务数据替代不稳定的 AKShare 源。 |

### 8.4 📰 新闻
| 后端文件 | `services/news_data.py` |
| 功能 | 市场新闻 + 情绪标签(利好🟢/利空🔴/中性) + 事件→行业→基金关联分析 |

### 8.5 🏛️ 政策
| 后端文件 | `services/policy_data.py` |
| 功能 | 8 大主题分类（房地产/科技/金融/医药/新能源/消费/基建/外交）+ DeepSeek 政策影响分析 + 房地产结构化数据 + 房价指数 |

### 8.6 📈 技术
| 后端文件 | `services/technical.py` |
| 功能 | RSI/MACD/布林带可视化 + 买卖信号 + 术语解释弹窗（点击即看白话解读） |

### 8.7 📊 宏观
| 后端文件 | `services/macro_data.py`, `services/macro_extended.py`, `services/macro_v8.py` |
| 功能 | CPI/PMI/M2/PPI/GDP/工业增加值/社零/固投/M1/社融/LPR/美林时钟/涨跌家数/龙虎榜 |

### 8.8 🌐 全球
| 后端文件 | `services/global_market.py` |
| 功能 | 美股三大指数 / 外汇(美元人民币) / 美联储利率 / 中美PE对比 / DeepSeek 全球→A股影响分析 |

### 8.9 🔬 因子检验
| 后端文件 | `services/factor_ic.py` |
| API | `GET /api/factor-ic` — IC 检验<br>`GET /api/factor-ic/decay` — IC 衰减曲线 |
| 设计意图 | 验证 30 因子体系中哪些因子**真正具有收益预测能力**（而不是过拟合）。计算 Spearman IC（秩相关系数），按 |IC|>0.03 筛选有效因子。IC 衰减曲线展示因子在不同预测周期（5/10/20/40/60天）下的效果变化。**对标量化基金的标准因子验证流程。** |

### 8.10 🎲 蒙特卡洛模拟
| 后端文件 | `services/monte_carlo.py` |
| API | `GET /api/monte-carlo/{code}` — 单只股票<br>`POST /api/monte-carlo/portfolio` — 组合模拟<br>`GET /api/monte-carlo/compare/{code}` — 纪律 vs 无纪律对比 |
| 设计意图 | 用**概率分布替代单点预测**。基于历史收益分布，5000 次模拟生成盈利概率/最差情景/收益分布。参考 AQR 蒙特卡洛方法论。支持"有纪律(止盈止损) vs 无纪律"对比，量化纪律的价值。支持持仓组合级模拟。 |

### 8.11 🤖 AI 预测引擎（P1 — 对标幻方量化）
| 后端文件 | `services/ai_predictor.py` (~350行) |
| API | `GET /api/ai-predict/{code}` — 单只预测<br>`GET /api/ai-predict/portfolio/{user_id}` — 全持仓预测<br>`POST /api/ai-predict/batch` — 批量预测 |
| 模型 | MLP 神经网络 + GBM（梯度提升）双模型集成 |
| 特征 | ~40 个特征（价格动量/波动率/成交量/技术指标/趋势等） |
| 设计意图 | **从"凭感觉"到"AI 预测"的质变**。训练轻量 ML 模型预测未来 5 天涨跌概率。双模型投票增强鲁棒性。不追求精确点位预测，而是给出"上涨概率 65%"这样的概率性判断。 |

### 8.12 🧬 遗传编程因子挖掘（P2 — 对标幻方量化）
| 后端文件 | `services/genetic_factor.py` (~370行) |
| API | `GET /api/genetic-factor/{code}` |
| 算法 | 表达式树 + 遗传进化（200 个体 × 30 代） |
| 设计意图 | **自动发现人类想不到的 Alpha 因子**。用遗传编程构造因子表达式（如 `rank(close/lag(close,5)) * sqrt(volume)`），通过 Spearman IC 评估适应度，进化保留 IC 最高的因子。对标幻方量化的因子挖掘流水线。 |

### 8.13 ⚡ 组合优化器（P3 — 对标幻方量化）
| 后端文件 | `services/portfolio_optimizer.py` (~320行) |
| 5 种优化方法 | 最大夏普比率(MVO) / 最小方差(MinVol) / CVaR优化 / 层次风险平价(HRP) / 等权 |
| 设计意图 | **从"凭感觉分仓"到数学最优组合**。基于历史收益率矩阵，用 5 种方法求解最优权重，自动推荐最佳方法。CVaR 方法参考幻方量化的风控框架。输出各方法对比 + 调仓建议。 |

### 8.14 📡 另类数据仪表盘（P4 — 对标幻方量化）
| 后端文件 | `services/alt_data.py` (~380行) |
| 6 大数据源 | 北向资金 / 融资融券 / 龙虎榜 / 大宗交易 / 股东增减持 / 行业ETF资金流 |
| API | `GET /api/alt-data/dashboard` — 综合仪表盘<br>`GET /api/alt-data/{source}` — 单源数据 |
| 设计意图 | 幻方用卫星图/物流GPS等另类数据，我们用**免费的高价值信号**替代：北向资金是"聪明钱"风向标，融资融券反映杠杆情绪，龙虎榜揭示游资/机构动向。这些数据散户完全可以免费获取，但很少有人系统性整合。 |

### 8.15 🎮 强化学习仓位管理（P5 — 对标幻方量化）
| 后端文件 | `services/rl_position.py` (~340行) |
| API | `GET /api/rl-position/{code}` — 单只建议<br>`GET /api/rl-position/portfolio/{user_id}` — 全持仓建议 |
| 算法 | Q-Learning Agent，离散状态空间（仓位×收益×波动×RSI×趋势） |
| 设计意图 | **让 AI 学会"什么情况下该加仓/减仓/观望"**。在历史数据上训练 5 轮，学习不同市场状态下的最优仓位动作。输出：当前状态 → 推荐动作（大幅加仓/小幅加仓/持有/小幅减仓/大幅减仓）+ 各动作的 Q 值对比。 |

### 8.16 🧠 LLM 因子生成器（P6 — 对标幻方量化 Alpha-GPT）
| 后端文件 | `services/llm_factor_gen.py` (~280行) |
| API | `GET /api/llm-factor/{code}` |
| 流程 | DeepSeek 构思因子 → 生成 Python 代码 → 安全执行 → IC 验证 → 迭代优化 |
| 设计意图 | **DeepSeek 驱动的 Alpha-GPT 平替**。让 LLM 像量化研究员一样自动构思、编码、验证交易因子。第一轮生成 5 个因子 → IC 验证 → 把验证结果反馈给 LLM → 第二轮改进。实现"AI 设计因子 → AI 验证因子"的闭环。 |

---

## 九、风控系统

### 9.1 多层风控体系
| 后端文件 | `services/risk.py`, `config.py` |
| API | `POST /api/risk-metrics` — 风控指标<br>`POST /api/risk-actions` — 风控执行建议 |

### 9.2 风控硬阈值（借鉴幻方量化）
| 阈值 | 值 | 动作 |
|------|-----|------|
| 回撤预警线 | -15% | 降仓至 50% |
| 回撤警戒线 | -18% | 降仓至 40% + 增配债券 |
| 单日跌幅限制 | -4% | 暂停开新仓 |
| 最大允许回撤 | -20% | **绝不突破** |
| 单票最大占比 | 3% | 超过提醒分散 |
| 单基金最大占比 | 15% | 超过提醒分散 |
| 单行业最大占比 | 20% | 防行业集中 |
| 止盈阈值 | +40% | 收益≥40% 减半 |
| 再平衡触发 | ±8% | 偏离度超标触发 |

### 9.3 风控设计意图
> 散户亏钱的核心原因是**没有纪律**。这套风控体系借鉴幻方量化的 CVaR 模型简化版，用硬阈值强制执行——不管你主观判断如何，触线就执行。风控经理在 AI 对话中拥有**一票否决权**。

---

## 十、12维多因子信号引擎

### 10.1 综合交易信号
| 后端文件 | `services/signal.py` |
| API | `GET /api/daily-signal` — 每日综合信号<br>`GET /api/daily-signal/interpret` — DeepSeek 人话解读 |
| 5 大维度 | 技术面(25%) / 基本面(30%) / 资金面(20%) / 情绪面(15%) / 宏观面(10%) |
| 设计意图 | 融合 12 个因子生成综合买卖信号（-100 ~ +100），配合大师策略（巴菲特/格雷厄姆/林奇/塔勒布）给出投资建议。DeepSeek 把数字信号翻译成人话，普通人也能看懂。 |

### 10.2 智能定投策略
| API | `POST /api/smart-dca` |
| 设计意图 | 根据估值百分位动态调整定投倍率：极度低估 2x → 低估 1.5x → 偏低 1.2x → 适中 1x → 偏高 0.5x → 高估 0.3x → 极度高估暂停。 |

---

## 十一、回测引擎

### 11.1 策略回测
| 后端文件 | `services/backtest.py`, `services/backtest_engine.py` |
| API | `GET /api/backtest` — 智能定投 vs 固定定投回测<br>`GET /api/backtest/{code}` — 单只标的回测<br>`POST /api/backtest/portfolio` — 组合回测 |
| 指标 | 年化收益 / 最大回撤 / 夏普比率 / Sortino / Calmar / IR(信息比率) |
| 设计意图 | "不回测就投资等于蒙着眼开车"。支持 1-3 年历史回测，对比智能定投 vs 固定定投的差异，用数据证明策略有效性。 |

---

## 十二、全资产管理

### 12.1 手动资产管理
| API | `POST /api/assets` — 添加/更新资产<br>`DELETE /api/assets/{id}` — 删除<br>`GET /api/assets` — 获取全部 |
| 资产类型 | 现金 / 房产 / 车辆 / 保险 / 负债 / 其他 |

### 12.2 记账系统
| API | `POST /api/ledger/add` — 添加记账<br>`GET /api/ledger/{user_id}` — 流水<br>`GET /api/ledger/{user_id}/summary` — 月度汇总 |
| 支持 | 支出(餐饮/交通/购物等) + 收入(工资/兼职/理财收益等) + OCR 小票识别 |

### 12.3 收入源管理
| API | `POST /api/income-sources/add` — 添加收入源<br>`POST /api/income-sources/record` — 登记收入 |
| 设计意图 | 追踪民宿/出租房/外包等多收入源，每月登记到账金额，自动关联到记账系统。 |

### 12.4 OCR 拍照记账
| API | `POST /api/receipt/ocr` |
| 设计意图 | 拍照识别小票/银行截图/基金买入截图。自动提取金额、商品、日期，智能分类为消费记账 or 交易记录 or 资产更新。 |

### 12.5 DeepSeek 资产诊断
| API | `POST /api/ds/asset-diagnosis` |
| 设计意图 | AI 全量分析用户资产结构（存款比例、投资比例、负债率等），给出个性化优化建议。 |

---

## 十三、企业微信集成

### 13.1 消息推送 & 回调
| 后端文件 | `routers/wxwork.py`, `services/wxwork_push.py` |
| API | `GET/POST /api/wxwork/callback` — 回调验证+消息接收<br>`GET /api/wxwork/status` — 配置状态<br>`POST /api/wxwork/test` — 测试推送<br>`POST /api/wxwork/daily-report` — 市场日报推送 |
| 企业信息 | 企业=钱袋子工作室 / CorpID=wwa766e2ffc88ea428 / AgentID=1000002 |

### 13.2 快捷指令（企微聊天直达）
| 指令 | 功能 |
|------|------|
| `持仓` | 查看持仓列表 |
| `扫描` | 立即触发盯盘扫描 |
| `帮助` | 显示帮助信息 |
| `模型 deepseek-reasoner` | 切换 AI 模型 |
| 任意问题 | DeepSeek AI 分析回复 |

### 13.3 设计意图
> 不用打开网页也能盯盘。企微消息 → 后端接收 → 调 DeepSeek 分析 → 异步回复。Cron 定时扫描异动自动推送到微信。"思考中"先发一条，防止用户以为没收到。

---

## 十四、Agent 决策引擎

### 14.1 Plan-and-Execute 模式
| 后端文件 | `services/agent_engine.py` |
| API | `POST /api/agent/analyze` — 触发分析周期<br>`GET /api/agent/signals/{user_id}` — 获取信号 |
| 设计意图 | 不依赖 LLM 做数据收集（Python 计算），只在最后总结时调 DeepSeek。场景→Skill 自动映射，注入对应的分析 Prompt。 |

### 14.2 Agent 记忆系统
| 后端文件 | `services/agent_memory.py` |
| API | `GET /api/agent/memory/{user_id}` — 记忆摘要<br>`POST /api/agent/preferences` — 保存偏好<br>`POST /api/agent/rules` — 自定义规则 |
| 5 层记忆 | 用户偏好(长期) / 决策日志(自动) / 自定义规则 / 上下文接力 / 记忆摘要注入 |
| 设计意图 | AI 分析师应该"记住"用户——知道你偏好价值投资、不碰白酒、关注新能源。每次分析自动注入记忆上下文，实现个性化分析。 |

---

## 十五、DeepSeek 智能增强层

### 15.1 8 大增强模块
| 后端文件 | `services/ds_enhance.py` |

| # | 模块 | API | 说明 |
|---|------|-----|------|
| 1 | 闲置资金建议 | `POST /api/assets/advice` | 分析存款金额，建议活期/货基/理财配置 |
| 2 | 选基/选股点评 | 内部调用 | 给 TOP 推荐列表加一句话 AI 点评 |
| 3 | 今日关注 | `GET /api/daily-focus` | 首页个性化"今日关注"卡片 |
| 4 | 新闻风控联动 | `GET /api/news/risk-assess` | 评估新闻对持仓的风控影响 |
| 5 | 信号人话解读 | `GET /api/daily-signal/interpret` | 把 12 维数字信号翻译成人话 |
| 6 | 新闻深度影响 | `GET /api/news/deep-impact` | DeepSeek 分析事件→行业→持仓影响链 |
| 7 | 配置建议增强 | 内部调用 | 在基础建议上叠加新闻/行业维度 |
| 8 | 资产诊断 | `POST /api/ds/asset-diagnosis` | 全量分析用户资产结构 |

---

## 十六、数据层

### 16.1 数据源架构（多源分层降级）
| 优先级 | 数据源 | 用途 | 备注 |
|--------|--------|------|------|
| 🥇 主力 | AKShare | 行情/宏观/新闻/基金/因子 | 免费，覆盖广 |
| 🥈 增强 | Tushare Pro | PE/PB/财务/估值 | ¥200/年，更稳定 |
| 🥉 降级 | 新浪+雪球 | 股票行情降级源 | 东方财富反爬时启用 |
| ⚙️ 计算 | DeepSeek | AI 分析/点评/预测 | `deepseek-chat` 主力 |

### 16.2 核心 Service 文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `stock_screen.py` | ~450 | 30 因子选股（V2，7 维权重） |
| `stock_monitor.py` | ~480 | 股票持仓 CRUD + 实时行情 + 技术指标 + 异动检测 |
| `ai_predictor.py` | ~350 | MLP+GBM 双模型 AI 预测引擎 |
| `genetic_factor.py` | ~370 | 遗传编程因子挖掘 |
| `alt_data.py` | ~380 | 6 大另类数据源 |
| `rl_position.py` | ~340 | Q-Learning 仓位管理 |
| `portfolio_optimizer.py` | ~320 | 5 种组合优化方法 |
| `llm_factor_gen.py` | ~280 | LLM Alpha 因子生成器 |
| `monte_carlo.py` | ~450 | 蒙特卡洛模拟 |
| `factor_ic.py` | ~400 | 因子 IC 检验 + 衰减曲线 |
| `ds_enhance.py` | ~470 | DeepSeek 8 大智能增强模块 |
| `global_market.py` | ~450 | 全球市场数据（美股/外汇/PE） |
| `wxwork_push.py` | ~260 | 企微推送 + 回调加解密 |
| `agent_engine.py` | ~220 | Agent 决策引擎 |
| `agent_memory.py` | ~270 | Agent 5 层记忆系统 |
| `fund_monitor.py` | ~370 | 基金持仓 CRUD + 估值 + 异动 |
| `news_data.py` | ~370 | 新闻 + 政策 + 影响分析 |
| `policy_data.py` | ~300 | 政策结构化数据 + DeepSeek 分析 |
| `signal.py` | ~580 | 12 维多因子信号 + 大师策略 |
| `factor_data.py` | ~550 | 因子数据层（北向/融资/国债/SHIBOR） |
| `backtest_engine.py` | ~280 | 回测引擎 |
| `chat.py` | ~440 | AI 对话（规则引擎+LLM） |
| `risk.py` | ~270 | 风控系统（HHI/回撤/硬阈值） |
| `portfolio.py` | ~290 | 智能配置引擎 |
| `macro_v8.py` | ~300 | 扩展宏观（GDP/社零/龙虎榜） |
| `tushare_data.py` | ~150 | Tushare Pro 数据层 |
| `stock_data_provider.py` | ~300 | A 股多源行情（东方财富→新浪+雪球降级） |

---

## 十七、基础设施

### 17.1 数据健康检查
| API | `GET /api/health/data-audit` |
| 检查项 | 宏观数据新鲜度 / 估值数据合理性 / 基金净值新鲜度 / 新闻相关性 / API 响应时间 |
| 前端 | 资讯页「🔍 数据体检」按钮 |

### 17.2 缓存策略
| 数据 | TTL | 说明 |
|------|-----|------|
| 基金净值 | 1h | 每日更新 |
| 新闻 | 30min | 半小时刷新 |
| 宏观 | 2h | 月度数据 |
| 选股 | 2h | 计算密集 |
| 因子 | 1h | 实时性要求中等 |
| 市场上下文 | 5min | AI 对话用 |
| 净资产 | 2min | 首页展示 |

### 17.3 PWA 支持
- Service Worker 离线缓存
- 添加到桌面（`manifest.json`）
- 移动端优先设计（max-width: 480px）
- 暗色主题（深蓝色系 `#0F172A`）

### 17.4 静态文件缓存
| 类型 | 策略 |
|------|------|
| JS/CSS | 5min + 后台刷新(stale-while-revalidate) |
| 图片 | 7 天 |
| HTML | no-cache（每次验证） |
| JSON | 1 分钟 |

---

## 十八、盯盘 Cron

### 18.1 自动盯盘脚本
| 文件 | `scripts/stock_monitor_cron.py` |
| 功能 | 工作日定时扫描全持仓 → 检测异动 → 企微推送预警 |
| 设计意图 | 用户不需要盯盘，系统自动在交易时段扫描，发现异常（涨跌停/放量/RSI超买超卖等）立即推送到微信。 |

---

## 📊 项目统计

| 维度 | 数量 |
|------|------|
| API 端点总数 | **142+**（main.py 85+ / routers 11+） |
| Service 文件 | **26 个** |
| Router 文件 | **2 个**（profiles + wxwork） |
| Prompt 文件 | **8 个**（1 system + 7 skills） |
| 后端总代码 | **~15,000 行** |
| 前端 JS | **~2,270 行** |
| 前端 CSS | **142 行** |
| AI 分析 Skill | **10 个** |
| 选股因子 | **30 个（7 维）** |
| 数据维度 | **45+** |
| 量化引擎 | **6 个**（AI预测/遗传因子/组合优化/另类数据/RL仓位/LLM因子） |

---

> 💡 **设计哲学**: 用 AI + 量化的武器武装普通散户，把幻方量化 ¥10 亿才能做到的事情，用 ¥200/年（Tushare）+ 免费数据源 + DeepSeek API 做到 45-55% 的水平。不追求极致性能，追求**信息优势**和**纪律执行**。
