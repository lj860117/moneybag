# MoneyBag 全量体检 Bug 修复报告

> **修复日期**：2026-04-19 11:58 → 12:35（约 40 分钟）
> **关联体检报告**：`HEALTH-CHECK-2026-04-19.md`
> **修复范围**：P0 × 6 + P1 × 8 + 小白可用性改进 × 4

---

## 🎯 整体成果

| 修前 | 修后 |
|------|------|
| AI 非交易日编造 RSI/PE/MACD 等数据（幻觉） | AI 明确标注"数据缺失"，拒绝编造，基于真实盈亏给建议 |
| 持仓通过 V4 transactions 建仓在 overview 看不到 | 两套存储双向可见（桥接层） |
| Sortino 比率算错 100 倍 | 正确使用年化 1.8%/12 ≈ 0.0015 |
| 回撤用"成本×1.1"假设历史高点 | 用真实持仓市值/成本最大值近似 |
| 资产类型判断只认 5 个基金代码 | 按名称关键字智能分类 |
| Steward 16 模块静默失败 4 个看不见 | modules_status/errors/missing 清晰透出 |
| 小白看不懂 PE/RSI/夏普等术语 | 新增 `/api/glossary` 提供 35+ 术语解释 |
| 不知道数据是不是最新 | 新增 `/api/market-status` + is_snapshot 字段 |

---

## 🔴 P0 修复（6 个）

### F1: Sortino 比率计算 BUG
**位置**：`backend/services/backtest.py:184`
```diff
- risk_free_monthly = 0.15  # 假设年化1.8%/12  ← 错误！15%/月=180%/年
+ risk_free_monthly = 0.0015  # 年化 1.8% ÷ 12
```

### F2: 非交易日 AI 幻觉（最关键修复）
**三处改动**：

1. `backend/services/stock_monitor.py` — 新增 `_fallback_hist_close()` 双源降级
   - 源 1：东方财富 `stock_zh_a_hist`
   - 源 2：新浪 `stock_zh_a_daily`（降级备用）
   - 返回带 `is_snapshot=True` 和 `data_date` 标记

2. `backend/main.py` — `/api/stock-holdings/analyze` 注入数据诚信铁律
   ```python
   system_prompt += """
   🔴 数据诚信铁律（必须遵守）：
   1. 若持仓数据中字段为 N/A 或 null，绝对禁止编造具体数值。
   2. 若标注为『非交易日数据』，必须在分析中明确说明基于哪一天的数据。
   3. 禁止引用『PE约XX倍』『RSI=XX』等具体数字，除非原始数据中明确给出且不为 null。
   4. 分析深度与数据完整度成正比——数据缺失越多，分析就该越保守、越短。
   """
   ```

3. `scan_all_holdings` 透传 `is_snapshot` 和 `data_date` 字段

**效果对比**：

| 修前 | 修后 |
|------|------|
| "茅台 PE 约 28 倍，RSI 偏高，估值百分位>80%" | "⚠️ PE/PB/RSI 数据缺失，本次无法评估估值，等开盘后数据更新再看" |

### F3: 持仓双轨分裂修复
**新建**：`backend/services/holdings_bridge.py`
- `unified_load_stock_holdings(uid)` — 优先独立文件，空则回退 V4 transactions
- `unified_load_fund_holdings(uid)` — 同上
- 代码区分 A 股股票（600/000/300/688 开头）vs 基金

**改动**：`portfolio_overview.py` 改用桥接层
```diff
- from services.stock_monitor import load_stock_holdings
+ from services.holdings_bridge import unified_load_stock_holdings
```

### F4: 选股权重统一到 config.py
**位置**：`backend/services/stock_screen.py:38-46` + `backend/config.py:53-61`
- 统一为 `quality=0.18`（保留 stock_screen 原有智慧）
- stock_screen 改为 `from config import STOCK_SCREEN_WEIGHTS as DEFAULT_DIM_WEIGHTS`

### F5: 回撤计算用真实峰值
**位置**：`backend/services/risk.py:82`
```diff
- peak = total_cost * 1.1  # 简化：假设历史高点
+ peak = _calc_real_peak(active)  # 用 max(成本, 当前市值) 之和近似
```

### F6: 资产类型判断重构
**位置**：`backend/services/risk.py:104-120`

**修前**：
```python
stock_count = sum(1 for h in active if h["code"] in ["110020", "050025", "008114"])
bond_count  = sum(1 for h in active if h["code"] in ["217022"])
```

**修后**：
```python
_KNOWN_FUND_TYPES = {"510300": "equity", "519736": "bond", "000216": "gold", ...}
_EQUITY_KEYWORDS = ["股票", "沪深", "创业", "ETF", "300", "500", ...]
_BOND_KEYWORDS   = ["债券", "债A", "纯债", "可转债", ...]
# 加 A 股代码规则 → 按名称关键字智能分类
```

---

## 🟡 P1 修复（8 个）

### F7/F8: requirements.txt 补全
```diff
+ tushare>=1.4.0
+ pycryptodome>=3.20.0
+ scikit-learn>=1.3.0
+ numpy>=1.24.0
+ pandas>=2.0.0
+ scipy>=1.10.0
```

### F9: rl_position 训练崩溃
**根因**：`_get_stock_hist` 返回 `list[dict]`（每个元素 `{"date":..., "close":...}`），但代码直接 `np.array(prices, dtype=float64)` → TypeError
```diff
+ if isinstance(prices_raw[0], dict):
+     prices = [float(p.get("close", 0) or 0) for p in prices_raw if p.get("close") is not None]
```

### F10/F11: GLOBAL_PE 数据异常
中美 PE 相同时切换到本地沪深 300 数据（`get_valuation_percentile`）

### F12: 版本号双处不一致
```diff
- app = FastAPI(title="钱袋子 API", version="6.0.0-phase0")
+ from config import APP_VERSION as _APP_VERSION
+ app = FastAPI(title="钱袋子 API", version=_APP_VERSION)
```

### F13: NAV 日志噪音
股票代码（600/000/300/688 开头）直接跳过 `get_fund_nav`，避免 akshare 报 SyntaxError

### F14: Pipeline 错误透出
**修改**：`backend/services/decision_context.py` 的 `to_user_response()`
```python
return {
    ...
    "modules_status": {"called": 22, "succeeded": 12, "failed": 0, "skipped": 2},  # 新
    "modules_errors": {"module_name": "error_msg"},  # 新
    "modules_skipped": ["xxx:no_user_id"],  # 新
    "modules_missing": ["business_exposure", "earnings_forecast", ...],  # 新
}
```

### F11（补）: TREASURY 报错
`factor_data.py` 补 import：`from services.market_data import get_valuation_percentile`

---

## 🟢 小白可用性改进（4 项）

### 新增 `/api/market-status`
```json
{
  "is_trading_day": false,
  "session": "closed",
  "weekday": "Sunday",
  "message": "📅 非交易日，数据为最近一次收盘快照"
}
```

### 新增 `/api/glossary?term=XX`
35+ 金融术语词典，每个术语包含：
- `name`：官方名称
- `short`：一句话定义
- `plain`：大白话解释
- `example`：举例（可选）

示例（PE）：
```json
{
  "name": "市盈率 (PE)",
  "short": "股价 ÷ 每股盈利",
  "plain": "你花多少年赚回股票钱。PE=20 就是 20 年回本。越低越便宜，但也可能是公司不行。",
  "example": "茅台 PE=28，意思是按现在的盈利，28年回本"
}
```

覆盖分类：
- **估值**：PE / PE-TTM / PB / PEG / ROE / DCF / 估值百分位
- **技术面**：RSI / MACD / 布林带 / 量比
- **风险指标**：夏普 / Sortino / 最大回撤 / HHI / CVaR / Beta
- **资金面**：北向资金 / 两融 / SHIBOR / 恐贪指数
- **宏观**：股债性价比 / 美林时钟
- **组合管理**：定投 / 止盈止损 / 再平衡 / 配置偏离度
- **Pipeline 术语**：置信度 / 分歧度 / EV / Kelly

### AI Prompt 结构化（小白友好）
```
📊 总体结论（一句话，明确方向和置信度）
🟢 多头观点 + 🔴 空头观点（各2-3条，用数据说话）
🛡️ 操作建议（按持仓每只给 1-2 句，避免长篇）
📌 数据说明（本次使用什么数据、有哪些缺失）
```
每节 ≤ 200 字，避免长篇大论。

### 数据新鲜度透出
所有 scan 响应透出：
- `is_snapshot`：是否为快照（非实时）
- `data_date`：数据截止日期
- `data_quality`：一句话综合描述（供前端顶部显示）

---

## 📦 新增 / 修改文件清单

| 类型 | 路径 | 说明 |
|------|------|------|
| 🆕 新建 | `backend/services/holdings_bridge.py` | F3 持仓统一桥接 |
| 🆕 新建 | `backend/services/glossary.py` | D4 金融术语词典 |
| ✏️ 修改 | `backend/services/backtest.py` | F1 Sortino 修正 |
| ✏️ 修改 | `backend/services/stock_monitor.py` | F2 非交易日降级 + 透传元数据 |
| ✏️ 修改 | `backend/services/risk.py` | F5 真实峰值 + F6 资产分类 |
| ✏️ 修改 | `backend/services/stock_screen.py` | F4 权重统一 |
| ✏️ 修改 | `backend/services/rl_position.py` | F9 dict list 处理 |
| ✏️ 修改 | `backend/services/factor_data.py` | F11 TREASURY import |
| ✏️ 修改 | `backend/services/market_data.py` | F13 股票代码识别 |
| ✏️ 修改 | `backend/services/global_market.py` | F11 GLOBAL_PE 降级 |
| ✏️ 修改 | `backend/services/decision_context.py` | F14 modules_status 透出 |
| ✏️ 修改 | `backend/services/pipeline_runner.py` | F14 错误收集 |
| ✏️ 修改 | `backend/services/portfolio_overview.py` | F3 桥接层对接 |
| ✏️ 修改 | `backend/config.py` | F4 权重同步 |
| ✏️ 修改 | `backend/main.py` | F12 版本号 + AI prompt 加固 + 新 API |
| ✏️ 修改 | `backend/requirements.txt` | F7/F8 依赖补全 |

---

## ✅ 回归测试结果

| # | 接口 | 修前 | 修后 |
|---|------|------|------|
| 1 | `/api/health` | version="6.0.0-phase0" | version="7.1.0" ✅ |
| 2 | `/api/market-status` | 不存在 | 正确返回非交易日状态 ✅ |
| 3 | `/api/glossary?term=PE` | 不存在 | 正确返回 PE 解释 ✅ |
| 4 | `/api/portfolio/overview` | 走 transactions 看不到 | 3 股 2 基金全显示 ✅ |
| 5 | `/api/stock-holdings/analyze` | AI 编造 PE/RSI | AI 明确标注"数据缺失" ✅ |
| 6 | `/api/steward/ask` | 4 模块静默消失 | 清晰列出 `modules_missing` ✅ |
| 7 | `/api/stock-holdings/realtime/600519` | price=null | price=¥1407.24 (快照) ✅ |

---

## 🚧 已知遗留（非代码 Bug）

1. **东方财富反爬频繁**：这是外部数据源问题，已通过双源降级（新浪）缓解
2. **非交易日 ai-predict 历史数据不足**：需要 200+ 天历史数据，非交易日 akshare 偶尔拿不全——不是 Bug，是数据源限制
3. **部分中文术语查询需 URL encode**：`/api/glossary?term=夏普比率` 需要客户端 encode，英文术语（PE/RSI/HHI）直接可用

---

## 💡 下次可做（V8 规划）

- **硬编码集中治理**：60+ 处散落硬编码（DCF 折现率 10%、蒙特卡洛止盈止损、各类评分阈值）迁移到 config.py
- **测试覆盖**：risk.py / backtest.py / pipeline_runner.py 核心模块添加单元测试
- **前端 tooltip 接入**：利用新的 glossary API，给每个专业术语加鼠标悬停解释
- **交易日检测全局化**：所有数据接口开头判断非交易日 → 直接走缓存，减少 30 秒级等待

---

_修复完成时间：2026-04-19 12:35_
_本次修复产生代码提交建议：标题 `fix(moneybag): P0/P1 全量体检修复 + 小白可用性改进`_
