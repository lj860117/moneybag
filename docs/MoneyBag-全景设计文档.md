# MoneyBag 全景设计文档（合并版）

> 📅 最后更新：2026-04-16 02:24  
> 📝 包含：Phase 0 + V6 + V6.5 + V7 + V8 + V9 全部设计  
> 👥 用户：厉害了哥（LeiJiang）、部落格里（BuLuoGeLi）

---

## 📌 文档导航

本项目共有 **3 份设计文档**，按开发顺序排列：

```
开发路线图：

Phase 0（3天）→ V6（8-12天）→ V6.5（9-10天）→ V7（4-7h）→ V8（9天）→ V9（12天）
    ↓                ↓                ↓               ↓            ↓           ↓
  Part 1           Part 2           Part 3          Part 4       Part 5      Part 6
```

| Part | 内容 | 工时 |
|------|------|------|
| **Part 1** | V6 Phase 0：前端整合 | 3 天 |
| **Part 2** | V6：7 大模块升级（地缘/原油/北向/行业/研报/情景/历史） | 8-12 天 |
| **Part 3** | V6.5：盈利预测 + 估值定价 + 业务敞口 | 9-10 天 |
| **Part 4** | V7：推荐引擎 + DCF 估值 + 买卖决策 | 4-7h |
| **Part 5** | V8：AI 自主复盘 + 研报追踪（验证→归因→策略调整） | 9+6 天 |
| **Part 6** | V9：AI 模拟交易 + 自主学习（练功房） | 10-12 天 |

---

## 📅 统一实施总表（全链路）

> 一张表看从 Phase 0 到 V8 的完整顺序，含提前任务和跨版本验证

### Phase 0（3 天 / ~24h）

#### Day 1：基础设施 + 框架搭建 + BUG 修复（~9.5h）

| # | 任务 | 时间 | 改动文件 | 自测 |
|---|------|------|----------|------|
| 1.1 | 数据源巡检脚本 ⬆️ | 0.5h | 新建 `scripts/datasource_health_check.py` | `python scripts/datasource_health_check.py` |
| 1.2 | 前后端覆盖率检查 ⬆️ | 0.5h | 新建 `scripts/check_coverage.py` | `python scripts/check_coverage.py` |
| 1.3 | 用户选择器（登录态）🆕 | 0.5h | `app.js` | 清 localStorage 后刷新，弹选人 |
| 1.4 | R1 日限 50→100 🆕 | 5min | `config.py` | 检查 DAILY_LIMIT |
| 1.5 | 缓存持久化 + 重启恢复 🆕 | 0.5h | `main.py` + `data/cache/` | `systemctl restart → curl /api/health` |
| 1.6 | JSON 文件锁（并发保护）🆕 | 0.5h | `persistence.py` | 并发 curl 测试 |
| 1.7 | 操作日志 🆕 | 0.5h | 新建 `services/audit_log.py` | `cat data/audit/$(date +%F).jsonl` |
| 1.8 | 版本号对齐 🆕 | 0.5h | `config.py` + `main.py` + `app.js` | `curl /api/health` 返回 version |
| 1.9 | 用户偏好 API | 1h | `main.py` + `persistence.py` | `curl /api/user/preference?userId=LeiJiang` |
| 1.10 | Simple/Pro 模式切换 | 1h | `app.js` + `styles.css` | 切换后刷新保持 |
| 1.11 | 首页改造（含空仓推荐版）🆕 | 2h | `app.js` | 有仓→持仓视图，空仓→市场概览 |
| 1.12 | 修复 3 个记忆 BUG | 1h | `agent_engine.py` / `main.py` | 连续两轮对话测试 |
| 1.13 | 删除死代码 + 三态封装 | 0.5h | `app.js` | `grep -rn "chat.py" backend/` |

#### Day 2：P0 API 接入（~6.5h）

| # | 任务 | 时间 | 改动文件 | 自测 |
|---|------|------|----------|------|
| 2.1 | 家庭资产汇总 API + 前端 | 1.5h | `main.py` + `app.js` | `curl /api/household/summary` |
| 2.2 | `/news/deep-impact` 前端接入 | 1.5h | `app.js` | Pro 点击新闻展示分析 |
| 2.3 | `/news/risk-assess` 前端接入 | 1h | `app.js` | 风控评估展示 |
| 2.4 | `/stock-holdings/analyze` 前端接入 | 1.5h | `app.js` | Pro 可触发分析 |
| 2.5 | `/fund-holdings/analyze` 复用 2.4 | 0.5h | `app.js` | 基金分析 |
| 2.6 | Day 2 自测 | 0.5h | - | 健康检查脚本 + 两账号隔离 |

#### Day 3：P1 API + 增强 + 主题（~8h）

| # | 任务 | 时间 | 改动文件 | 自测 |
|---|------|------|----------|------|
| 3.1 | `/daily-signal/interpret` 接入 | 1h | `app.js` | Pro 信号解读 |
| 3.2 | `/timing` + `/smart-dca` 接入 | 1h | `app.js` | Pro 展示建议 |
| 3.3 | 激活 portfolio_optimizer + API | 1h | `main.py` | `curl /api/asset-allocation` |
| 3.4 | 盯盘 API + 前端轮询 | 1h | `main.py` + `app.js` | 空仓返回 idle |
| 3.5 | 推送空仓适配 | 1h | `wxwork_push.py` | 手动触发推送 |
| 3.6 | 聊天意图分类 | 0.5h | `main.py` | R1 问题走 R1 |
| 3.7 | 决策日志格式定义 ⬆️ | 0.5h | `data/decisions/` | 检查 JSON 格式 |
| 3.8 | 暗色模式 CSS 🆕 | 1h | `styles.css` + `app.js` | 切换主题即时生效 |
| 3.9 | 全量联调 | 1h | - | 全部检查脚本通过 |

> ⬆️ = 从后续版本提前    🆕 = 本轮新增基础设施
> 
> **每晚 2-3h 节奏**：Day 1 拆成 3-4 晚，Day 2-3 各 2-3 晚，总计 **4-5 晚** 完成 Phase 0

### V6 之后追加（不急）

| 任务 | 时间 | 说明 |
|------|------|------|
| app.js 懒加载拆分（309KB→150KB 首屏） | 2h | 按 Tab 拆成 modules/*.js |
| PWA 离线增强（Service Worker 改造） | 1h | 网络优先 + 缓存兜底 + 离线提示条 |

### Phase 0 → V6 衔接验证

```bash
# Phase 0 完成后、V6 开始前，运行一次全面检查：
python scripts/check_coverage.py          # 前后端覆盖率
python scripts/datasource_health_check.py  # 数据源健康
python scripts/api_health_check.py         # API 健康
# 以上 3 个全 ✅ 才开始 V6
```

### V6（5 Phase / 8-12 天）

| Phase | 任务 | 时间 | 自测 |
|-------|------|------|------|
| V6-P1 | 地缘政治 + 原油联动 | 1-2 天 | `curl .../api/geopolitical` 返回非空 |
| V6-P2 | 北向资金修复 + ETF 数据 | 1-2 天 | Part 2 验证脚本 Phase 2 |
| V6-P3 | 行业轮动 + 资金流向 | 1-2 天 | Part 2 验证脚本 Phase 3 |
| V6-P4 | 情景分析 + DataSourceRouter | 1-2 天 | 模拟 AKShare 挂掉，验证降级 |
| V6-P5 | DecisionContextBuilder + 分析历史 | 2-3 天 | 构建完整数据包，检查字段完整性 |

### V6 → V6.5 衔接验证

```bash
# V6 完成后运行：
python -c "
from services.decision_context import DecisionContextBuilder
ctx = DecisionContextBuilder().build('LeiJiang')
# 验证：地缘/原油/北向/行业 数据全部存在
assert 'geopolitical' in ctx
assert 'oil' in ctx
assert 'northbound' in ctx
assert 'sector_rotation' in ctx
print('✅ V6 → V6.5 衔接验证通过')
"
```

### V6.5（3 Phase / 9-10 天）

| Phase | 任务 | 时间 | 自测 |
|-------|------|------|------|
| V6.5-P1 | 盈利预测（Tushare report_rc 接入） | 3-4 天 | `curl .../api/earnings/{stock_code}` 返回预测数据 |
| V6.5-P2 | 估值定价（PE/PB/PS 多维估值） | 3-4 天 | `curl .../api/valuation/{stock_code}` 返回估值结果 |
| V6.5-P3 | 业务敞口 + 前端展示 | 2 天 | Pro 模式展示盈利预测图表 |

### V6.5 → V7 衔接验证

```bash
# V6.5 完成后运行：
python -c "
from services.earnings_forecast import EarningsService
from services.valuation import ValuationService

# 验证盈利预测可用
e = EarningsService().get_forecast('600519.SH')
assert e and 'eps_growth' in e
print(f'✅ 茅台 EPS 增速: {e[\"eps_growth\"]}')

# 验证估值可用
v = ValuationService().get_valuation('600519.SH')
assert v and 'forward_pe' in v
print(f'✅ 茅台 Forward PE: {v[\"forward_pe\"]}')

print('✅ V6.5 → V7 衔接验证通过')
"
```

### V7（6 Phase / 4-7h）

| Phase | 任务 | 时间 | 自测 |
|-------|------|------|------|
| V7.1 | 推荐引擎 | 2h | `curl .../api/recommend/stocks?userId=LeiJiang&topN=5` |
| V7.2 | DCF 估值 | 1.5h | `curl .../api/dcf/600519` 返回内在价值 |
| V7.3 | 买卖决策 | 2h | `curl .../api/decisions?userId=LeiJiang` 返回操作列表 |
| V7.4 | 前端展示 | 1h | Simple 3 秒看清买什么卖什么 |
| V7.5 | 推送增强 | 0.5h | 企微收到操作建议 |
| V7.6 | 联调 | 1h | 全链路 |

### V7 → V8 衔接验证

```bash
# V7 上线 1 个月后运行：
python -c "
from pathlib import Path
import json

# 验证决策日志积累够了
log_dir = Path('data/decisions/LeiJiang')
logs = list(log_dir.glob('*.json'))
total_decisions = sum(
    len(json.loads(f.read_text()).get('decisions', []))
    for f in logs
)
print(f'决策日志: {len(logs)} 天, {total_decisions} 条决策')

assert total_decisions >= 200, f'需要 ≥200 条，当前 {total_decisions}'
print('✅ V7 → V8 衔接验证通过，可以开始 V8')
"
```

### V8（5 Phase / 9 天）

| Phase | 任务 | 时间 | 自测 |
|-------|------|------|------|
| V8.1 | 预测验证引擎 | 2 天 | `curl .../api/review/run?userId=LeiJiang` 返回准确率 |
| V8.2 | R1 归因分析 | 1.5 天 | 归因 JSON 含 6 维度分布 |
| V8.3 | 策略自动调整 | 2 天 | A 级自动执行 + B 级推企微 |
| V8.4 | 触发机制 | 1 天 | 周日 02:00 自动触发 |
| V8.5 | 前端 + 推送 | 1.5 天 | Simple 看准确率，Pro 看归因图 |
| V8.6 | 联调 | 1 天 | 全链路 |

### V7/V8 API 预期返回示例

```json
// GET /api/recommend/stocks?userId=LeiJiang&topN=3
{
    "recommendations": [
        {
            "code": "600519", "name": "贵州茅台",
            "total_score": 82.5,
            "dimension_scores": {"valuation": 75, "earnings": 90, "technical": 80, "capital": 85, "risk": 70},
            "reason": "盈利增速 15%，估值合理偏低，北向持续增持",
            "suggested_position": {"action": "建议买入", "position_pct": 0.05}
        }
    ],
    "updated_at": "2026-05-20T08:30:00"
}

// GET /api/dcf/600519
{
    "stock_code": "600519", "current_price": 1680.0,
    "intrinsic_value": 2100.50, "buy_price": 1470.35,
    "upside": 25.0, "verdict": "低估（有安全边际）", "emoji": "🟢"
}

// GET /api/decisions?userId=LeiJiang
{
    "decisions": [
        {"symbol": "510300", "name": "沪深300ETF", "action": "buy", "position_pct": 0.05, "reason": "估值低+盈利稳", "confidence": 0.8}
    ],
    "scenarios": {
        "optimistic": "3个月内 +15%",
        "neutral": "3个月内 +5%",
        "pessimistic": "3个月内 -8%"
    },
    "overall_strategy": "逢低分批建仓，控制总仓位 40% 以内"
}

// GET /api/review/latest?userId=LeiJiang
{
    "stats": {
        "7d": {"total": 20, "correct": 13, "accuracy": 65.0},
        "30d": {"total": 50, "correct": 31, "accuracy": 62.0}
    },
    "attribution": {
        "error_distribution": {"valuation": 30, "earnings": 20, "technical": 15, "capital": 10, "black_swan": 15, "timing": 10},
        "top_lessons": ["估值过度依赖 PE，需结合 PEG", "地缘事件反应滞后"]
    },
    "adjustments": {
        "weight_adjustments": {"valuation": {"old": 0.30, "new": 0.255}},
        "new_rules": ["当地缘风险 > 70 时，新建仓上限降至 3%"]
    }
}
```

---

## 🎯 五大核心问题 × 版本解决矩阵

> 每个版本解决什么问题，一目了然

| 问题 | Phase 0 | V6 | V6.5 | V7 | V8 |
|------|---------|-----|------|-----|-----|
| **1. 前后端不统一** | ✅ 治存量（接 7 个 API） | ✅ 自动覆盖率检查 | - | - | - |
| **2. 硬编码规则限制 AI** | ❌ 不动 | ✅ signal/risk/regime 迁移 AI | ✅ 盈利预测 AI 化 | ✅ 买卖决策 AI 化 | - |
| **3. 数据拉不到/显示错** | ❌ 不动 | ✅ 多源降级 + 校验 + 告警 | - | - | - |
| **4. 模块不互联** | ⚠️ 展示层打通 | ✅ DecisionContextBuilder | ✅ 盈利因子接入 | ✅ 推荐引擎串联 | - |
| **5. AI 未发挥极致** | ⚠️ 意图分类 | ✅ R1 预分析 | ✅ 盈利预测 | ✅ 买卖决策 | ✅ 自主复盘 |

### 防护机制：前后端覆盖率自动检查

> 防止问题 1 再次发生（铁律 #18：后端 API 做了必须验证前端调了）

```python
# scripts/check_coverage.py — 每次提交前运行
import re

def check_frontend_api_coverage():
    """扫描 main.py 所有路由 vs app.js 中的 fetch 调用"""
    # 提取后端路由
    with open('backend/main.py', 'r') as f:
        backend = set(re.findall(r'@app\.\w+\("(/api/[^"]+)"', f.read()))
    
    # 提取前端 fetch 调用
    with open('app.js', 'r') as f:
        frontend = set(re.findall(r'fetch\([`"].*?(/api/[^`"?\s]+)', f.read()))
    
    uncovered = backend - frontend
    if uncovered:
        print(f"⚠️ {len(uncovered)} 个 API 后端有但前端未调用：")
        for api in sorted(uncovered):
            print(f"  - {api}")
        return False
    else:
        print("✅ 所有后端 API 前端均已接入")
        return True

if __name__ == '__main__':
    check_frontend_api_coverage()
```

---

## 🛡️ 数据源保障体系

### 数据源优先级策略：Tushare 优先，AKShare 为辅

| 数据类型 | 主数据源 | 备选 1 | 备选 2（兜底） | AI 分析影响 |
|----------|---------|--------|--------------|------------|
| **股票日线** | Tushare `daily` | AKShare `stock_zh_a_hist` | 最近缓存 | 🟢 低（有缓存） |
| **财务报表** | Tushare `income/balance/cashflow` | AKShare `stock_financial` | 上季度缓存 | 🟢 低 |
| **盈利预测** | Tushare `report_rc` | ❌ 无替代 | R1 用历史趋势推算 | 🟡 中（降级为趋势外推） |
| **北向资金** | Tushare `moneyflow_hsgt` | AKShare `stock_hsgt_hist_em` | 标记"数据暂缺" | 🟡 中 |
| **基金净值** | AKShare `fund_open_fund_info_em` | 天天基金 HTTP 直接爬 | 昨日缓存 | 🟢 低 |
| **基金排行** | AKShare `fund_open_fund_rank_em` | ❌ 无替代 | 用最近缓存（≤7天有效） | 🟡 中 |
| **恐贪指数** | AKShare `index_fear_greed_funddb` | 新浪恐贪指数页面 | 固定中性值 50 + 标记 | 🟡 中 |
| **SHIBOR** | Tushare `shibor` | AKShare `rate_interbank` | 央行官网爬取 | 🟢 低 |
| **新闻** | AKShare `stock_news_em` | 新浪财经 RSS | 跳过新闻维度，权重归零 | 🟡 中 |
| **实时行情** | AKShare `stock_zh_a_spot_em` | 新浪实时接口 `hq.sinajs.cn` | 最近收盘价 | 🟢 低 |
| **宏观（CPI/GDP）** | Tushare `cn_cpi/cn_gdp` | AKShare 对应接口 | 上月数据 | 🟢 低（宏观数据月更） |
| **估值百分位** | AKShare `index_value_hist_funddb` | 手动计算（PE/历史PE） | 标记"估值暂缺" | 🟡 中 |

### AI 分析降级策略

> 🔑 **核心原则：数据缺失不能让 AI 停摆，而是降低该维度权重，明确标记**

```python
# services/data_source_router.py（V6 新建）

class DataSourceRouter:
    """数据源智能路由 + 降级"""
    
    async def get_data(self, data_type: str, **params) -> dict:
        """获取数据，自动降级"""
        chain = FALLBACK_CHAINS.get(data_type, [])
        
        for source in chain:
            try:
                data = await source['func'](**params)
                if self._validate(data, source.get('expect')):
                    return {
                        'data': data,
                        'source': source['name'],
                        'degraded': False,
                    }
            except Exception as e:
                logger.warning(f"数据源 {source['name']} 失败: {e}")
                continue
        
        # 全部失败 → 使用缓存兜底
        cached = self._get_cache(data_type, params)
        if cached:
            return {
                'data': cached['data'],
                'source': f"缓存({cached['age']})",
                'degraded': True,
                'warning': f"{data_type} 使用 {cached['age']} 前的缓存数据",
            }
        
        # 连缓存都没有 → 返回空 + 标记
        return {
            'data': None,
            'source': 'none',
            'degraded': True,
            'warning': f"{data_type} 数据完全不可用",
        }

# AI 分析时的处理：
# 如果某维度数据 degraded=True：
#   1. 该维度权重降为原来的 50%
#   2. 在分析结果中标注"⚠️ xxx 数据使用缓存/不可用"
#   3. 置信度自动下调 10-20%
```

### 每日数据源健康巡检（01:00）

```python
# scripts/datasource_health_check.py
# cron: 0 1 * * * cd /opt/moneybag && venv/bin/python scripts/datasource_health_check.py

HEALTH_CHECKS = [
    # === AKShare（爬虫，不稳定）===
    {'name': '基金净值', 'source': 'akshare', 'func': 'fund_open_fund_info_em',
     'args': {'fund': '110020', 'indicator': '单位净值'}, 'expect': 'rows > 0'},
    {'name': '恐贪指数', 'source': 'akshare', 'func': 'index_fear_greed_funddb',
     'args': {}, 'expect': '0 <= value <= 100'},
    {'name': '实时行情', 'source': 'akshare', 'func': 'stock_zh_a_spot_em',
     'args': {}, 'expect': 'rows > 100'},
    {'name': '基金排行', 'source': 'akshare', 'func': 'fund_open_fund_rank_em',
     'args': {'symbol': 'all'}, 'expect': 'rows > 100'},
    {'name': '估值百分位', 'source': 'akshare', 'func': 'index_value_hist_funddb',
     'args': {'symbol': '沪深300', 'indicator': '市盈率'}, 'expect': 'rows > 0'},
    {'name': '新闻', 'source': 'akshare', 'func': 'stock_news_em',
     'args': {'symbol': '000001'}, 'expect': 'rows > 0'},
    
    # === Tushare（付费 API，稳定）===
    {'name': '股票日线', 'source': 'tushare', 'func': 'daily',
     'args': {'ts_code': '000001.SZ', 'limit': 1}, 'expect': 'rows > 0'},
    {'name': '盈利预测', 'source': 'tushare', 'func': 'report_rc',
     'args': {'ts_code': '600519.SH'}, 'expect': 'rows > 0'},
    {'name': '北向资金', 'source': 'tushare', 'func': 'moneyflow_hsgt',
     'args': {}, 'expect': 'rows > 0'},
    {'name': 'SHIBOR', 'source': 'tushare', 'func': 'shibor',
     'args': {}, 'expect': 'rows > 0'},
]

def run_health_check():
    """运行巡检，推送结果"""
    results = []
    for check in HEALTH_CHECKS:
        try:
            data = call_api(check['source'], check['func'], check['args'])
            ok = validate(data, check['expect'])
            status = '✅' if ok else '⚠️ 数据异常'
        except Exception as e:
            status = f'❌ {str(e)[:50]}'
        
        results.append({
            'name': check['name'],
            'source': check['source'],
            'status': status,
        })
    
    # 统计
    failures = [r for r in results if '❌' in r['status'] or '⚠️' in r['status']]
    
    if failures:
        # 有异常 → 推企微告警
        msg = f"⚠️ 数据源巡检（{len(failures)} 个异常）\n\n"
        for r in failures:
            msg += f"{r['status']} [{r['source']}] {r['name']}\n"
        msg += f"\n✅ 正常：{len(results) - len(failures)} 个"
        msg += f"\n\n降级方案已自动激活，AI 分析不受影响"
        
        from services.wxwork_push import send_text
        send_text(msg)  # ⚠️ 只推给 LeiJiang，不推给 BuLuoGeLi（技术告警不打扰老婆）
    
    # 写日志
    log_file = DATA_DIR / "health" / f"{date.today()}.json"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    
    return results

# cron 配置（加到服务器）：
# 0 1 * * * cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python -c \
#   "from scripts.datasource_health_check import run_health_check; run_health_check()"
```

### 巡检推送示例

```
⚠️ 数据源巡检（2 个异常）

❌ [akshare] 恐贪指数：CNN 页面结构变化
⚠️ [akshare] SHIBOR：返回数据为空

✅ 正常：8 个

降级方案已自动激活，AI 分析不受影响
→ 恐贪指数：使用昨日缓存值 45
→ SHIBOR：使用 Tushare 备选接口
```

### AKShare 完全不可用时的 AI 分析影响

| 场景 | AI 分析怎么做 | 用户感知 |
|------|-------------|---------|
| 基金净值挂了 | 用昨日净值计算盈亏 | "净值数据延迟 1 天" |
| 恐贪指数挂了 | 情绪因子权重归零，其他维度权重补上 | Pro 模式标注"情绪数据暂缺" |
| 新闻挂了 | 跳过新闻分析，直接用技术面+基本面 | "新闻维度暂不可用" |
| 基金排行挂了 | 用 ≤7 天内缓存；超 7 天则不推荐新基金 | "基金推荐基于近期数据" |
| 实时行情挂了 | 用新浪备选；都挂了用最近收盘价 | "行情数据延迟" |
| **AKShare 全部挂了** | **Tushare 扛大梁 + 全部走缓存** | **"部分数据使用缓存，分析置信度降低"** |

> **关键**：AI 分析永远不会因为数据源问题完全停摆。最差情况下用缓存 + Tushare，分析质量下降但不中断。

---

# Part 1：V6 Phase 0 — 前端整合设计（v2.3 终版）

> 📄 原始文件：`V6-Phase0-前端整合设计.md`  
> 🎯 目标：接入已有 API + Simple/Pro 模式 + 修 BUG + 多账号  
> ⏱️ 工时：3 天（每天 7h）

</content>
<parameter name="explanation">创建全景文档的开头和导航部分# MoneyBag V6 Phase 0：前端整合设计

> 📅 创建时间：2026-04-16  
> 📝 版本：v2.3（技术栈校准终版）  
> 🎯 目标：在 V6 功能开发前，先整合前端展示，让后续新功能有"家"可归  
> 👥 用户：厉害了哥（LeiJiang）、部落格里（BuLuoGeLi）

---

## 〇、实际技术栈（已确认）

```
┌─────────────────────────────────────────────────┐
│  后端：FastAPI 0.110+ / Python 3.11 / Pydantic 2│
│  前端：原生 JS SPA (app.js 309KB) + Chart.js 4  │
│  数据：JSON 文件 (data/users/sha256.json)        │
│  缓存：Python dict 内存缓存                      │
│  AI  ：DeepSeek V3 + R1 (llm_gateway.py)        │
│  数据源：AKShare + Tushare (5000积分)             │
│  推送：企业微信 (wxwork_push.py)                  │
│                                                  │
│  部署：                                           │
│  ├── 前端：GitHub Pages (Actions 自动部署)        │
│  ├── 后端：腾讯云 150.158.47.189:8000            │
│  │         Ubuntu 22.04 / systemd / uvicorn ×2   │
│  └── 备用：Railway (美国，回退方案)               │
│                                                  │
│  认证：邀请码制 + SHA256(userId) 路径隔离          │
│  GitHub：https://github.com/lj860117/moneybag    │
│  服务器代码路径：/opt/moneybag/                    │
└─────────────────────────────────────────────────┘
```

### Phase 0 原则：不动底层，只加功能

| 维度 | Phase 0 做法 | 理由 |
|------|-------------|------|
| 前端框架 | **继续原生 JS** | 309KB app.js 已上线可用，迁移 Vue 需 2-3 周 |
| 数据库 | **继续 JSON 文件** | 2 个用户够用，新增字段加 JSON 字段即可 |
| 认证 | **保持 SHA256 隔离** | 有效且已在用 |
| 缓存 | **继续内存 dict** | 重启丢缓存 R1 凌晨会重跑 |
| 推送 | **继续企业微信** | 已有 wxwork_push.py，改造而非重写 |
| 进程管理 | **继续 systemd** | 已配置好 moneybag.service |

---

## 一、问题背景

### 1.1 后端已有但前端未展示的 API

> 以下 API 已在 `backend/main.py` (3262行) 中定义，但 `app.js` 未调用

| # | 后端路由 | 功能 | 优先级 | Phase 0 处理 |
|---|----------|------|--------|--------------|
| 1 | `/news/deep-impact` | DeepSeek 新闻深度分析 | P0 | ✅ Day 2 接入 |
| 2 | `/news/risk-assess` | DeepSeek 新闻风控评估 | P0 | ✅ Day 2 接入 |
| 3 | `/stock-holdings/analyze` | 股票持仓 7-Skill 分析 | P0 | ✅ Day 2 接入 |
| 4 | `/fund-holdings/analyze` | 基金持仓 7-Skill 分析 | P0 | ✅ Day 2 接入 |
| 5 | `/daily-signal/interpret` | DeepSeek 信号解读 | P1 | ✅ Day 3 接入 |
| 6 | `/timing` | 入场时机建议 | P1 | ✅ Day 3 接入 |
| 7 | `/smart-dca` | 智能定投建议 | P1 | ✅ Day 3 接入 |
| 8 | `/rl-position/portfolio/{uid}` | RL 仓位建议 | P2 | ⏸️ V6 阶段 |
| 9 | `/stock/financials/{code}` | 个股财务数据 | P2 | ⏸️ V6 阶段 |
| 10 | `/agent/memory/{uid}` | Agent 记忆查看 | P2 | ⏸️ V6 阶段 |

### 1.2 五个孤岛模块 (`backend/services/`)

| 模块 | 大小 | Phase 0 处理 | 说明 |
|------|------|--------------|------|
| `ai_predictor.py` | 20.6KB | ⏸️ V6 | 需要与 signal.py 整合 |
| `genetic_factor.py` | 16.0KB | ⏸️ V6 | 复杂度高 |
| `rl_position.py` | 16.2KB | ⏸️ V6 | 对应 P2 API |
| `monte_carlo.py` | 18.8KB | ⏸️ V6 | 需要与 risk.py 整合 |
| `portfolio_optimizer.py` | 14.3KB | ✅ **Day 3 激活** | 资产配置建议依赖 |

### 1.3 必修 BUG

| BUG | 位置 | Day | 修复方式 | 验证方法 |
|-----|------|-----|----------|----------|
| `get_memory_summary` 拼错 | `agent_memory.py` 或 `agent_engine.py` 中调用处 | Day 1 | `grep -rn "get_memory_summary" backend/` → 全局替换为 `build_memory_summary` | 企微聊天问"上次分析了什么" |
| `save_context()` 未调用 | `agent_memory.py` 中定义，`main.py` chat 路由未调用 | Day 1 | 在 `/api/chat` 路由的 AI 响应返回后调用 | 连续两轮对话验证上下文串联 |
| `chat.py` 死代码 | `backend/services/` 目录下（如存在） | Day 1 | `grep -rn "import.*chat" backend/` 确认零引用后删除 | grep 结果为空 |

---

## 二、多账号数据隔离

### 2.1 现有用户体系

> 当前认证：`routers/profiles.py` 邀请码制 + `persistence.py` SHA256 路径隔离

```python
# 现有代码 (persistence.py)
def _user_file(user_id: str) -> Path:
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return USERS_DIR / f"{safe_id}.json"
```

### 2.2 Phase 0 用户配置扩展

> 在现有 JSON 用户数据中新增字段，不引入新表

```python
# 在 load_user() 返回的 dict 中新增以下字段（兼容旧数据用 .get() 读取）
USER_DEFAULTS = {
    'display_mode': 'simple',        # 'simple' | 'pro'
    'risk_profile': 'balanced',      # 保守/平衡/成长/激进
    'push_preferences': {
        'morning_brief': True,
        'closing_review': True,
        'risk_alert': True,
        'trade_signal': True,
        'breaking_news': True,
    },
    'watchlist_config': {
        'stop_loss_pct': -0.08,
        'take_profit_pct': 0.20,
        'price_alert_range': 0.05,
    },
}

# 两个用户的个性化配置
USER_OVERRIDES = {
    'LeiJiang': {
        'display_mode': 'pro',
        'risk_profile': 'growth',
        'push_preferences': {
            'morning_brief': True, 'closing_review': True,
            'risk_alert': True, 'trade_signal': True, 'breaking_news': True,
        },
        'watchlist_config': {
            'stop_loss_pct': -0.10, 'take_profit_pct': 0.25, 'price_alert_range': 0.05,
        },
    },
    'BuLuoGeLi': {
        'display_mode': 'simple',
        'risk_profile': 'balanced',
        'push_preferences': {
            'morning_brief': True, 'closing_review': False,
            'risk_alert': True, 'trade_signal': False, 'breaking_news': False,
        },
        'watchlist_config': {
            'stop_loss_pct': -0.05, 'take_profit_pct': 0.15, 'price_alert_range': 0.03,
        },
    },
}
```

### 2.3 隔离矩阵

| 功能模块 | 隔离方式 | 说明 |
|----------|----------|------|
| 持仓数据 | SHA256(userId).json | 已有，各自独立文件 |
| 交易流水 | 同上（portfolio.transactions） | V4 交易流水制已实现 |
| 聊天记忆 | `data/{userId}/memory/` | agent_memory.py 已实现 |
| 分析结果 | 按 userId 参数隔离 | API 调用时传 userId |
| 风险画像 | JSON 用户文件新增字段 | Phase 0 新增 |
| 盯盘阈值 | JSON 用户文件新增字段 | Phase 0 新增 |
| 推送偏好 | JSON 用户文件新增字段 | Phase 0 新增 |
| 模式偏好 | JSON 用户文件新增字段 | Phase 0 新增 |
| 市场数据 | 共享（内存 dict 缓存） | 不需要隔离 |
| R1 分析缓存 | 共享（内存 dict 缓存） | 宏观分析对所有人一样 |

> **推送分类规则**：
> - **投资类推送**（早安简报/复盘/风险预警）→ 按各人 `push_preferences` 决定
> - **技术类告警**（数据源巡检/服务异常/API 报错）→ **只推给 LeiJiang**，不打扰老婆

---

## 三、Simple/Pro 双模式

### 3.1 后端：用户偏好 API

> 新增 2 个端点到 `main.py`

```python
# === 新增 API：用户偏好 ===

@app.get("/api/user/preference")
async def get_user_preference(userId: str):
    """获取用户偏好（Simple/Pro模式、推送、盯盘阈值）"""
    user = load_user(userId)
    defaults = USER_DEFAULTS.copy()
    overrides = USER_OVERRIDES.get(userId, {})
    
    return {
        "display_mode": user.get("display_mode", overrides.get("display_mode", defaults["display_mode"])),
        "risk_profile": user.get("risk_profile", overrides.get("risk_profile", defaults["risk_profile"])),
        "push_preferences": user.get("push_preferences", overrides.get("push_preferences", defaults["push_preferences"])),
        "watchlist_config": user.get("watchlist_config", overrides.get("watchlist_config", defaults["watchlist_config"])),
    }

@app.put("/api/user/preference")
async def update_user_preference(userId: str, body: dict):
    """更新用户偏好"""
    user = load_user(userId)
    
    for key in ["display_mode", "risk_profile", "push_preferences", "watchlist_config"]:
        if key in body:
            user[key] = body[key]
    
    save_user(user)
    return {"success": True}
```

### 3.2 前端：模式切换 (app.js)

> 在 `app.js` 中新增渲染逻辑

```javascript
// === Simple/Pro 模式切换 ===

let currentMode = localStorage.getItem('display_mode') || 'simple';

function renderModeSwitch() {
    return `
    <div class="mode-switch">
        <button class="${currentMode === 'simple' ? 'active' : ''}" 
                onclick="setMode('simple')">🌸 简洁</button>
        <button class="${currentMode === 'pro' ? 'active' : ''}" 
                onclick="setMode('pro')">🔧 专业</button>
    </div>`;
}

async function setMode(mode) {
    const oldMode = currentMode;
    currentMode = mode;
    localStorage.setItem('display_mode', mode);
    
    try {
        await fetch(`${ENGINE}/api/user/preference?userId=${USER_ID}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ display_mode: mode })
        });
        renderCurrentTab();  // 重新渲染当前页面
    } catch (e) {
        currentMode = oldMode;  // 回滚
        localStorage.setItem('display_mode', oldMode);
        showToast('切换失败，请重试');
    }
}

// 启动时从后端同步
async function syncPreferences() {
    try {
        const res = await fetch(`${ENGINE}/api/user/preference?userId=${USER_ID}`);
        const prefs = await res.json();
        currentMode = prefs.display_mode || 'simple';
        localStorage.setItem('display_mode', currentMode);
    } catch (e) {
        // 降级：用 localStorage 缓存
    }
}
```

### 3.3 Simple 模式首页 (renderLanding 改造)

```
┌─────────────────────────────────────┐
│  🏠 MoneyBag          [🌸简洁|🔧专业] │
├─────────────────────────────────────┤
│                                     │
│  💰 家庭净资产                       │
│  ¥ 520,000                         │
│  📈 今日 +0.23% (+¥1,200)          │
│  厉害了哥 ¥320,000 / 部落格里 ¥200,000│
│                                     │
├─────────────────────────────────────┤
│  📡 今日信号                         │
│  🟢 沪深300 信号良好                 │
│  🟡 消费行业 观望                    │
│  🔴 招商银行 止盈提醒               │
├─────────────────────────────────────┤
│  🏥 持仓健康度                       │
│  整体：🟢 良好 (85/100)             │
│  ├─ 分散度：🟢 优秀                 │
│  ├─ 风险敞口：🟡 适中               │
│  └─ 盈亏状态：🟢 盈利               │
├─────────────────────────────────────┤
│  🎯 AI 管家准确率                    │
│  近7日：65% │ 近30日：62%           │
├─────────────────────────────────────┤
│  💬 问问管家                         │
│  [沪深300现在能买吗？    ] [发送]    │
└─────────────────────────────────────┘
```

### 3.4 Pro 模式额外展示

Simple 基础上追加：
- 技术指标详情（MACD、RSI、布林带）— 已有 `technical.py`
- 因子分析结果（30+ 因子）— 已有 `factor_data.py`
- 风控数据（VaR、回撤）— 已有 `risk.py`
- 模块执行状态 — 已有 `module_registry.py`
- 判断追踪 — 已有 `judgment_tracker.py`

---

## 四、前端 API 接入方案

### 4.1 通用封装

> 在 `app.js` 中新增统一请求函数

```javascript
// === API 封装 ===
const ENGINE = localStorage.getItem('moneybag_engine') || 'http://150.158.47.189:8000';

async function apiCall(path, options = {}) {
    const url = `${ENGINE}${path}`;
    const timeout = options.timeout || 10000;
    
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeout);
        
        const res = await fetch(url, {
            ...options,
            signal: controller.signal,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
        });
        clearTimeout(timer);
        
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (e) {
        if (e.name === 'AbortError') {
            return { error: true, message: '请求超时，请稍后再试' };
        }
        return { error: true, message: e.message || '网络异常' };
    }
}

// R1 深度分析专用（180s 超时 + 思考动画）
async function apiCallR1(path, options = {}) {
    showThinkingAnimation('AI 正在深度分析...');
    try {
        return await apiCall(path, { ...options, timeout: 180000 });
    } finally {
        hideThinkingAnimation();
    }
}
```

### 4.2 超时策略

| API 类型 | 超时 | 前端表现 |
|----------|------|----------|
| 普通查询（持仓/净资产/信号） | 10s | 骨架屏 → 数据/错误卡片 |
| V3 AI 响应（聊天/简单问答） | 15s | "管家正在查询..." |
| R1 深度分析（买卖决策/诊断） | **180s** | **思考动画 + "预计30-60秒"** |
| 新闻深度分析（deep-impact） | 30s | "AI 正在分析新闻影响..." |
| 7-Skill 持仓分析 | 60s | "正在进行多维度分析..." |

### 4.3 前端组件-API 映射表

> 每个组件 = app.js 中的一个 `render*()` 函数

| 前端函数 | 后端 API | 模式 | 刷新策略 |
|----------|----------|------|----------|
| `renderNetWorth()` | `GET /api/portfolio/networth` × 2人 | Both | 手动下拉 |
| `renderSignalCard()` | `GET /api/daily-signal?userId=` | Both | 5min 缓存 |
| `renderHealthScore()` | `GET /api/allocation-advice?userId=` | Both | 首次加载 |
| `renderAccuracy()` | `GET /api/accuracy?userId=` (新增) | Both | 首次加载 |
| `renderChatInput()` | `POST /api/chat` | Both | 实时 |
| `renderHoldingAnalysis()` | `POST /api/stock-holdings/analyze` | **Pro** | 手动触发 |
| `renderFundAnalysis()` | `POST /api/fund-holdings/analyze` | **Pro** | 手动触发 |
| `renderDeepImpact()` | `POST /api/news/deep-impact` | **Pro** | 手动触发 |
| `renderRiskAssess()` | `POST /api/news/risk-assess` | **Pro** | 手动触发 |
| `renderSignalInterpret()` | `POST /api/daily-signal/interpret` | **Pro** | 手动触发 |
| `renderTimingAdvice()` | `GET /api/timing?userId=` | **Pro** | 首次加载 |
| `renderSmartDCA()` | `GET /api/smart-dca?userId=` | **Pro** | 首次加载 |
| `renderAlerts()` | `GET /api/watchlist/alerts?userId=` (新增) | Both | 15s 轮询 |

### 4.4 前端三态模式（所有组件统一）

```javascript
// 通用三态渲染
function renderCard(title, state, content) {
    if (state === 'loading') {
        return `<div class="card skeleton"><h3>${title}</h3><div class="pulse-bar"></div></div>`;
    }
    if (state === 'error') {
        return `<div class="card error"><h3>${title}</h3><p>加载失败</p>
                <button onclick="retry('${title}')">重试</button></div>`;
    }
    if (state === 'empty') {
        return `<div class="card empty"><h3>${title}</h3><p>暂无数据</p></div>`;
    }
    return `<div class="card">${content}</div>`;
}
```

---

## 五、四大增强功能

### 5.1 资产配置建议（首页增强）

**定位**：`renderLanding()` 的「持仓健康度」卡片深化

#### 配置模板（7大资产类别 × 4种风险画像）

```python
# 放在 config.py 或 main.py 顶部
ALLOCATION_TEMPLATES = {
    'conservative': {  # 保守型
        '股票': 0.10, '债券': 0.40, '现金': 0.20, '黄金': 0.10,
        '海外': 0.05, '房产': 0.10, '另类': 0.05,
    },
    'balanced': {      # 平衡型（部落格里）
        '股票': 0.30, '债券': 0.25, '现金': 0.15, '黄金': 0.10,
        '海外': 0.10, '房产': 0.05, '另类': 0.05,
    },
    'growth': {        # 成长型（厉害了哥）
        '股票': 0.50, '债券': 0.15, '现金': 0.10, '黄金': 0.05,
        '海外': 0.10, '房产': 0.05, '另类': 0.05,
    },
    'aggressive': {    # 激进型
        '股票': 0.70, '债券': 0.05, '现金': 0.05, '黄金': 0.03,
        '海外': 0.10, '房产': 0.02, '另类': 0.05,
    },
}
```

#### 后端：激活 `portfolio_optimizer.py` + 新 API

```python
# main.py 新增
from services.portfolio_optimizer import PortfolioOptimizer

@app.get("/api/asset-allocation")
async def asset_allocation(userId: str, mode: str = "simple"):
    """资产配置建议"""
    user = load_user(userId)
    profile = user.get("risk_profile", "balanced")
    optimizer = PortfolioOptimizer()
    
    # 获取当前持仓分布
    portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
    holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
    
    # 计算建议
    try:
        result = optimizer.get_allocation_advice(holdings, profile)
        
        if mode == "simple":
            return {
                "score": result.get("score", 70),
                "level": "good" if result["score"] >= 70 else "warning" if result["score"] >= 40 else "danger",
                "summary": result.get("summary", "配置均衡"),
            }
        else:
            return result  # Pro 返回完整数据
    except Exception as e:
        return {"error": True, "message": str(e)}
```

#### 前端：健康度卡片

```javascript
// Simple: 评分 + 一句话
// Pro: 评分 + 饼图 + 偏离度 + 调整建议
async function renderHealthScore() {
    const data = await apiCall(`/api/asset-allocation?userId=${USER_ID}&mode=${currentMode}`);
    if (data.error) return renderCard('持仓健康度', 'error', '');
    
    if (currentMode === 'simple') {
        const emoji = data.level === 'good' ? '🟢' : data.level === 'warning' ? '🟡' : '🔴';
        return `<div class="card">
            <h3>🏥 持仓健康度</h3>
            <p class="big-score">${emoji} ${data.score}/100</p>
            <p>${data.summary}</p>
        </div>`;
    } else {
        // Pro: 用 Chart.js 画饼图
        return `<div class="card">
            <h3>🏥 持仓健康度 (${data.score}/100)</h3>
            <canvas id="allocationChart"></canvas>
            <div class="deviation-list">${renderDeviationTable(data.deviation)}</div>
        </div>`;
    }
}
```

### 5.2 实时盯盘（智能休眠）

#### 后端：新增盯盘 API + 定时检查

```python
# main.py 新增
import asyncio
from datetime import time as dtime

# 盯盘运行时间：9:25 - 15:05（交易时段 ± 5分钟）
MARKET_OPEN = dtime(9, 25)
MARKET_CLOSE = dtime(15, 5)

# 预警冷却：同一条预警 30 分钟内不重复
_alert_cooldown = {}  # key: f"{userId}_{symbol}_{type}" → last_sent_time

@app.get("/api/watchlist/alerts")
async def watchlist_alerts(userId: str, since: float = 0):
    """获取盯盘预警"""
    user = load_user(userId)
    portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
    holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
    
    # 空仓 → 返回空列表（盯盘休眠）
    if not holdings.get("active"):
        return {"alerts": [], "status": "idle", "message": "空仓观望中"}
    
    # 非交易时间 → 返回空
    now = datetime.now().time()
    if not (MARKET_OPEN <= now <= MARKET_CLOSE):
        return {"alerts": [], "status": "closed", "message": "非交易时间"}
```

#### 空仓时任务调度矩阵

| 任务类型 | 有持仓 | 空仓 | 原因 |
|----------|--------|------|------|
| **实时盯盘** | ✅ 运行 | ❌ idle | 没东西盯 |
| **数据缓存** | ✅ 运行 | ✅ 运行 | 投资建议需要 |
| **R1 凌晨分析** | ✅ 运行 | ✅ 运行 | 找买入时机 |
| **早安简报** | ✅ 持仓导向 | ✅ **机会导向** | 内容切换 |
| **收盘复盘** | ✅ 完整版 | ⚠️ 简化版 | 只说大盘 |

```python
    
    # 检查各持仓预警
    config = user.get("watchlist_config", USER_DEFAULTS["watchlist_config"])
    alerts = []
    
    for h in holdings["active"]:
        pnl_pct = (h["currentNav"] - h["avgNav"]) / h["avgNav"] if h["avgNav"] > 0 else 0
        
        # 止损
        if pnl_pct <= config["stop_loss_pct"]:
            alert = _make_alert(userId, h, "stop_loss", "danger", 
                f"{h['name']} 已亏损 {pnl_pct:.1%}，触及止损线")
            if alert: alerts.append(alert)
        
        # 止盈
        if pnl_pct >= config["take_profit_pct"]:
            alert = _make_alert(userId, h, "take_profit", "warning",
                f"{h['name']} 已盈利 {pnl_pct:.1%}，考虑止盈")
            if alert: alerts.append(alert)
    
    return {"alerts": alerts, "status": "active"}

def _make_alert(userId, holding, alert_type, level, message):
    """生成预警（带 30 分钟冷却）"""
    key = f"{userId}_{holding['code']}_{alert_type}"
    now = time.time()
    
    if key in _alert_cooldown and (now - _alert_cooldown[key]) < 1800:
        return None  # 30 分钟冷却中
    
    _alert_cooldown[key] = now
    return {
        "type": alert_type,
        "symbol": holding["code"],
        "name": holding["name"],
        "level": level,
        "message": message,
        "timestamp": datetime.now().isoformat(),
    }
```

#### 前端：15 秒轮询

```javascript
let alertPolling = null;

function startAlertPolling() {
    if (alertPolling) return;
    alertPolling = setInterval(async () => {
        const data = await apiCall(`/api/watchlist/alerts?userId=${USER_ID}`);
        if (data.alerts && data.alerts.length > 0) {
            showAlertBadge(data.alerts.length);
            // 高危预警弹 toast
            data.alerts.filter(a => a.level === 'danger').forEach(a => {
                showToast(`⚠️ ${a.message}`, 'danger');
            });
        }
    }, 15000);
}

function stopAlertPolling() {
    if (alertPolling) { clearInterval(alertPolling); alertPolling = null; }
}
```

### 5.3 AI 聊天增强

#### 后端：意图分类 + 记忆修复

> 改造 `main.py` 中现有的 `/api/chat` 路由

```python
# 意图分类（嵌入现有 chat 路由）
INTENT_KEYWORDS = {
    # R1 深度思考（优先匹配）
    'buy_decision':  (['能买吗', '可以买', '要不要买', '值得买', '入场', '抄底'], 'llm_heavy'),
    'sell_decision': (['要卖吗', '该卖', '止盈', '止损', '出场', '清仓'], 'llm_heavy'),
    'portfolio':     (['优化持仓', '调仓', '配置建议', '怎么调整'], 'llm_heavy'),
    'risk':          (['风险', '危险', '会跌吗', '安全吗', '回撤'], 'llm_heavy'),
    # V3 快速响应（后匹配）
    'price':         (['多少钱', '什么价', '现价', '股价', '行情'], 'llm_light'),
    'holding':       (['我的持仓', '持有', '仓位', '有多少'], 'llm_light'),
    'qa':            (['什么是', '解释', '定义', '含义'], 'llm_light'),
    'news':          (['新闻', '消息', '动态'], 'llm_light'),
}

def classify_intent(message: str) -> tuple:
    """返回 (intent_name, model_tier)"""
    msg = message.lower()
    # 先匹配 R1 意图（复杂优先）
    for intent, (keywords, tier) in INTENT_KEYWORDS.items():
        if tier == 'llm_heavy' and any(kw in msg for kw in keywords):
            return intent, tier
    # 再匹配 V3 意图
    for intent, (keywords, tier) in INTENT_KEYWORDS.items():
        if tier == 'llm_light' and any(kw in msg for kw in keywords):
            return intent, tier
    return 'unknown', 'llm_light'  # Fallback 用 V3

# 在现有 /api/chat 路由中：
# 1. intent, tier = classify_intent(message)
# 2. 用 llm_gateway.call(prompt, tier) 替代硬编码模型
# 3. 修复：调用 build_memory_summary(userId) 而非 get_memory_summary
# 4. 修复：响应后调用 save_context(userId, message, result)
```

#### 前端：模式适配

```javascript
// /api/chat 响应后，根据模式格式化展示
function renderChatResponse(result) {
    if (currentMode === 'simple') {
        return `<div class="chat-bubble ai">
            <p class="conclusion">${result.emoji || '💡'} ${result.conclusion}</p>
            <p class="reason">${result.one_line_reason || ''}</p>
        </div>`;
    } else {
        return `<div class="chat-bubble ai pro">
            <h4>${result.title || '分析结果'}</h4>
            <p><strong>结论：</strong>${result.conclusion}</p>
            <p><strong>分析：</strong>${result.detailed_analysis || ''}</p>
            <p class="warning"><strong>风险提示：</strong>${result.risk_warning || ''}</p>
            <p class="meta">引擎: ${result.engine} | 耗时: ${result.thinking_time || '-'}s</p>
        </div>`;
    }
}
```

### 5.4 推送内容适配（空仓切换）

#### 后端：改造 `wxwork_push.py`

```python
# 在现有 wxwork_push.py 的推送函数中增加空仓判断
def build_morning_brief(userId: str) -> str:
    """08:30 早安简报 - 根据持仓状态切换内容"""
    user = load_user(userId)
    mode = user.get("display_mode", "simple")
    portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
    holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
    
    has_holdings = bool(holdings.get("active"))
    
    if has_holdings:
        summary = calc_portfolio_summary(holdings)
        if mode == 'simple':
            return f"🌅 早安！\n💰 总资产 ¥{summary['total']:,.0f}\n📈 昨日 {summary['change']:+,.0f}"
        else:
            return f"🌅 早安！持仓回顾\n\n💰 总资产：¥{summary['total']:,.0f}\n📈 昨日：{summary['change']:+,.0f} ({summary['change_pct']:+.2%})\n\n📡 今日关注：\n{get_focus_items(userId)}"
    else:
        # 空仓 → 机会导向
        if mode == 'simple':
            return f"🌅 早安！\n📊 大盘：{get_market_signal_text()}\n💡 建议关注：{get_top_recommendation()}\n空仓观望中 👀"
        else:
            return f"🌅 早安！今日市场机会\n\n📊 大盘信号：{get_market_signal_text()}\n🔥 热门行业：{get_hot_sectors_text()}\n💡 建议关注：{get_recommendations_text()}\n\n空仓观望中，管家持续监控入场时机 👀"
```

---

## 六、家庭资产汇总

### 6.1 后端 API

```python
# main.py 新增
FAMILY_MEMBERS = ['LeiJiang', 'BuLuoGeLi']
NICKNAMES = {'LeiJiang': '厉害了哥', 'BuLuoGeLi': '部落格里'}

@app.get("/api/household/summary")
async def household_summary():
    """家庭资产汇总"""
    members = []
    total_value = 0
    total_change = 0
    
    for uid in FAMILY_MEMBERS:
        user = load_user(uid)
        portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
        holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
        assets = portfolio.get("assets", [])
        
        # 用现有 unified_networth 计算
        from services.unified_networth import calc_net_worth
        nw = calc_net_worth(holdings, assets)
        
        members.append({
            "userId": uid,
            "nickname": NICKNAMES.get(uid, uid),
            "value": nw.get("total", 0),
            "change": nw.get("daily_change", 0),
        })
        total_value += nw.get("total", 0)
        total_change += nw.get("daily_change", 0)
    
    yesterday = total_value - total_change
    change_pct = (total_change / yesterday) if yesterday > 0 else 0.0
    
    return {
        "total_value": total_value,
        "daily_change": total_change,
        "daily_change_pct": change_pct,
        "members": members,
    }
```

---

## 七、补充设计：资产快照 + 聊天记忆清理 + 交易流水分页

### 7.1 资产历史快照（每日 16:00）

> 在 `scripts/cache_warmer.py` 或新建定时脚本中添加

```python
# 每日 16:00（收盘后）记录资产快照到 JSON
def save_daily_snapshot():
    """写入 data/snapshots/{userId}/{date}.json"""
    for uid in FAMILY_MEMBERS:
        user = load_user(uid)
        portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
        holdings = calc_holdings_from_transactions(portfolio.get("transactions", []))
        
        snapshot = {
            "date": date.today().isoformat(),
            "total_value": sum(h.get("marketValue", 0) for h in holdings.get("active", [])),
            "stock_value": sum(h.get("marketValue", 0) for h in holdings.get("active", []) if h.get("type") == "stock"),
            "fund_value": sum(h.get("marketValue", 0) for h in holdings.get("active", []) if h.get("type") == "fund"),
        }
        
        snapshot_dir = DATA_DIR / "snapshots" / uid
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / f"{date.today()}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
        )
    
    logger.info("📸 每日资产快照完成")

# 注册到定时任务（systemd timer 或 cron）
# 0 16 * * 1-5 cd /opt/moneybag && /opt/moneybag/venv/bin/python -c "from scripts.snapshot import save_daily_snapshot; save_daily_snapshot()"
```

### 7.2 资产历史 API（用于曲线图）

```python
@app.get("/api/asset-history")
async def asset_history(userId: str, days: int = 90):
    """获取资产历史数据（用于 Chart.js 曲线）"""
    snapshot_dir = DATA_DIR / "snapshots" / userId
    if not snapshot_dir.exists():
        return {"data": [], "message": "暂无历史数据"}
    
    files = sorted(snapshot_dir.glob("*.json"), reverse=True)[:days]
    data = []
    for f in reversed(files):
        try:
            data.append(json.loads(f.read_text(encoding="utf-8")))
        except: pass
    
    return {"data": data}
```

### 7.3 聊天记忆清理策略

```python
# 在 agent_memory.py 中增加
MAX_MEMORY_ROUNDS = 50  # 最多保留 50 轮对话
SUMMARY_WINDOW = 20     # build_memory_summary 取最近 20 条

def cleanup_old_memory(userId: str):
    """清理超过 50 轮的旧记忆"""
    memory_dir = DATA_DIR / userId / "memory"
    if not memory_dir.exists():
        return
    
    files = sorted(memory_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
    if len(files) > MAX_MEMORY_ROUNDS:
        for f in files[:-MAX_MEMORY_ROUNDS]:
            f.unlink()

# 在 save_context() 末尾调用 cleanup_old_memory(userId)
```

### 7.4 交易流水分页 API

```python
@app.get("/api/transactions")
async def get_transactions(userId: str, page: int = 1, pageSize: int = 20, symbol: str = None):
    """分页查询交易流水"""
    user = load_user(userId)
    portfolio = ensure_v4_portfolio(user.get("portfolio", {}))
    transactions = portfolio.get("transactions", [])
    
    # 按日期倒序
    transactions.sort(key=lambda t: t.get("date", ""), reverse=True)
    
    # 按 symbol 筛选
    if symbol:
        transactions = [t for t in transactions if t.get("code") == symbol]
    
    total = len(transactions)
    start = (page - 1) * pageSize
    end = start + pageSize
    
    return {
        "data": transactions[start:end],
        "pagination": {
            "page": page,
            "pageSize": pageSize,
            "total": total,
            "totalPages": (total + pageSize - 1) // pageSize,
        }
    }
```

---

## 七、实施计划（3 天）

### Day 1：框架搭建 + BUG 修复（7h）

| # | 任务 | 时间 | 改动文件 | 验证方法 |
|---|------|------|----------|----------|
| 1.1 | 用户偏好 API (GET/PUT) | 1h | `main.py` + `persistence.py` | curl 读写偏好 |
| 1.2 | Simple/Pro 模式切换 UI | 1.5h | `app.js` + `styles.css` | 切换后刷新保持 |
| 1.3 | 首页 renderLanding() 改造 | 2h | `app.js` | Simple 版首页截图 |
| 1.4 | 修复 `build_memory_summary` | 0.5h | `agent_engine.py` 或调用处 | 聊天问"上次分析了什么" |
| 1.5 | 修复 `save_context()` 未调用 | 0.5h | `main.py` chat 路由 | 连续对话上下文串联 |
| 1.6 | 删除死代码 chat.py | 0.5h | 删除文件 | grep 零引用 |
| 1.7 | 前端三态组件 + apiCall 封装 | 1h | `app.js` | 断网显示错误卡片 |

**Day 1 结束验证**：
```bash
# 后端
curl http://150.158.47.189:8000/api/health
curl http://150.158.47.189:8000/api/user/preference?userId=LeiJiang
# 前端：两个账号分别打开，确认 Simple/Pro 切换正常
```

### Day 2：P0 API 接入（7h）

| # | 任务 | 时间 | 改动文件 | 验证方法 |
|---|------|------|----------|----------|
| 2.1 | 家庭资产汇总 API + 前端 | 1.5h | `main.py` + `app.js` | 首页显示两人合计 |
| 2.2 | `/news/deep-impact` 前端接入 | 1.5h | `app.js` | 资讯页点击新闻展示分析 |
| 2.3 | `/news/risk-assess` 前端接入 | 1h | `app.js` | 风控评估展示 |
| 2.4 | `/stock-holdings/analyze` 前端接入 | 1.5h | `app.js` | Pro 模式可触发分析 |
| 2.5 | `/fund-holdings/analyze` 前端接入 | 0.5h | `app.js` | 复用 2.4 |
| 2.6 | Day 2 自测 | 1h | - | 两账号登录确认隔离 |

### Day 3：P1 API + 增强功能（7h）

| # | 任务 | 时间 | 改动文件 | 验证方法 |
|---|------|------|----------|----------|
| 3.1 | `/daily-signal/interpret` 接入 | 1h | `app.js` | Pro 模式信号解读 |
| 3.2 | `/timing` + `/smart-dca` 接入 | 1h | `app.js` | Pro 模式展示建议 |
| 3.3 | 激活 portfolio_optimizer.py | 1h | `main.py` | 资产配置 Simple/Pro |
| 3.4 | 盯盘 API + 前端轮询 | 1h | `main.py` + `app.js` | 空仓返回 idle |
| 3.5 | 推送内容空仓适配 | 1h | `wxwork_push.py` | 空仓推送"机会导向" |
| 3.6 | 聊天意图分类 + 记忆修复验证 | 0.5h | `main.py` | R1 问题走 R1 引擎 |
| 3.7 | 全量联调 | 1.5h | - | 健康检查脚本 |

---

## 八、API 健康检查脚本

```python
#!/usr/bin/env python3
"""Phase 0 API 验证 - 每天结束运行"""
import requests

BASE = "http://150.158.47.189:8000"

def check(name, method, url, **kw):
    try:
        r = getattr(requests, method)(f"{BASE}{url}", timeout=10, **kw)
        ok = r.status_code == 200
        print(f"  {'✅' if ok else '❌'} {name} [{r.status_code}] {r.elapsed.total_seconds():.1f}s")
        return ok
    except Exception as e:
        print(f"  ❌ {name} ERROR: {e}")
        return False

print("=== Day 1 ===")
check("健康检查", "get", "/api/health")
check("偏好-读", "get", "/api/user/preference?userId=LeiJiang")
check("偏好-写", "put", "/api/user/preference?userId=LeiJiang",
      json={"display_mode": "pro"})

print("\n=== Day 2 ===")
check("家庭汇总", "get", "/api/household/summary")
check("新闻分析", "post", "/api/news/deep-impact",
      json={"newsId": "test", "userId": "LeiJiang"})
check("持仓分析", "post", "/api/stock-holdings/analyze",
      json={"userId": "LeiJiang"})

print("\n=== Day 3 ===")
check("资产配置-Simple", "get", "/api/asset-allocation?userId=LeiJiang&mode=simple")
check("资产配置-Pro", "get", "/api/asset-allocation?userId=LeiJiang&mode=pro")
check("盯盘预警", "get", "/api/watchlist/alerts?userId=LeiJiang")
check("智能定投", "get", "/api/smart-dca?userId=LeiJiang")
check("入场时机", "get", "/api/timing?userId=LeiJiang")

print("\n=== 多账号 ===")
check("部落格里-偏好", "get", "/api/user/preference?userId=BuLuoGeLi")
check("部落格里-配置", "get", "/api/asset-allocation?userId=BuLuoGeLi&mode=simple")
check("AI聊天", "post", "/api/chat",
      json={"message": "沪深300能买吗", "userId": "LeiJiang"})
```

---

## 九、测试用例

### 9.1 模式切换

| ID | 操作 | 预期 |
|----|------|------|
| TC-1.1 | 厉害了哥切到 Simple | 界面简化 + 后端 JSON 更新 |
| TC-1.2 | 部落格里切到 Pro | 界面展开 + 后端 JSON 更新 |
| TC-1.3 | 刷新页面 | 模式保持（后端恢复） |
| TC-1.4 | 断网切换 | localStorage 回滚 + toast |
| TC-1.5 | Pro 额外数据 | 只在 Pro 下请求 |

### 9.2 多账号隔离

| ID | 操作 | 预期 |
|----|------|------|
| TC-2.1 | 厉害了哥看持仓 | 只看到自己的 |
| TC-2.2 | 首页家庭资产 | 两人合计 + 各自明细 |
| TC-2.3 | 各自聊天 | 记忆互不干扰 |
| TC-2.4 | 推送偏好 | 部落格里不收复盘 |

### 9.3 空仓场景

| ID | 场景 | 预期 |
|----|------|------|
| TC-3.1 | 一人有仓一人空仓 | 有仓的盯盘，空仓的 idle |
| TC-3.2 | 都空仓 | 盯盘 idle，后台分析继续 |
| TC-3.3 | 空仓早安简报 | "机会导向"内容 |
| TC-3.4 | 首页空仓 | 不报错，"暂无持仓" |

### 9.4 可靠性

| ID | 场景 | 预期 |
|----|------|------|
| TC-4.1 | 后端挂了 | 错误卡片，不白屏 |
| TC-4.2 | API 超时 | 超时提示 + 重试 |
| TC-4.3 | R1 分析中 | 思考动画 |

---

## 十、验收标准

### 基础功能（Day 1-2）
- [ ] Simple/Pro 模式可切换，刷新保持
- [ ] Simple 模式：老婆 5 秒内获取关键信息
- [ ] 7 个 P0+P1 API 前端可访问
- [ ] 3 个记忆 BUG 已修复
- [ ] 死代码已清理
- [ ] 前端三态覆盖（Loading/Error/Empty）

### 增强功能（Day 3）
- [ ] 资产配置建议：健康度卡片 + portfolio_optimizer 激活
- [ ] 实时盯盘：空仓休眠 + 交易时间外不跑 + 30分钟冷却
- [ ] AI 聊天：意图分类 + 记忆生效 + 模式适配
- [ ] 家庭资产汇总：首页两人合计
- [ ] 推送内容：空仓切换为"机会导向"

### 多账号隔离
- [ ] 持仓/记忆/分析按 userId 隔离
- [ ] 推送偏好各人独立
- [ ] 盯盘阈值各人独立

### 质量保证
- [ ] 健康检查脚本全部通过
- [ ] 无控制台报错

---

## 十一、部署流程

```bash
# 1. SSH 登录
ssh ubuntu@150.158.47.189

# 2. 拉代码
cd /opt/moneybag && git pull origin main

# 3. 重启后端
sudo systemctl restart moneybag

# 4. 验证
curl http://150.158.47.189:8000/api/health

# 5. 前端自动部署（push main → GitHub Actions → GitHub Pages）
# 无需手动操作

# 回滚
cd /opt/moneybag && git revert HEAD
sudo systemctl restart moneybag
```

---

**下一步**：确认后开始 Day 1 实施。

---

## 附录 A：功能定位总结

| 功能 | 定位 | 增强点 | 影响范围 |
|------|------|--------|----------|
| **资产配置建议** | 首页增强 | 健康度 → 资产分布 + 调仓建议 | renderLanding() + 新 API |
| **实时盯盘** | 已有功能 | **空仓智能休眠** + 个性化阈值 | 新增 API + 前端轮询 |
| **AI 聊天** | 已有功能增强 | 意图路由 + 记忆修复 + 模式适配 | main.py chat 路由改造 |
| **资产功能** | 已有功能增强 | 家庭合并 + 交易流水分页 + 历史曲线 | 新增 API + app.js |

## 附录 B：Pro 模式完整信息架构

```
首页 (renderLanding)
├── 家庭净资产卡片（两人合计 + 各自明细）
├── 今日信号（Simple 简化 / Pro 详细）
├── 持仓健康度（Simple 评分 / Pro 饼图+偏离度）
├── AI 准确率
├── [Pro] 早安简报卡片
└── 快捷问管家

持仓页 (renderStocks)
├── 股票持仓列表
│   └── [Pro] 7-Skill 深度分析入口
├── 基金持仓列表
│   └── [Pro] 7-Skill 深度分析入口
├── 交易流水（分页）
├── 资产历史曲线（Chart.js）
└── [Pro] 组合优化建议

资讯页 (renderInsight)
├── 新闻列表（16 个子 Tab 已有）
├── [Pro] Deep-Impact 深度分析
└── [Pro] 风控评估

AI分析页 (renderChat)
├── 聊天界面（意图路由 + 模式适配）
├── [Pro] 信号解读
├── [Pro] 入场时机
├── [Pro] 智能定投
└── [Pro] Agent 记忆查看

资产页 (renderAssets)
├── 全资产管理（已有）
├── 记账（已有）
└── 收入源（已有）
```

## 附录 C：V6 后续衔接

Phase 0 完成后，V6 新功能自动有位置放：

| V6 新功能 | 放在哪 | 对应前端 |
|-----------|--------|----------|
| 地缘政治分析 | 资讯页 [Pro] | renderInsight() 新增子 Tab |
| 原油联动分析 | 资讯页 [Pro] | 同上 |
| 北向资金修复 | 首页信号卡片 | renderSignalCard() 增加数据 |
| 行业轮动 | 持仓页推荐 | renderStocks() 新增推荐区 |
| 分析历史 | AI 分析页 | renderChat() 新增历史时间线 |

## 附录 D：设计参考来源

| 来源 | 吸收内容 |
|------|----------|
| 幻方量化 | 信号侦察、风险预警思路 |
| Monarch Money | 净资产仪表盘、交易流水制 |
| Ghostfolio | Activity/Transaction 数据模型（加权平均成本法） |
| 6 位朋友方案 | 风控参数、门控、成本控制等 |

## 附录 E：R1 深度分析取消机制

```
用户点击「取消」时的行为：

前端：
├── 停止等待响应（AbortController.abort()）
├── 隐藏思考动画
└── 显示"已取消"

后端：
├── R1 调用继续完成（不中断）
├── 结果写入缓存（下次可复用）
└── 不浪费已消耗的 token

原因：R1 一次调用 ¥0.06，中断了也收费，不如跑完缓存
```


---

# Part 2：V6 — 7 大模块升级设计（V4-Final）

> 📄 原始文件：`docs/v6-upgrade-design-final.md`
> 🎯 目标：地缘政治 + 原油 + 北向 + 行业轮动 + 研报 + 情景分析 + 分析历史
> ⏱️ 工时：8-12 天（5 Phase）

# MoneyBag V6 完整升级设计方案

> **版本**：V4-Final（整合 V1~V4 全部迭代）
> **日期**：2026-04-15
> **背景**：双AI vs 6家机构对比，发现 AI 最大盲点是**地缘政治**和**大宗商品**。本文档设计 7 个改进模块（A~G），从数据层→分析层→展示层→历史存档全链路打通。
> **设计原则**：不新建框架，在现有 ModuleRegistry + Pipeline + `_build_market_context` 体系上增量扩展。每个模块独立可部署，互相可选依赖。
> **配套文件**：`双AI投资分析对比报告.md` / `AI分析vs专业机构对比报告.md`

---

## 目录

1. [项目背景与问题诊断](#1-项目背景与问题诊断)
2. [架构总览](#2-架构总览)
3. [模块A：地缘政治/重大事件追踪 🔴 P0](#3-模块a地缘政治重大事件追踪--p0)
4. [模块B：原油/大宗商品扩展 🔴 P0](#4-模块b原油大宗商品扩展--p0)
5. [模块C：修复北向资金 + ETF资金流 🟡 P1](#5-模块c修复北向资金--etf资金流--p1)
6. [模块D：行业轮动分析 🟡 P1](#6-模块d行业轮动分析--p1)
7. [模块E：券商研报摘要 🟢 P2](#7-模块e券商研报摘要--p2)
8. [模块F：情景分析引擎 🟢 P2](#8-模块f情景分析引擎--p2)
9. [模块G：分析历史 + 多源对比 + 自动录入 🔴 P0](#9-模块g分析历史--多源对比--自动录入--p0)
10. [模块关联矩阵](#10-模块关联矩阵)
11. [各场景下的使用方式](#11-各场景下的使用方式)
12. [前后端对应审查结果](#12-前后端对应审查结果)
13. [Simple/Pro 模式分配](#13-simplepro-模式分配)
14. [多账号（铁律#19）审查](#14-多账号铁律19审查)
15. [铁律检查点矩阵](#15-铁律检查点矩阵)
16. [逐 Phase 验证清单](#16-逐-phase-验证清单)
17. [实施计划（5 Phase / 8-12天）](#17-实施计划5-phase--8-12天)
18. [风险评估](#18-风险评估)
19. [预期效果](#19-预期效果)

---

## 1. 项目背景与问题诊断

### 对比实验摘要

2026-04-15 进行了 **MoneyBag 双AI（Claude + DeepSeek Pipeline V4.5）** vs **6家专业机构（招商/易方达/华龙/国泰海通/中信/证券市场周刊）** 的三方对比验证。

**AI 做对的（3.0/5 综合评分）**：
- ✅ 市场方向判断：B+（Claude"谨慎乐观"与招商/易方达吻合）
- ✅ 估值判断：A（45.1%百分位精确量化）
- ✅ 配置方向：B（宽基ETF+黄金+科技+红利与机构方向一致）
- ✅ 技术面分析：A-（RSI/MACD/布林带量化到位，机构研报通常不覆盖）
- ✅ 数据透明度：A（标出5个数据源不可用）
- ✅ 响应速度：A+（46秒 vs 机构研报1-2周）

**AI 做错的（致命盲点）**：
- ❌ **地缘政治：F** — 3月A股暴跌6-7%的元凶（美以伊冲突）完全未提及
- ❌ **能源/资源板块：F** — 油价100美元/桶背景下完全遗漏能源板块
- ❌ 产业链深度：D — 只到ETF级别，无法触及产业链细节
- ❌ 个股研判：D — 不推荐个股

### V6 要解决的 7 个问题

| # | 问题 | 优先级 | 对应模块 |
|---|------|--------|---------|
| 1 | AI 看不到地缘政治/重大事件 | 🔴 P0 | 模块A |
| 2 | AI 看不到原油/大宗商品价格 | 🔴 P0 | 模块B |
| 3 | 北向资金数据断了 + ETF资金流不准 | 🟡 P1 | 模块C |
| 4 | 只有指数级别，缺行业颗粒度 | 🟡 P1 | 模块D |
| 5 | AI 不知道机构在想什么 | 🟢 P2 | 模块E |
| 6 | 不能做"如果...会怎样"的情景分析 | 🟢 P2 | 模块F |
| 7 | 每次分析完结果就丢了 + Claude只能手动粘贴 + 没有多源对比 | 🔴 P0 | 模块G |

---

## 2. 架构总览

```
                          ┌─────────────────────────────────────────┐
                          │           _build_market_context()        │
                          │  (main.py:2017-2159, 注入 DeepSeek)     │
                          └──────────┬──────────────────────────────┘
                                     │ 读取
    ┌────────────────────────────────┼────────────────────────────────┐
    │                                │                                │
    ▼                                ▼                                ▼
┌──────────┐  ┌───────────────┐  ┌──────────────┐  ┌─────────────┐  ┌──────────┐
│ 模块A    │  │ 模块B         │  │ 模块C        │  │ 模块D       │  │ 模块E    │
│地缘事件  │  │原油+大宗扩展  │  │北向+ETF修复  │  │行业轮动     │  │研报摘要  │
│ NEW      │  │ EXTEND        │  │ FIX          │  │ NEW         │  │ NEW      │
└────┬─────┘  └──────┬────────┘  └──────┬───────┘  └─────┬───────┘  └────┬─────┘
     │               │                  │                │               │
     └───────┬───────┴────────┬─────────┴────────┬───────┘               │
             │                │                  │                       │
             ▼                ▼                  ▼                       │
      ┌──────────────────────────────────────────────┐                   │
      │  模块F: 情景分析引擎 (scenario_engine.py)     │ ◄────────────────┘
      │  "如果中东停火" / "如果油价突破120"            │
      └──────────────────────────────────────────────┘
             │
             ▼
      ┌──────────────┐     ┌────────────────┐     ┌──────────────┐
      │ Pipeline      │     │ signal.py      │     │ regime_engine │
      │ (仲裁时注入)  │     │ (第13维因子)   │     │ (地缘加权)   │
      └──────────────┘     └────────────────┘     └──────────────┘

      ┌──────────────────────────────────────────────┐
      │  模块G: 分析历史系统 (analysis_history.py)    │
      │  统一存档 / 多源对比 / Claude 自动录入        │
      └──────────────────────────────────────────────┘
```

---

## 3. 模块A：地缘政治/重大事件追踪 🔴 P0

### 问题
AI 完全没看到美以伊冲突、霍尔木兹海峡、油价100美元——这是3月A股暴跌6-7%的元凶。现有 `news_data.py` 的 `POLICY_KEYWORDS` 只有**被动关键词过滤**，没有主动追踪、严重性评估、持续时间感知。

### 设计

**新建文件**：`backend/services/geopolitical.py`

```python
MODULE_META = {
    "name": "geopolitical",
    "scope": "public",
    "input": [],
    "output": "geopolitical_risk",
    "cost": "llm_light",
    "tags": ["地缘", "风险事件", "黑天鹅"],
    "description": "地缘政治事件追踪+严重性评估+A股影响链",
    "layer": "data",
    "priority": 1,
}
```

**核心函数**：

| 函数 | 职责 | 数据源 | 缓存 |
|------|------|--------|------|
| `get_geopolitical_events()` | 抓取地缘新闻 + 分类 + 评级 | AKShare 新闻 + 关键词规则 | 30min |
| `assess_event_severity(events)` | DS 评估严重性 1-5 级 | DeepSeek V3 | 1h |
| `get_geopolitical_risk_score()` | 输出综合地缘风险分 0-100 | 规则 + DS | 30min |
| `get_geo_impact_on_sectors()` | 地缘→行业→持仓影响链 | 规则映射 | 30min |
| `enrich(ctx)` | Pipeline 注入 | 上面四个 | - |

**关键词体系**（扩展现有 POLICY_KEYWORDS）：

```python
GEO_EVENT_CATEGORIES = {
    "军事冲突": {
        "keywords": ["战争", "军事", "空袭", "导弹", "入侵", "开战", "停火",
                     "以色列", "伊朗", "中东", "俄乌", "台海", "朝鲜",
                     "霍尔木兹", "红海", "南海"],
        "base_severity": 4,
    },
    "制裁升级": {
        "keywords": ["制裁", "禁运", "封锁", "脱钩", "出口管制", "实体清单",
                     "芯片禁令", "技术封锁"],
        "base_severity": 3,
    },
    "能源危机": {
        "keywords": ["石油危机", "天然气", "能源安全", "断供", "管道",
                     "OPEC减产", "油价暴涨", "能源短缺"],
        "base_severity": 4,
    },
    "金融风险": {
        "keywords": ["银行倒闭", "债务危机", "主权违约", "资本外逃",
                     "汇率崩盘", "流动性危机"],
        "base_severity": 3,
    },
    "贸易摩擦": {
        "keywords": ["关税", "贸易战", "报复", "反倾销", "WTO"],
        "base_severity": 2,
    },
}
```

**地缘→行业影响映射表**：

```python
GEO_SECTOR_IMPACT = {
    "军事冲突": {
        "bullish": ["黄金", "军工", "石油", "债券"],
        "bearish": ["航空", "旅游", "消费", "科技"],
        "a_share_impact": "避险情绪升温，资金从成长转向防御",
    },
    "能源危机": {
        "bullish": ["石油", "煤炭", "新能源", "黄金"],
        "bearish": ["航空", "化工", "运输", "消费"],
        "a_share_impact": "输入性通胀压力，央行政策空间收窄",
    },
    # ...其他类别类似
}
```

**严重性评估逻辑**：

```
规则预筛（0 成本）：
  1. 关键词命中 → base_severity
  2. 多类别同时命中 → severity +1
  3. 连续3天出现 → severity +1（持续性）
  4. severity >= 3 → 调 DeepSeek 做精细评估

DS 精细评估（1 次 LLM）：
  prompt: "评估以下地缘事件对A股的影响，返回JSON:
           {severity:1-5, duration_days, affected_sectors[], a_share_impact_pct}"
  → 用 LLMGateway.call_sync(model_tier="llm_light")
```

### 接入点

| 接入位置 | 怎么接 | 影响 |
|---------|--------|------|
| `_build_market_context()` | 新增"地缘风险"段落 | DeepSeek 聊天/分析都能看到 |
| `signal.py` | 新增第13维因子"地缘面" | 综合信号评分纳入地缘 |
| `regime_engine.py` | severity≥4 时强制 regime=high_vol_bear | Pipeline 自动切 cautious 管线 |
| `pipeline_runner.py` | step_risk_firewall 读 geo risk score | severity=5 → 风控一票否决 |
| `cache_warmer.py` | warm_morning + warm_midday 预热 | 地缘新闻时效性高 |
| `stock_monitor_cron.py` | 扫描时检查地缘事件 | 有重大事件→企微推送 |
| `ds_enhance.py` | assess_news_risk 增加地缘维度 | 新闻风控更准 |

---

## 4. 模块B：原油/大宗商品扩展 🔴 P0

### 问题
`market_factors.py` 只有黄金(AU0)和铜(CU0)，**没有原油**——2026年3月油价从65涨到100美元，所有机构都在讨论能源冲击，AI 一个字没提。

### 设计

**修改文件**：`backend/services/market_factors.py`

**新增大宗商品**：

| 品种 | AKShare 接口 | symbol | 意义 |
|------|-------------|--------|------|
| 原油（上期能源） | `futures_main_sina` | `SC0` | A股能源链+通胀预期 |
| 布伦特原油（国际） | `futures_foreign_hist` | `BZ` | 国际油价基准 |
| 天然气 | `futures_main_sina` | `LU0` | 能源替代品 |
| 铁矿石 | `futures_main_sina` | `I0` | 钢铁/基建晴雨表 |
| 螺纹钢 | `futures_main_sina` | `RB0` | 基建活跃度 |

**新增函数**：

```python
def get_crude_oil_price() -> dict:
    """专门获取原油价格（国内SC + 国际布伦特）"""
    return {
        "sc": {"price": 750, "change_pct": 2.1, "unit": "元/桶"},
        "brent": {"price": 102, "change_pct": 1.5, "unit": "美元/桶"},
        "alert_level": "warning",  # normal/warning/crisis
        "vs_30d_avg": "+15%",
        "available": True,
    }

def get_commodity_impact_assessment() -> dict:
    """大宗商品价格→A股影响评估（纯规则，0 LLM）"""
    return {
        "oil_impact": "输入性通胀压力，航空化工承压，能源股受益",
        "metal_impact": "铜价回暖反映经济预期改善",
        "overall_tone": "bearish",
    }
```

**油价阈值配置**（加到 `config.py`）：

```python
OIL_BRENT_NORMAL = 80      # 美元/桶，正常区间
OIL_BRENT_WARNING = 100    # 警戒线
OIL_BRENT_CRISIS = 120     # 危机线
```

### 接入点

| 接入位置 | 怎么接 |
|---------|--------|
| `_build_market_context()` 大宗商品段 | 原来只有黄金/铜，加上原油/铁矿石，油价>100标红 |
| `get_all_market_factors()` | 返回值加 `crude_oil` 字段 |
| `NEWS_IMPACT_MAP` "大宗商品"规则 | 扩展 impact 描述，加入油价区间判断 |
| `signal.py` 宏观面因子 | 油价偏离度纳入宏观面评分 |
| 模块A 的 `get_geo_impact_on_sectors()` | 能源危机类事件 + 油价数据联动 |

### 模块A↔B 联动

```
模块A 检测到"能源危机"类事件 (severity≥3)
  → 自动调 模块B 的 get_crude_oil_price()
  → 如果 brent>100 → 触发联合警报: "地缘冲突+油价飙升"
  → 注入 _build_market_context(): "⚠️ 地缘+能源双重风险"
  → regime_engine 强制 cautious
```

---

## 5. 模块C：修复北向资金 + ETF资金流 🟡 P1

### 问题
- 北向资金：2024年8月起交易所不再披露每日净流入，代码已做降级处理但数据过时
- ETF资金流：AKShare 1.18.55 无专用接口，已用增长率排名替代，但不是真实资金流

### 设计

**修改文件**：`backend/services/factor_data.py`（北向）+ `backend/services/market_factors.py`（ETF）

**北向资金修复方案（三级降级）**：

```python
def get_northbound_flow() -> dict:
    """北向资金"""
    # 方案1: stock_hsgt_hist_em（官方，8月后可能不完整）
    # 方案2: 港交所数据 stock_hsgt_hold_stock_em（个股持仓变化推算）
    # 方案3: 沪港通/深港通成交额差值估算
    # 新增：从个股层面推算整体方向
```

**ETF 资金流修复方案（四级降级）**：

```python
def get_etf_fund_flow() -> dict:
    """ETF资金流"""
    # 方案A: fund_etf_fund_flow_em（真实流）
    # 方案B: fund_etf_spot_em（当日实时）
    # 方案C: fund_etf_fund_daily_em 增长率排名（当前已实现）
    # 方案D: ETF份额变化（fund_etf_hist_em 的份额列日差）
```

### 接入点
- 已有接入无需新增（`signal.py` 资金面 + `_build_market_context()` 已读北向和ETF）
- 修复后数据质量提升，下游自动受益

---

## 6. 模块D：行业轮动分析 🟡 P1

### 问题
目前只到"沪深300指数"级别，缺乏行业颗粒度。机构研报的核心是"配半导体/配新能源/配能源"这种行业级建议，AI做不到。

### 设计

**新建文件**：`backend/services/sector_rotation.py`

```python
MODULE_META = {
    "name": "sector_rotation",
    "scope": "public",
    "input": [],
    "output": "sector_data",
    "cost": "cpu",
    "tags": ["行业", "轮动", "板块", "资金流"],
    "description": "行业轮动分析：申万一级行业涨跌/资金流/动量排名",
    "layer": "data",
    "priority": 2,
}
```

**核心函数**：

| 函数 | 职责 | AKShare 接口 |
|------|------|-------------|
| `get_sector_performance()` | 31个申万一级行业近1/5/20日涨跌排名 | `stock_board_industry_name_em` + `stock_board_industry_hist_em` |
| `get_sector_fund_flow()` | 行业资金流向TOP5/BOTTOM5 | `stock_sector_fund_flow_rank` |
| `get_sector_momentum()` | 行业动量因子（20日涨幅 + 5日加速度） | 计算列 |
| `detect_rotation_pattern()` | 检测轮动模式（哪些板块在接力） | 规则引擎 |
| `get_hot_sectors()` | 当前热门板块（资金+涨幅+新闻三维） | 综合 |
| `enrich(ctx)` | Pipeline 注入行业数据 | - |

**轮动模式检测**：

```python
ROTATION_PATTERNS = {
    "防御转进攻": {
        "condition": "近5日 银行/公用事业下跌 AND 科技/军工上涨",
        "meaning": "资金从避险切换到进攻",
        "a_share_signal": "bullish",
    },
    "进攻转防御": {
        "condition": "近5日 科技/新能源下跌 AND 银行/煤炭上涨",
        "meaning": "资金从成长切换到防御",
        "a_share_signal": "bearish",
    },
    "全面普涨": {
        "condition": "近5日 >20个行业上涨",
        "meaning": "牛市信号",
        "a_share_signal": "strong_bullish",
    },
    "全面普跌": {
        "condition": "近5日 >20个行业下跌",
        "meaning": "熊市信号",
        "a_share_signal": "strong_bearish",
    },
}
```

### 接入点

| 接入位置 | 怎么接 |
|---------|--------|
| `_build_market_context()` | 新增"行业轮动"段落：TOP3涨幅行业 + 轮动模式 |
| `signal.py` | 新增第14维因子"行业面" |
| `regime_engine.py` | 全面普跌→高波熊市加分 |
| `ds_enhance.py` generate_daily_focus | 注入行业数据让"今日关注"更有料 |
| `portfolio.py` 配置建议 | 基于行业轮动推荐超配/低配行业 |
| `holding_intelligence.py` | 用户持仓所属行业是热门还是冷门 |
| 前端 | 新增"行业热力图"展示模块 |

### 跨模块联动

**A↔D 联动**：
```
模块A 检测到"制裁升级"→半导体行业
  → 模块D 检查半导体行业近期表现
  → 如果半导体已跌5% → "利空已部分消化"
  → 如果半导体还在涨 → "利空尚未反映，注意风险"
```

**B↔D 联动**：
```
模块B 检测到油价>100 + 持续上涨
  → 模块D 检查能源/煤炭行业排名
  → 如果能源行业TOP3 → "能源板块已热，追高需谨慎"
  → 如果能源行业不在TOP10 → "能源链尚未被市场充分定价，关注机会"
```

---

## 7. 模块E：券商研报摘要 🟢 P2

### 问题
AI不知道机构在想什么。用户需要"听听专业人士怎么说"的能力。

### 设计

**新建文件**：`backend/services/broker_research.py`

```python
MODULE_META = {
    "name": "broker_research",
    "scope": "public",
    "input": [],
    "output": "broker_views",
    "cost": "llm_light",
    "tags": ["研报", "券商", "策略观点"],
    "description": "主流券商策略观点摘要（月度+事件驱动）",
    "layer": "data",
    "priority": 4,
}
```

**数据源策略（由易到难）**：

| 优先级 | 数据源 | 可行性 | 说明 |
|--------|--------|--------|------|
| 1️⃣ | AKShare `stock_report_fund_hold_em` | ✅ 已有 | 机构持仓变化（间接推断观点）|
| 2️⃣ | 东方财富研报标题（AKShare 爬取） | 🔶 需验证 | `stock_news_em(symbol="研报")` |
| 3️⃣ | Web 搜索券商策略关键词 | ✅ WebSearch | "中信证券 4月策略 A股" |
| 4️⃣ | Tushare `report_rc`（需积分） | 🔶 备选 | 已有 tushare_data.py 框架 |

**核心函数**：

```python
def get_broker_consensus() -> dict:
    """获取主流券商策略共识"""
    return {
        "consensus": "谨慎乐观",
        "bullish_count": 4, "bearish_count": 1, "neutral_count": 2,
        "key_sectors": ["半导体", "新能源", "能源"],
        "key_risks": ["中东局势", "油价", "美联储"],
        "source_count": 7,
        "updated_at": "2026-04-15",
    }
```

### 接入点

| 接入位置 | 怎么接 |
|---------|--------|
| `_build_market_context()` | 新增"机构观点"段落 |
| `ds_enhance.py` 配置建议增强 | 对比AI建议 vs 机构共识的差异 |
| `cache_warmer.py` warm_morning | 早盘预热拉一次 |
| 企微推送 | 早报加一行"机构今日共识" |

---

## 8. 模块F：情景分析引擎 🟢 P2

### 问题
用户问"如果中东停火A股会怎样"——AI 没有这个能力。这是专业机构报告中"情景分析/压力测试"的核心功能。

### 设计

**新建文件**：`backend/services/scenario_engine.py`

```python
MODULE_META = {
    "name": "scenario_engine",
    "scope": "public",
    "input": ["scenario_type"],
    "output": "scenario_analysis",
    "cost": "llm_heavy",  # 需要 R1 深度推理
    "tags": ["情景", "假设", "压力测试", "What-if"],
    "description": "情景分析：给定假设条件→推演A股影响→给配置建议",
    "layer": "analysis",
    "priority": 5,
}
```

**预设情景模板（4 个即用型）**：

```python
PRESET_SCENARIOS = {
    "ceasefire": {
        "name": "中东停火",
        "assumptions": "美以伊达成停火协议，霍尔木兹海峡恢复正常通航",
        "affected_vars": ["oil_price:-30%", "gold:-5%", "risk_appetite:+"],
        "sector_impact": {"能源": "bearish", "航空": "bullish", "军工": "bearish", "消费": "bullish"},
    },
    "oil_120": {
        "name": "油价突破120",
        "assumptions": "布伦特原油突破120美元/桶，持续1个月以上",
        "affected_vars": ["oil_price:+20%", "cpi:+0.5%", "a_share:-5%"],
        "sector_impact": {"能源": "strong_bullish", "化工": "bearish", "航空": "strong_bearish"},
    },
    "fed_cut": {
        "name": "美联储意外降息",
        "assumptions": "美联储紧急降息50BP",
        "affected_vars": ["usd:-2%", "gold:+3%", "northbound:+", "a_share:+3%"],
        "sector_impact": {"科技": "bullish", "地产": "bullish", "银行": "neutral"},
    },
    "chip_ban": {
        "name": "芯片禁令升级",
        "assumptions": "美国扩大对华芯片出口限制范围",
        "affected_vars": ["tech_sentiment:-", "domestic_sub:+"],
        "sector_impact": {"半导体": "short_bearish_long_bullish", "国产替代": "bullish"},
    },
}
```

**核心函数**：

```python
def analyze_scenario(scenario_id: str, custom_text: str = "") -> dict:
    """情景分析主函数（消费所有其他模块数据 + R1 推理）"""
    return {
        "scenario": "中东停火",
        "probability": "30%",
        "market_impact": {"a_share": "+3~5%", "oil": "-25~30%", "gold": "-3~5%"},
        "sector_winners": ["航空", "消费", "旅游"],
        "sector_losers": ["能源", "军工", "黄金"],
        "portfolio_advice": "减持能源ETF，加仓消费ETF",
        "timeframe": "1-3个月",
    }
```

**API 端点（3个）**：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/scenario/{scenario_id}` | GET | 预设情景分析（**注意：需加 userId 参数**） |
| `/api/scenario/custom` | POST | 自定义情景分析 |
| `/api/scenarios` | GET | 列出所有预设情景 |

### 接入点

| 接入位置 | 怎么接 |
|---------|--------|
| 前端 | 新增"情景分析"Tab（选预设 or 输入自定义）|
| 企微 | "情景 中东停火" 快捷指令 |
| AI聊天 | 规则引擎识别"如果...会怎样" → 自动调用 |
| 不注入 market_context | 按需调用，不是常态数据 |

### 模块F 消费所有其他模块
```
情景分析 = f(
    当前地缘状态(A) + 当前油价/大宗(B) + 当前资金流向(C) +
    当前行业排名(D) + 当前机构观点(E) + 假设条件 + DeepSeek R1 推理
)
```

---

## 9. 模块G：分析历史 + 多源对比 + 自动录入 🔴 P0

> **V4 重写（2026-04-15）**：用户要求 **Claude 自动录入** + **每次分析都保留历史** + **能查看历史**。DeepSeek/机构同理。

### 问题（5个）

| # | 问题 | 来源 |
|---|------|------|
| 1 | 前端没调 3 个 analyze API | V2 识别 |
| 2 | 没有多源对比框架 | V2 识别 |
| 3 | **没有分析历史**——每次分析完结果就丢了 | V4 新增 |
| 4 | **Claude 只有手动粘贴**——应该自动录入 | V4 新增 |
| 5 | **机构观点没存档**——无法看"上周机构怎么说的" | V4 新增 |

### 核心架构：统一分析存档系统

所有来源的分析统一存到 `DATA_DIR/{userId}/analysis_history/`，格式一致，可查可比可追溯。

**存档目录结构**：
```
data/{userId}/analysis_history/
  ├── 20260415_1530_deepseek_stock.json   ← DeepSeek 股票分析（自动存）
  ├── 20260415_1545_claude_full.json      ← Claude 全量分析（自动推送）
  ├── 20260415_1600_broker_consensus.json ← 机构共识快照（定时存）
  └── ...（按日期排序，90天自动清理）
```

**统一记录格式（JSON）**：
```json
{
  "id": "20260415_1530_deepseek_stock",
  "source": "deepseek",
  "source_label": "DeepSeek V3",
  "type": "stock",
  "analysis": "分析正文...",
  "direction": "看多",
  "confidence": 72,
  "market_snapshot": {
    "sh_index": 3156,
    "oil_brent": 102,
    "gold": 2380,
    "fear_greed": 55
  },
  "created_at": "2026-04-15T15:30:00",
  "userId": "LeiJiang",
  "metadata": {}
}
```

### 后端改动

**新建 `backend/services/analysis_history.py`**：

| 函数 | 职责 |
|------|------|
| `save_analysis(userId, source, type, text, ...)` | 存一条分析记录 + 自动拍市场快照 |
| `get_analysis_history(userId, source?, type?, days=30)` | 查历史列表（分页/筛选） |
| `get_analysis_detail(userId, record_id)` | 查单条完整内容 |
| `get_latest_by_source(userId)` | 取各来源最新记录（对比视图用） |
| `_take_market_snapshot()` | 存档时自动保存当时的沪指/油价/黄金/恐贪 |
| `_cleanup_old_records(days=90)` | 自动清理过期记录 |

**改造现有 3 个 analyze 函数**（return 前自动存档）：

```python
# analyze_stock_holdings / analyze_fund_holdings / agent_analyze
from services.analysis_history import save_analysis
if result.get("source") == "ai":
    save_analysis(uid, "deepseek", "DeepSeek V3", "stock", result["analysis"])
```

**新增 5 个 API 端点**：

| 端点 | 方法 | 功能 | userId |
|------|------|------|--------|
| `/api/analysis/history` | GET | 查询分析历史列表 | ✅ 查询参数 |
| `/api/analysis/detail/{record_id}` | GET | 获取单条分析完整内容 | ✅ |
| `/api/analysis/latest` | GET | 各来源最新分析（对比视图） | ✅ |
| `/api/analysis/compare` | POST | 多源对比（可选强制刷新DS） | ✅ request body |
| `/api/analysis/external` | POST | 接收外部分析（Claude自动推送入口） | ✅ |

**`/api/analysis/external`（Claude 自动录入核心端点）**：

```python
@app.post("/api/analysis/external")
def receive_external_analysis(req: dict):
    uid = req.get("userId", "default")
    text = req.get("analysis", "")
    source = req.get("source", "claude")
    source_label = req.get("sourceLabel", "Claude")
    analysis_type = req.get("type", "full")
    direction = req.get("direction", "unknown")
    
    from services.analysis_history import save_analysis
    result = save_analysis(
        userId=uid, source=source, source_label=source_label,
        analysis_type=analysis_type, analysis_text=text,
        direction=direction,
    )
    return result
```

### Claude 录入方式（V4：自动优先）

| 方式 | 优先级 | 实现 | 阶段 |
|------|--------|------|------|
| **① WorkBuddy 自动推送** | **最高** | Claude 分析完自动 POST `/api/analysis/external`——你在 WorkBuddy 说"帮我分析持仓"，分析报告给你的同时自动推送到后端存档 | Phase 5 |
| ② 手动粘贴（兜底） | 并行做 | 前端"粘贴外部分析"按钮 → POST 同一个端点 | Phase 5 |
| ③ API Key 直调 | 后续 | 后端直接调 Claude API，不经过 WorkBuddy | ROI 低先不做 |

**WorkBuddy 自动推送完整链路**：
```
1. 你在 WorkBuddy 说"帮我分析一下持仓"
2. Claude 用 finance-data 拉实时数据 → 做分析 → 输出给你看
3. 同时自动调 POST http://150.158.47.189:8000/api/analysis/external
   body: {userId: "LeiJiang", source: "claude", analysis: "...", type: "full"}
4. 后端 analysis_history.save_analysis() 存档
5. 你打开钱袋子 App → "分析历史" → 能看到刚才 Claude 的分析
6. 点"对比" → 同时看 DeepSeek + Claude + 机构的最新分析
```

### 前端改动

**① 新增"分析历史"Tab**（Pro only）：

```
┌────────────────────────────────────────────────┐
│  分析历史                          [筛选]       │
├────────────────────────────────────────────────┤
│ 来源: [全部] [DeepSeek] [Claude] [机构]        │
│ 类型: [全部] [股票] [基金] [全量]              │
├────────────────────────────────────────────────┤
│ 2026-04-15                                     │
│ ┌──────────────────────────────────────────┐   │
│ │ DeepSeek 股票分析  15:30                 │   │
│ │ 方向: 看多 | 置信度: 72%                │   │
│ │ "整体持仓估值合理，技术面偏多..."        │   │
│ │                        [查看全文] [对比]  │   │
│ └──────────────────────────────────────────┘   │
│ ┌──────────────────────────────────────────┐   │
│ │ Claude 全量分析  15:45                   │   │
│ │ 方向: 谨慎                              │   │
│ │ "地缘风险是当前最大不确定性..."           │   │
│ │                        [查看全文] [对比]  │   │
│ └──────────────────────────────────────────┘   │
│ ┌──────────────────────────────────────────┐   │
│ │ 机构共识  16:00                          │   │
│ │ 方向: 谨慎乐观（4看多/1看空/2中性）      │   │
│ │                        [查看全文] [对比]  │   │
│ └──────────────────────────────────────────┘   │
│                                                │
│ 2026-04-14                                     │
│ └ ...                                          │
│                   [加载更多]                    │
└────────────────────────────────────────────────┘
```

**② 对比视图**（点"对比"触发）：

```
┌────────────────────────────────────────────────┐
│  多源分析对比                  [重新分析(DS)]   │
├────────────────────────────────────────────────┤
│  [DeepSeek] | [Claude] | [机构]                │
│  4/15 15:30   4/15 15:45  4/15 16:00           │
│ ──────────────────────────────────────────────  │
│  （当前 Tab 的分析全文，可滚动）               │
├────────────────────────────────────────────────┤
│  分歧汇总                                       │
│  DeepSeek: 看多 72%（估值+技术双支撑）         │
│  Claude:   谨慎（地缘风险尚未消化）             │
│  机构:     谨慎乐观（先抑后扬）                 │
│  关键分歧：地缘风险的定价程度                    │
├────────────────────────────────────────────────┤
│  分析时市场快照                                  │
│  沪指:3156 | 油价:$102 | 黄金:$2380 | 恐贪:55  │
└────────────────────────────────────────────────┘
```

**③ 持仓页新增入口**：
- 股票持仓页 → "请求深度分析"按钮（触发 DS analyze + 自动存档）
- 基金持仓页 → 同上
- 两个页面都有"粘贴外部分析"兜底按钮

**④ 首页 Dashboard**：
- 新增"最近分析"卡片 → 显示最近一次各来源分析的时间和方向
- 点击跳转到分析历史页

### 定时任务自动存档

| 已有定时任务 | V6 改造 |
|-------------|---------|
| `stock_monitor_cron.py` 15:30 收盘复盘 | 复盘调 `agent_analyze()` → 结果自动存档 |
| `cache_warmer.py` 9:15 早盘预热 | 顺便拉一次 `broker_consensus` → 存档 |
| **新增** | 每日 16:00 自动调 analyze_stock + analyze_fund → 双双存档 |

---

## 10. 模块关联矩阵

```
         A(地缘)  B(原油)  C(资金)  D(行业)  E(研报)  F(情景)  G(历史)
A(地缘)    —      A→B     ·       A→D      ·       A→F      ·
B(原油)   B→A      —      ·       B→D      ·       B→F      ·
C(资金)    ·       ·       —      C→D      ·       C→F      ·
D(行业)    ·       ·       ·       —       D↔E     D→F      ·
E(研报)    ·       ·       ·      D↔E       —      E→F      E→G
F(情景)    ·       ·       ·       ·        ·       —       F→G
G(历史)    ·       ·       ·       ·        ·       ·        —
```

**关键联动链**：

1. **地缘+油价 → 行业 → 情景**：中东冲突→油价飙升→能源行业暴涨→其他行业承压→自动触发"能源危机"情景分析
2. **资金+行业 → 信号**：北向流入+行业轮动到科技→综合信号偏多
3. **研报+行业 → 配置**：机构共识推荐半导体+AI检测到半导体行业TOP3→配置建议加权科技ETF
4. **所有模块 → 市场上下文**：`_build_market_context()` 汇聚所有模块一次性注入 DeepSeek
5. **所有分析 → 历史存档**：DS/Claude/机构的每次分析都自动存档到模块G

---

## 11. 各场景下的使用方式

### 场景1：用户打开首页

```
首页Dashboard:
  ├── 恐惧贪婪指数（已有）
  ├── 估值百分位（已有）
  ├── [NEW] 地缘风险指数: 75/100 (中东局势紧张)
  ├── [NEW] 油价: 布伦特 $102 (+1.5%) [警戒]
  ├── [NEW] 行业热点: 能源+4.2% / 军工+3.1% / 银行+1.5%
  ├── [NEW] 机构共识: 谨慎乐观 (4看多/1看空/2中性)
  ├── [NEW] 最近分析: DS 15:30 看多 | Claude 15:45 谨慎
  └── 今日关注（DS生成，现在有地缘+油价+行业数据，质量大幅提升）
```

### 场景2：用户问"现在适合入场吗"

```
_build_market_context() 注入:
  ├── 已有: 估值45%/恐贪55/RSI中性/MACD金叉
  ├── [NEW] 地缘: 中东冲突持续，severity=4/5，油价102美元
  ├── [NEW] 行业: 防御板块领涨(银行+3.9%)，进攻板块回调(科技-2.1%)
  ├── [NEW] 机构: 4家看先抑后扬，建议4月下旬布局
  └── DeepSeek 现在能说:
      "地缘风险是当前最大不确定性，建议等油价企稳后分批建仓"
      （之前只能说"估值合理，技术面中性"）
```

### 场景3：10分钟盯盘 cron 发现地缘事件

```
stock_monitor_cron.py:
  1. 扫描持仓（已有）
  2. [NEW] 检查地缘事件 → severity=5（重大军事冲突）
  3. [NEW] 检查油价 → 布伦特突破110
  4. [NEW] 检查持仓行业 → 用户持有航空股
  5. → 企微推送: "重大地缘风险! 油价110美元，你持有的XX航空可能承压"
```

### 场景4：收盘复盘

```
stock_monitor_cron.py --close:
  steward.review() 的 DecisionContext 现在包含:
  ├── [NEW] ctx.geopolitical_risk = {severity:4, events:[...]}
  ├── [NEW] ctx.crude_oil = {brent:102, alert:"warning"}
  ├── [NEW] ctx.sector_rotation = {pattern:"防御转进攻", hot:["银行","能源"]}
  └── R1 复盘: "今日市场受中东局势缓和预期影响，能源板块回落..."
  
  → 复盘结果自动存档到 analysis_history（模块G）
```

### 场景5：用户问"如果中东停火呢"

```
规则引擎匹配 "如果" → 调 scenario_engine:
  1. 加载 PRESET_SCENARIOS["ceasefire"]
  2. 获取当前状态: 油价102/地缘severity=4/能源行业TOP3
  3. R1 推演: 停火→油价回落到75-80→能源板块回调15-20%→消费航空反弹
  4. 对用户持仓的影响: 如持有能源ETF→建议减持30%
  5. 输出完整情景分析报告 → 自动存档到 analysis_history
```

### 场景6：WorkBuddy 分析完自动存档

```
用户在 WorkBuddy 说"帮我分析持仓":
  1. Claude 拉数据 → 分析 → 输出报告给用户
  2. 同时 POST http://150.158.47.189:8000/api/analysis/external
  3. 后端存档 → 用户打开 App "分析历史" Tab 可查看
  4. 点"对比" → 同时看 DeepSeek + Claude + 机构
```

---

## 12. 前后端对应审查结果

> 基于完整扫描 app.js（2890行/82个API端点）+ main.py（3262行/161个路由）+ routers/（13个端点）。

### 铁律#18 违规清单

**🔴 严重违规（有价值功能，前端 0 调用）— 9 个**：

| 后端 API | 功能 | V6 修复方案 |
|---------|------|------------|
| `POST /api/stock-holdings/analyze` | 股票深度分析 | **模块G：持仓页"请求深度分析"按钮** |
| `POST /api/fund-holdings/analyze` | 基金深度分析 | **模块G：持仓页"请求深度分析"按钮** |
| `POST /api/agent/analyze` | 全量AI分析 | **模块G：对比视图"重新分析"** |
| `POST /api/timing` | 择时信号 | 接入信号Tab |
| `POST /api/smart-dca` | 智能定投 | 接入管家Tab |
| `POST /api/take-profit` | 止盈建议 | 接入管家Tab |
| `GET /api/news/deep-impact` | 新闻深度影响 | 嵌入新闻详情 |
| `GET /api/news/risk-assess` | 新闻风险评估 | 嵌入新闻详情 |
| `GET /api/daily-signal/interpret` | 信号解读 | 注入信号Tab |

**🟡 中等违规 — 9 个**（独立端点/ML/batch/agent子端点等）

**🟢 低风险 — 9 个**（管理/调试/内部API，不需前端）

**⚠️ 前端Bug**：`POST /portfolio/transaction/delete` (app.js:L1026) vs 后端 `DELETE /api/portfolio/transaction/{tx_id}` — HTTP方法+路径都不匹配。

---

## 13. Simple/Pro 模式分配

| 功能 | Simple | Pro | 理由 |
|------|--------|-----|------|
| 首页 地缘风险指数卡片 | ✅ | ✅ | 所有人需看到重大风险 |
| 首页 油价卡片 | ✅ | ✅ | 直观信息 |
| 首页 行业热点卡片 | ✅ TOP3 | ✅ TOP5+详情 | Simple精简 |
| 首页 机构共识卡片 | ✅ 一句话 | ✅ 完整 | Simple精简 |
| 首页 最近分析卡片 | ❌ | ✅ | Pro功能 |
| 油价>100 警戒标红 | ✅ | ✅ | 风险提示 |
| 行业轮动 Tab | ❌ | ✅ 新增 | 专业功能 |
| 情景分析 Tab | ❌ | ✅ 新增 | 专业功能 |
| 分析历史 Tab | ❌ | ✅ 新增 | 专业功能 |
| 全量分析对比 | ❌ | ✅ 持仓页入口 | 只有Pro用 |
| 粘贴外部分析 | ❌ | ✅ | 只有Pro用 |
| AI聊天增强 | ✅ 自动受益 | ✅ | context注入，不走Tab |
| 企微推送 | ✅ | ✅ | 不区分模式 |

**Simple 白名单不需修改**——新 Tab 只加到 `all` 数组：
```javascript
// all 数组新增：
['sector','行业'], ['scenario','情景'], ['compare','对比'], ['analysis-history','分析']
// simple 数组保持不变：['overview','news','policy','doctor','steward']
```

---

## 14. 多账号（铁律#19）审查

**结论：基本合格，1 处 Gap**

- ✅ 前端 userId 已统一（`getProfileId()` 30+处 / `getUserId()` 18处 / `getProfileParam()` 8处，无冲突）
- ✅ 后端 ~60+ 路由有 userId 参数，~65 个公共路由不需要
- ✅ 模块 A-E（公共市场数据）+ 模块 G（已有userId）覆盖正常
- ⚠️ **模块F Gap**：`GET /api/scenario/{id}` 设计中没有 userId 参数，但内部会调 `analyze_stock_holdings(userId)` — **需改为 `GET /api/scenario/{id}?userId=xxx`**

---

## 15. 铁律检查点矩阵

### 每条铁律在 V6 中的绑定关系

| 铁律# | 铁律内容 | V6 中何时触发 | 检查方法 |
|:---:|---------|-------------|---------|
| 1 | 绝不用正则做批量重构 | 修改 main.py、signal.py 时 | 所有改动用 replace_in_file，禁止脚本 |
| 2 | 改代码前确认可回滚 | **每个 Phase 开始前** | 列文件清单 → git commit checkpoint |
| 3 | 改完一个文件立即验证 | **每改一个文件后** | read_file 重读 + import 语法检查 |
| 4 | 超过2轮修不好就停 | 遇到接口不可用时 | 2 次降级失败 → 停下来标记降级 |
| 5 | linter ≠ 编译器 | 每个 Phase 完成后 | 部署服务器 → 检查日志 → 调真实 API |
| 6 | 涉及技术深度先查参考 | 行业轮动算法、情景模型 | 先搜业界成熟方案 |
| 7 | 方案要有出处 | 油价阈值、行业分类标准 | 引用 IEA/Bloomberg/申万 |
| 8 | 最小可用版本先交 | 每个 Phase 定义 | Phase 1 数据获取 → 验证 → Phase 2 分析 |
| 9 | 超出能力范围就说 | 券商研报爬取 | AKShare 没接口就直说 |
| 10 | 记忆 ≠ 事实 | AKShare 接口名 | 每次调接口前先验证确实存在 |
| 12 | 改完必须只读验证 | 每改一个文件后 | read_file + 检查关联 import |
| 13 | "不能"=穷尽所有选项 | 数据源降级 | 北向3级/ETF4级/原油2级降级 |
| **18** | **后端做了前端必须接** | **每个新 API** | 写后端后同 Phase 写前端，grep 确认 |
| **19** | 多ID系统统一入口 | 用户身份相关 | 统一用 userId 参数 |
| **20** | 推送先查格式兼容性 | 地缘事件推送企微 | 用 text_card 格式 |

### 实施护栏（硬性规则）

```
✅ 开始 Phase N 前：
   1. git commit -m "checkpoint before phase N" （你确认）
   2. 列出本 Phase 要改的文件清单
   3. 你确认"可以开始"

✅ 每改一个文件后：
   1. read_file 重读确认写入正确
   2. python -c "import ..." 语法验证

✅ Phase N 完成后：
   1. 部署到服务器
   2. curl 调真实 API 验证返回格式
   3. 如果有前端改动 → 浏览器打开确认能看到
   4. git commit + push

❌ 禁止：
   - 攒多个文件改动最后一起验证（违反铁律#3）
   - 写了后端 API 但不写前端调用（违反铁律#18）
   - 用 PowerShell 脚本做代码批量替换（违反铁律#1）
   - 接口报错超过 2 轮还在硬修（违反铁律#4）

✅ 代码组织约束（防止 main.py 膨胀）：
   - 新 API 端点必须放 routers/ 目录（如 routers/scenario.py、routers/analysis_history.py），
     main.py 只加一行 app.include_router()
   - _build_market_context() 只做编排循环，具体数据拉取逻辑在各模块的 enrich(ctx) 方法里
   - 每个 Phase 结束时检查 main.py 行数，增长超过 50 行必须拆
```

---

## 16. 逐 Phase 验证清单

### Phase 1 验证：P0 核心数据层

| 步骤 | 验什么 | 怎么验 | 通过标准 | 失败处理 |
|------|--------|--------|---------|---------|
| 1.1 | geopolitical.py 关键词匹配 | `python -c "from services.geopolitical import get_geopolitical_events; print(...)"` | 返回 dict 且有 events 列表 | 检查 AKShare 新闻接口 |
| 1.2 | 原油 SC0/BZ 数据 | `python -c "from services.market_factors import get_crude_oil_price; print(...)"` | `available=True`, price > 0 | 降级到只用 SC0 |
| 1.3 | market_context 注入 | `curl /api/decision-data` 检查文本 | 包含"地缘"和"原油" | 检查注入逻辑 |
| 1.4 | config.py 阈值 | `python -c "from config import *; print(...)"` | 输出 80 100 120 | 语法检查 |
| 1.5 | 线上部署 | `systemctl restart + journalctl + curl` | 200 + crude_oil 字段 | 检查 requirements |

**端到端验证脚本**：
```bash
curl -s http://localhost:8000/api/decision-data | python3 -c "
import json,sys; d=json.load(sys.stdin); ctx=str(d)
assert '地缘' in ctx or 'geopolitical' in ctx, '缺少地缘数据'
assert '原油' in ctx or 'crude' in ctx or 'oil' in ctx, '缺少原油数据'
print('Phase 1 验证通过')
"
```

### Phase 2 验证：P0 分析层集成

| 步骤 | 验什么 | 怎么验 | 通过标准 |
|------|--------|--------|---------|
| 2.1 | DS 严重性评估 | 构造假新闻 → `assess_event_severity()` | 返回 severity(1-5) |
| 2.2 | signal 第13维 | `/api/daily-signal` | factors 含"地缘面" |
| 2.3 | regime 地缘加权 | 模拟 severity=4 | 返回 high_vol_bear |
| 2.4 | pipeline 风控 | 模拟 severity=5 | 含"风控否决" |
| 2.5 | NEWS_IMPACT_MAP | 输入地缘新闻 | impacts 有地缘影响 |
| 2.6 | cron 预热 | `python cache_warmer.py --once morning` | 日志显示 geo 预热成功 |
| 2.7 | **AI聊天能提到地缘** | 问"最大风险是什么" | 回复含地缘/油价 |

**端到端验证**：
```bash
curl -X POST http://localhost:8000/api/chat \
  -d '{"message":"目前市场最大的风险是什么？","userId":"default"}' \
  | python3 -c "
import json,sys; d=json.load(sys.stdin); r=d.get('reply','')
found=[k for k in ['地缘','冲突','油价','能源','中东'] if k in r]
print(f'关键词: {found}')
if len(found)>=2: print('Phase 2 验证通过')
else: print('AI 可能还没看到地缘数据')
"
```

### Phase 3 验证：P1 行业+资金修复

| 步骤 | 验什么 | 通过标准 |
|------|--------|---------|
| 3.1 | sector_rotation 31行业 | ≥20 个行业有数据 |
| 3.2 | 北向资金修复 | `available=True` + 有净流入数据 |
| 3.3 | ETF份额变化 | 有 share_change 字段 |
| 3.4 | market_context 行业段 | 聊天能说具体行业 |
| 3.5 | 首页行业卡片 | 浏览器看到卡片（**铁律#18**） |

### Phase 4 验证：P2 研报+情景

| 步骤 | 验什么 | 通过标准 |
|------|--------|---------|
| 4.1 | broker_research | consensus 字段返回正常 |
| 4.2 | scenario_engine | 4个预设都能跑通 |
| 4.3 | 情景 API 路由 | `/api/scenarios` 返回4个 |
| 4.4 | 企微快捷指令 | 企微收到分析推送 |
| 4.5 | 前端情景Tab | 4个预设+自定义输入框（**铁律#18**） |
| 4.6 | 全量验证 | "如果中东停火对我持仓影响"→含油价/行业/持仓 |

### Phase 5（模块G）验证

| 步骤 | 验什么 | 通过标准 |
|------|--------|---------|
| G.1 | 存档模块存/取 | save + get 返回列表含刚存记录 |
| G.2 | DS自动存档 | analyze 后目录下有 JSON |
| G.3 | 外部录入端点 | POST → ok=True + 文件存在 |
| G.4 | 历史查询+筛选 | records 列表按时间倒序 |
| G.5 | 对比视图 | sources ≥1 项 |
| G.6 | 前端历史Tab | 按日期分组显示卡片 |
| G.7 | 前端对比 | 3个Tab + 分歧汇总 |
| G.8 | **铁律#18** | grep app.js 含 ≥5 处 analysis/ 调用 |
| G.9 | 90天清理 | 91天前文件被删 |

---

## 17. 实施计划（5 Phase / 8-12天）

### Phase 1（1-2天）：🔴 P0 核心数据层

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 1.0 | checkpoint | git | 5min | #2 | git status 干净 |
| 1.1 | 新建 geopolitical.py | 新建 | 2h | #6 | import + 调用测试 |
| 1.2 | 扩展 get_commodity_prices() | market_factors.py | 1h | #3 #10 | 有原油数据 |
| 1.3 | _build_market_context 加地缘+原油 | main.py | 0.5h | #3 #12 | /api/decision-data |
| 1.4 | config.py 油价阈值 | config.py | 0.2h | #7 | print 验证 |
| 1.5 | 部署+线上验证 | deploy | 0.5h | #5 | 端到端脚本 |

### Phase 2（1-2天）：🔴 P0 分析层集成

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 2.0 | checkpoint | git | 5min | #2 | 确认 |
| 2.1 | geopolitical.py 加 DS 评估 | geopolitical.py | 1.5h | #4 | 假新闻→评估 |
| 2.2 | signal.py 第13维 | signal.py | 1h | #1 #3 | /api/daily-signal |
| 2.3 | regime_engine 地缘加权 | regime_engine.py | 0.5h | #3 | severity=4 测试 |
| 2.4 | pipeline 风控加地缘 | pipeline_runner.py | 0.5h | #3 | severity=5 测试 |
| 2.5 | NEWS_IMPACT_MAP 扩展 | news_data.py | 0.5h | #3 | 关键词测试 |
| 2.6 | cron 预热+推送 | scripts/ | 1h | #20 | 企微格式 |
| 2.7 | 部署+验证 | deploy | 0.5h | #5 | 端到端脚本 |

### Phase 3（1-2天）：🟡 P1 行业+资金修复

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 3.0 | checkpoint | git | 5min | #2 | 确认 |
| 3.1 | 新建 sector_rotation.py | 新建 | 2h | #6 | ≥20行业 |
| 3.2 | 北向资金多级降级 | factor_data.py | 1.5h | #4 #13 | 3级逐个验证 |
| 3.3 | ETF份额变化 | market_factors.py | 1h | #3 | share_change |
| 3.4 | market_context 加行业 | main.py | 0.5h | #18 | 聊天说具体行业 |
| 3.5 | 前端行业热点卡片 | app.js | 1h | #18 | 浏览器看到 |
| 3.6 | 部署+验证 | deploy | 0.5h | #5 | Phase 3 清单 |

### Phase 4（2-3天）：🟢 P2 研报+情景

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 4.0 | checkpoint | git | 5min | #2 | 确认 |
| 4.1 | 新建 broker_research.py | 新建 | 2h | #10 | 共识返回正常 |
| 4.2 | 新建 scenario_engine.py | 新建 | 3h | #6 #4 | 4预设跑通 |
| 4.3 | main.py 加情景 API | main.py | 0.5h | #3 #12 | /api/scenarios 200 |
| 4.4 | 企微"情景 XX"指令 | wxwork.py | 0.5h | #20 | 收到推送 |
| 4.5 | 前端情景 Tab | app.js | 2h | **#18** | 浏览器能用 |
| 4.6 | 部署+全量验证 | deploy | 0.5h | #5 | Phase 4 清单 |

### Phase 5（2天）：模块G 分析历史+自动录入

| # | 任务 | 文件 | 工时 | 铁律 | 验证 |
|---|------|------|------|------|------|
| 5.0 | checkpoint | git | 5min | #2 | 确认 |
| 5.1 | 新建 analysis_history.py | 新建 | 1.5h | #3 #12 | save/get 测试 |
| 5.2 | 改造 3 个 analyze 自动存档 | main.py | 0.5h | #3 | 调 analyze → 有文件 |
| 5.3 | 5 个 API 端点 | main.py | 1h | #3 #12 | curl 逐个验证 |
| 5.4 | 前端"分析历史" Tab | app.js | 2h | **#18** | 浏览器看到列表 |
| 5.5 | 前端"对比视图"+分歧汇总 | app.js | 1.5h | **#18** | Tab切换+汇总 |
| 5.6 | 持仓页入口按钮 | app.js | 1h | **#18** | 两个入口能用 |
| 5.7 | WorkBuddy 自动推送 | 配置 | 0.5h | — | 分析完后端自动收到 |
| 5.8 | cron 改造定时存档 | scripts/ | 0.5h | #3 | 收盘后有新存档 |
| 5.9 | 部署+验证 | deploy | 0.5h | #5 | G.1-G.9 全部通过 |

### 总工时

| Phase | 内容 | 工时 |
|-------|------|------|
| Phase 1 | P0 数据层 | 1-2天 |
| Phase 2 | P0 分析层 | 1-2天 |
| Phase 3 | P1 行业+资金 | 1-2天 |
| Phase 4 | P2 研报+情景 | 2-3天 |
| Phase 5 | 分析历史+自动录入 | 2天 |
| **总计** | **7 个模块** | **8-12天** |

---

## 18. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| AKShare 原油接口不可用 | 中 | 模块B降级 | 降级：从新闻提取油价/用国内SC0替代 |
| 地缘新闻被关键词过滤遗漏 | 中 | 模块A漏报 | DS定期扫描全量新闻做补充 |
| 行业数据API变更 | 低 | 模块D不可用 | 多接口降级链 |
| LLM调用成本增加 | 中 | 超日限50次 | severity≥3才调DS，其余纯规则 |
| 服务器2C2G内存不足 | 低 | 行业数据量大 | 只缓存TOP10+BOTTOM10 |
| 券商研报数据源不稳定 | 中 | 模块E降级 | WebSearch兜底 |
| analysis_history 文件膨胀 | 低 | 磁盘满 | 90天自动清理 |

---

## 19. 预期效果

| 维度 | 改进前（3.0/5） | 改进后预期 |
|------|-----------------|-----------|
| 地缘政治 | F（完全空白） | B+（能识别+评级+推送） |
| 能源/大宗 | F（没有原油） | A-（油价+多品种+影响评估） |
| 资金流向 | D（数据断了） | B（修复+多级降级） |
| 行业深度 | D（只有指数） | B+（31行业轮动+热点） |
| 机构观点 | F（没有） | B（每日共识摘要） |
| 情景分析 | F（没有） | A-（预设+自定义+R1推理） |
| 分析存档 | F（每次丢失） | A（统一存档+90天+对比） |
| Claude录入 | F（手动粘贴） | A（WorkBuddy自动推送） |
| **综合评分** | **3.0/5** | **4.2~4.5/5** |

> 从"快速量化参考"升级为"准专业级 AI 投研助手"。
> 
> **最大的三个改变**：
> 1. **AI 终于能看到"房间里的大象"** — 地缘政治和能源危机不再是盲区
> 2. **每次分析都有据可查** — 不再"用完即弃"，支持历史回溯和多源交叉验证
> 3. **Claude 从"外人"变成"住家" ** — 自动推送存档，与 DeepSeek/机构形成三足鼎立的分析矩阵

---

*文档编写: 2026-04-15*
*版本历程: V1(17:30 六模块初版) → V2(18:30 模块G+铁律矩阵+验证方案) → V3(18:41 前后端审查+Simple/Pro+多账号) → V4(18:46 分析历史+Claude自动录入) → V4-Final(19:10 整合完整版)*
*基于完整代码库分析: main.py 3262行 + 40+ services + app.js 312KB (2890行/82个API)*
*配套报告: `双AI投资分析对比报告.md` / `AI分析vs专业机构对比报告.md`*


---

# Part 3：V6.5 — 盈利预测模块设计

> 📄 原始文件：`docs/v6.5-earnings-forecast-design.md`
> 🎯 目标：盈利预测 + 估值定价 + 业务敞口
> ⏱️ 工时：9-10 天

# MoneyBag V6.5 盈利预测模块设计方案

> **版本**：V6.5-Draft
> **日期**：2026-04-15
> **触发**：领导反馈 + Tushare 5000积分升级完成
> **目标**：补齐"盈利预测"这个研报核心模块，从 3.0/5 提升到 4.2~4.5/5

---

## 📊 数据源验证（已完成）

### 5000 积分解锁的核心接口

| 接口 | api_name | 数据内容 | 验证状态 |
|------|----------|----------|----------|
| **券商盈利预测** | `report_rc` | 机构预测的营收/净利润/EPS/PE/ROE/目标价/评级 | ✅ 已测试（茅台数据正常返回） |
| 业绩预告 | `forecast` | 公司自己发布的业绩预增/预减/扭亏等 | ✅ 2000积分已有 |
| 业绩快报 | `express` | 正式财报前的快报 | ✅ 2000积分已有 |
| 财务指标 | `fina_indicator` | ROE/毛利率/成长性等100+指标 | ✅ 2000积分已有 |
| 主营业务构成 | `fina_mainbz` | 按产品/地区/行业分类的收入构成 | ✅ 2000积分已有 |

### `report_rc` 返回字段详解（核心！）

```json
{
  "ts_code": "600519.SH",
  "name": "贵州茅台",
  "report_date": "20260410",
  "org_name": "华泰证券",
  "quarter": "2026Q4",
  "op_rt": 9136563.489,     // 预测营业收入（万元）
  "np": 9136563.489,        // 预测净利润（万元）
  "eps": 72.96,             // 预测每股收益（元）
  "pe": 19.87,              // 预测市盈率
  "roe": 32.5,              // 预测ROE
  "rating": "买入",          // 券商评级（买入/增持/中性/减持/卖出）
  "max_price": 1900.0,      // 最高目标价
  "min_price": 1824.0       // 最低目标价
}
```

---

## 🎯 三大模块设计

### 模块 H：盈利预测聚合（🔴 P0）

**职责**：汇总多家券商的盈利预测，计算一致预期

**新建文件**：`backend/services/earnings_forecast.py`

```python
MODULE_META = {
    "name": "earnings_forecast",
    "scope": "public",
    "input": ["stock_basic"],
    "output": "earnings_consensus",
    "cost": "api_medium",
    "tags": ["盈利预测", "一致预期", "研报"],
    "description": "券商盈利预测聚合 + 一致预期计算",
    "layer": "data",
    "priority": 2,
}
```

**核心函数**：

| 函数 | 职责 | 数据源 | 缓存 |
|------|------|--------|------|
| `get_stock_forecast(ts_code)` | 获取单只股票的券商预测 | Tushare `report_rc` | 24h |
| `get_consensus_eps(ts_code)` | 计算一致预期 EPS | 聚合计算 | 24h |
| `get_target_price_range(ts_code)` | 目标价区间 | 聚合计算 | 24h |
| `get_rating_distribution(ts_code)` | 评级分布（多少家买入/增持...） | 聚合计算 | 24h |
| `get_index_consensus(index_code)` | 指数成分股的加权一致预期 | 批量聚合 | 24h |
| `enrich(ctx)` | Pipeline 注入 | 上面全部 | - |

**一致预期计算逻辑**：

```python
def calculate_consensus(forecasts: List[dict]) -> dict:
    """
    输入：多家券商对同一股票的预测列表
    输出：一致预期数据
    """
    # 按预测季度分组
    by_quarter = group_by(forecasts, 'quarter')
    
    result = {}
    for quarter, items in by_quarter.items():
        # 只取最近30天内的报告（过期的不算）
        recent = [f for f in items if is_within_days(f['report_date'], 30)]
        if not recent:
            recent = items[-5:]  # 没有近期的就取最新5份
        
        result[quarter] = {
            'eps_avg': mean([f['eps'] for f in recent if f['eps']]),
            'eps_high': max([f['eps'] for f in recent if f['eps']]),
            'eps_low': min([f['eps'] for f in recent if f['eps']]),
            'np_avg': mean([f['np'] for f in recent if f['np']]),  # 净利润均值
            'roe_avg': mean([f['roe'] for f in recent if f['roe']]),
            'target_price_avg': mean([f['min_price'] or f['max_price'] for f in recent if f['min_price'] or f['max_price']]),
            'target_price_high': max([f['max_price'] for f in recent if f['max_price']]),
            'target_price_low': min([f['min_price'] for f in recent if f['min_price']]),
            'org_count': len(set(f['org_name'] for f in recent)),  # 覆盖机构数
            'rating_dist': count_ratings(recent),  # {'买入': 5, '增持': 2, ...}
            'consensus_rating': get_consensus_rating(recent),  # 一致评级
        }
    
    return result
```

---

### 模块 I：估值定价（🟡 P1）

**职责**：基于盈利预测计算目标价、潜在空间、估值合理性

**新建文件**：`backend/services/valuation_engine.py`

```python
MODULE_META = {
    "name": "valuation_engine",
    "scope": "public",
    "input": ["earnings_forecast", "daily_basic"],
    "output": "valuation_assessment",
    "cost": "compute_light",
    "tags": ["估值", "目标价", "定价"],
    "description": "基于一致预期的估值定价引擎",
    "layer": "analysis",
    "priority": 3,
}
```

**核心函数**：

| 函数 | 职责 | 算法 |
|------|------|------|
| `calc_forward_pe(ts_code)` | 动态市盈率（基于预测EPS） | 现价 / 预测EPS |
| `calc_peg(ts_code)` | PEG（市盈率相对盈利增长） | PE / 盈利增速 |
| `calc_upside(ts_code)` | 潜在上涨空间 | (目标价 - 现价) / 现价 |
| `assess_valuation(ts_code)` | 估值合理性评估 | 综合打分 |
| `enrich(ctx)` | Pipeline 注入 | - |

**估值评估输出**：

```python
{
    "ts_code": "600519.SH",
    "current_price": 1450.0,
    "forward_pe": 19.9,           # 基于预测EPS的PE
    "trailing_pe": 25.2,          # 基于历史EPS的PE（现有）
    "pe_percentile": 45.1,        # PE历史百分位（现有）
    "peg": 1.32,                  # PEG
    "target_price_consensus": 1824.0,
    "upside_pct": 25.8,           # 潜在上涨空间
    "valuation_signal": "偏低",    # 偏高/合理/偏低
    "confidence": "高",           # 高（覆盖机构多）/ 中 / 低
    "org_count": 15,              # 覆盖机构数
}
```

---

### 模块 J：业务敞口分析（🟡 P1）

**职责**：分析上市公司的出口敞口、区域收入分布，与地缘模块联动

**新建文件**：`backend/services/business_exposure.py`

```python
MODULE_META = {
    "name": "business_exposure",
    "scope": "public",
    "input": ["fina_mainbz"],
    "output": "exposure_analysis",
    "cost": "api_light",
    "tags": ["业务敞口", "出口", "区域"],
    "description": "业务敞口分析（出口/区域/产品线）",
    "layer": "data",
    "priority": 4,
}
```

**核心函数**：

| 函数 | 职责 | 数据源 |
|------|------|--------|
| `get_revenue_by_region(ts_code)` | 按地区的收入分布 | Tushare `fina_mainbz` (type='D') |
| `get_revenue_by_product(ts_code)` | 按产品线的收入分布 | Tushare `fina_mainbz` (type='P') |
| `calc_export_exposure(ts_code)` | 出口敞口占比 | 海外收入 / 总收入 |
| `get_geo_vulnerability(ts_code)` | 地缘脆弱性（与模块A联动） | 出口区域 × 地缘风险 |
| `enrich(ctx)` | Pipeline 注入 | - |

**输出示例**：

```python
{
    "ts_code": "000858.SZ",  # 五粮液
    "revenue_by_region": {
        "境内": {"amount": 58000000000, "pct": 92.5},
        "境外": {"amount": 4700000000, "pct": 7.5},
    },
    "export_exposure": 7.5,  # 出口占比
    "geo_vulnerability": "低",  # 出口少，地缘影响小
    "vulnerable_to": [],  # 无特定地缘风险敞口
}

{
    "ts_code": "002475.SZ",  # 立讯精密
    "revenue_by_region": {
        "境内": {"amount": 12000000000, "pct": 18.2},
        "境外": {"amount": 54000000000, "pct": 81.8},
    },
    "export_exposure": 81.8,  # 出口占比极高
    "geo_vulnerability": "高",
    "vulnerable_to": ["美国制裁", "供应链脱钩"],
}
```

---

## 📐 架构整合

### 模块关联矩阵（更新）

```
                   新增模块
                      │
    ┌─────────────────┼─────────────────┐
    │                 │                 │
    ▼                 ▼                 ▼
┌──────────┐  ┌───────────────┐  ┌──────────────┐
│ 模块H    │  │ 模块I         │  │ 模块J        │
│盈利预测  │─►│估值定价引擎   │  │业务敞口      │
│ NEW      │  │ NEW           │  │ NEW          │
└────┬─────┘  └──────┬────────┘  └──────┬───────┘
     │               │                  │
     │               │                  │
     ▼               ▼                  ▼
┌────────────────────────────────────────────────┐
│              _build_market_context()            │
│  ├── 原有数据（估值/技术面/宏观/资金流）          │
│  ├── V6 新增（地缘/原油/行业轮动）               │
│  └── V6.5 新增（盈利预测/估值定价/业务敞口）      │
└────────────────────────────────────────────────┘
                      │
                      ▼
               ┌──────────────┐
               │ DeepSeek V3  │
               │ 管家分析报告 │
               └──────────────┘
```

### Pipeline 注入点

在 `main.py` 的 `_build_market_context()` 函数中新增：

```python
# === V6.5 盈利预测模块 ===
if include_earnings_forecast:
    try:
        from services.earnings_forecast import get_index_consensus
        from services.valuation_engine import assess_index_valuation
        from services.business_exposure import get_sector_exposure
        
        # 沪深300一致预期
        hs300_consensus = get_index_consensus("000300.SH")
        context_parts.append(f"""
【盈利预测 - 一致预期】
沪深300成分股机构预测（覆盖{hs300_consensus['org_count']}家券商）：
- 2026年预测净利润增速：{hs300_consensus['np_growth']}%
- 一致预期EPS增速：{hs300_consensus['eps_growth']}%
- 平均目标上涨空间：{hs300_consensus['avg_upside']}%
- 一致评级分布：买入{hs300_consensus['buy_pct']}% / 增持{hs300_consensus['hold_pct']}% / 中性{hs300_consensus['neutral_pct']}%
""")
        
        # 估值定价
        valuation = assess_index_valuation("000300.SH")
        context_parts.append(f"""
【估值定价】
- 动态PE（Forward PE）：{valuation['forward_pe']}x
- PEG：{valuation['peg']}
- 相对历史估值：{valuation['percentile_desc']}
- 估值信号：{valuation['signal']}（{valuation['confidence']}置信度）
""")
    except Exception as e:
        logger.warning(f"盈利预测模块异常: {e}")
```

---

## 🖥️ 前端展示设计

### Simple 模式（老婆/小白用户）

**只展示结论，不展示过程**

```
┌─────────────────────────────────────────────────┐
│  📈 机构看法                                      │
├─────────────────────────────────────────────────┤
│                                                  │
│  15家券商研究后认为：                             │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  🎯 目标：还能涨 25.8%                     │   │
│  │     （从 1,450 元到 1,824 元）             │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  👍 14家说"买入/增持"   👎 1家说"观望"           │
│                                                  │
│  💡 说人话：机构普遍看好，可以继续持有            │
│                                                  │
└─────────────────────────────────────────────────┘
```

### Pro 模式（专业用户）

**完整数据展示**

```
┌─────────────────────────────────────────────────────────────────┐
│  📊 盈利预测 & 估值定价                                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  【一致预期】覆盖机构：15家                                        │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 指标          │ 2025E     │ 2026E     │ 2027E     │ YoY    │ │
│  ├────────────────────────────────────────────────────────────┤ │
│  │ 营业收入(亿)  │ 1,580     │ 1,720     │ 1,850     │ +8.9%  │ │
│  │ 净利润(亿)    │ 895       │ 980       │ 1,050     │ +9.5%  │ │
│  │ EPS(元)      │ 71.53     │ 78.24     │ 83.81     │ +9.4%  │ │
│  │ ROE(%)       │ 32.5      │ 33.1      │ 33.8      │ +2.1%  │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  【估值定价】                                                     │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 当前价格      │ ¥1,450                                     │ │
│  │ 目标价(均值)  │ ¥1,824    │ 潜在空间 +25.8%               │ │
│  │ 目标价(区间)  │ ¥1,680 ~ ¥1,950                           │ │
│  ├────────────────────────────────────────────────────────────┤ │
│  │ Trailing PE  │ 25.2x     │ 历史百分位 45%                 │ │
│  │ Forward PE   │ 19.9x     │ 基于2026E EPS                  │ │
│  │ PEG          │ 1.32      │ <1偏低, 1-2合理, >2偏高        │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  【评级分布】                                                     │
│  买入 ████████████ 60% (9家)                                     │
│  增持 ██████ 33% (5家)                                           │
│  中性 █ 7% (1家)                                                 │
│                                                                  │
│  【估值信号】 🟢 偏低（Forward PE 低于历史均值，PEG 合理）          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### AI 分析报告中的整合

**Before（现在的报告）**：
```
## 估值判断
沪深300 PE 12.5x，历史百分位 45%，估值中性偏低。
```

**After（加入盈利预测后）**：
```
## 估值判断
沪深300：
- 当前 PE：12.5x（历史百分位 45%，中性偏低）
- 动态 PE（Forward）：11.2x（基于机构一致预期 EPS）
- 机构一致预期：2026年盈利增速 +11.5%
- PEG：1.09（合理区间）
- 平均目标上涨空间：+15.2%

📊 **15家券商一致预期**：
- 买入/增持评级占比：87%
- 目标价区间：3,650 ~ 4,200 点

💡 **结论**：估值便宜 + 盈利预期向上 = 潜在戴维斯双击机会
```

---

## 🔧 API 新增

### `/api/earnings/consensus/{ts_code}`

**功能**：获取单只股票的一致预期

**返回**：
```json
{
  "ts_code": "600519.SH",
  "name": "贵州茅台",
  "current_price": 1450.0,
  "consensus": {
    "2025E": {"eps": 71.53, "np": 89500000000, "roe": 32.5},
    "2026E": {"eps": 78.24, "np": 98000000000, "roe": 33.1},
    "2027E": {"eps": 83.81, "np": 105000000000, "roe": 33.8}
  },
  "target_price": {"avg": 1824, "high": 1950, "low": 1680},
  "upside_pct": 25.8,
  "rating_dist": {"买入": 9, "增持": 5, "中性": 1},
  "org_count": 15,
  "last_update": "2026-04-10"
}
```

### `/api/earnings/index/{index_code}`

**功能**：获取指数成分股的加权一致预期

**返回**：
```json
{
  "index_code": "000300.SH",
  "index_name": "沪深300",
  "coverage": 285,  // 300只成分股中有285只有覆盖
  "consensus": {
    "eps_growth_2026": 11.5,
    "np_growth_2026": 12.3,
    "avg_upside_pct": 15.2,
    "buy_rating_pct": 65,
    "overweight_rating_pct": 22
  },
  "top_coverage_stocks": [
    {"ts_code": "600519.SH", "name": "贵州茅台", "org_count": 35, "upside": 25.8},
    {"ts_code": "000858.SZ", "name": "五粮液", "org_count": 32, "upside": 18.5},
    // ...
  ]
}
```

### `/api/valuation/{ts_code}`

**功能**：获取估值定价分析

**返回**：
```json
{
  "ts_code": "600519.SH",
  "current_price": 1450.0,
  "trailing_pe": 25.2,
  "forward_pe": 19.9,
  "peg": 1.32,
  "pe_percentile": 45,
  "signal": "偏低",
  "confidence": "高",
  "reasoning": "Forward PE 低于历史均值，PEG 在合理区间，机构覆盖充分"
}
```

---

## 📅 开发计划

### V6.5 独立 Phase（在 V6 之后）

| Phase | 内容 | 工时 | 产出 |
|-------|------|------|------|
| **6.1** | 模块H：盈利预测聚合 | 2天 | `earnings_forecast.py` + API |
| **6.2** | 模块I：估值定价引擎 | 1.5天 | `valuation_engine.py` + API |
| **6.3** | 模块J：业务敞口分析 | 1天 | `business_exposure.py` |
| **6.4** | Pipeline 整合 + Context 注入 | 1天 | `_build_market_context()` 扩展 |
| **6.5** | 前端展示（Simple + Pro） | 2天 | app.js 新增 UI 组件 |
| **6.6** | 测试 + 联调 | 1.5天 | 端到端验证 |

**总工时**：9-10 天

### 与 V6 的关系

```
V6 (8-12天)                    V6.5 (9-10天)
├── Phase 1: 地缘 + 原油        ├── Phase 6.1: 盈利预测
├── Phase 2: 北向 + ETF修复     ├── Phase 6.2: 估值定价
├── Phase 3: 行业轮动           ├── Phase 6.3: 业务敞口
├── Phase 4: 研报摘要           ├── Phase 6.4: Pipeline 整合
├── Phase 5: 分析历史           ├── Phase 6.5: 前端展示
│                               └── Phase 6.6: 测试联调
│
└─────────────────────────────────────────────────────────────┐
                                                              │
                        V7 (预研)                              │
                        ├── DCF 自动估值                       │
                        ├── 情景分析引擎增强                   │
                        └── 个股推荐系统                       │
```

**建议执行顺序**：
1. **先完成 V6 Phase 1-2**（地缘 + 北向修复）— 这是最紧急的盲点
2. **并行启动 V6.5 Phase 6.1**（盈利预测）— 数据独立，可以并行
3. V6 完成后全力推进 V6.5

---

## 📊 预期效果

### 分析报告质量提升

| 维度 | Before (V4) | After (V6+V6.5) |
|------|-------------|-----------------|
| **估值判断** | 只有 PE 百分位 | + Forward PE + PEG + 目标价 |
| **盈利预期** | ❌ 没有 | ✅ 机构一致预期（营收/净利润/EPS/ROE） |
| **定价参考** | ❌ 没有 | ✅ 目标价区间 + 潜在空间 |
| **机构观点** | ❌ 没有 | ✅ 评级分布 + 覆盖机构数 |
| **地缘敏感度** | ❌ 没有 | ✅ 业务敞口 + 出口占比 |

### 与领导反馈的对应

| 领导说的 | V6.5 如何解决 |
|---------|--------------|
| "盈利预测是重中之重" | ✅ 模块H 直接接入券商预测 |
| "估值定价预测" | ✅ 模块I 计算 Forward PE / PEG / 目标价 |
| "业务敞口" | ✅ 模块J 分析出口占比、地缘脆弱性 |
| "情景假设" | 🟡 V6 模块F 已有，V6.5 可增强 |

### 综合评分预期

| 版本 | 评分 | 关键提升 |
|------|------|---------|
| V4 (现状) | 3.0/5 | 技术面+估值百分位 |
| V6 (地缘+原油) | 3.5/5 | 补齐地缘盲点 |
| **V6+V6.5** | **4.2~4.5/5** | **+盈利预测+估值定价** |
| V7 (远期) | 4.5~4.8/5 | +DCF+个股推荐 |

---

## ⚠️ 风险与注意事项

### 数据风险

| 风险 | 影响 | 应对 |
|------|------|------|
| 券商预测覆盖不全 | 小盘股可能没有覆盖 | 展示时标注覆盖机构数，<3家标黄 |
| 预测数据滞后 | 报告日期可能是2周前 | 展示最新报告日期，>30天标橙 |
| 极端预测干扰均值 | 个别券商预测离谱 | 使用中位数 + 去极值 |

### 技术风险

| 风险 | 影响 | 应对 |
|------|------|------|
| Tushare API 限频 | 批量拉取可能被限 | 缓存24h + 渐进式预热 |
| 数据量大 | 沪深300 = 300次API | 夜间批量预热 + Redis 缓存 |

---

## 📝 下一步行动

1. **确认设计方案**（今晚）
2. **开始 V6 Phase 1**（明天）— 地缘政治最紧急
3. **并行写 earnings_forecast.py 骨架**（可以先写）
4. **V6 完成后全力推 V6.5**

---

**作者**：MoneyBag AI + LeiJiang
**日期**：2026-04-15 22:55


---
---

# Part 4：V7 — 推荐引擎 + DCF 估值 + 买卖决策

> 🎯 目标：从"分析给你看"升级到"告诉你买什么、卖什么、买多少"
> ⏱️ 工时：4-7h（Opus 辅助）
> 📋 前置：V6.5 完成（盈利预测 + 估值数据可用）

---

## V7.1 模块 K：个股/基金推荐引擎

### 评分维度

| 维度 | 权重 | 数据来源 | 评分指标 |
|------|------|----------|----------|
| 估值 | 30% | V6.5 盈利预测 | Forward PE、PEG、目标价空间 |
| 盈利 | 25% | V6.5 盈利预测 | EPS 增速、机构覆盖数、评级 |
| 技术 | 15% | 已有 technical.py | RSI、MACD、均线位置 |
| 资金 | 15% | 已有 factor_data.py | 北向、融资、主力 |
| 风险 | 15% | 已有 risk.py + V6 地缘 | 地缘敞口、波动率、行业集中度 |

### 后端实现

```python
# services/recommend_engine.py（新建）
# 配置放在 config.py
RECOMMEND_WEIGHTS = {
    'valuation': 0.30, 'earnings': 0.25, 'technical': 0.15,
    'capital': 0.15, 'risk': 0.15,
}

class RecommendEngine:
    """推荐引擎：5维评分 → 排序 → R1生成理由 → 输出推荐列表"""

    async def get_stock_recommendations(self, user_id, top_n=10, pool='all'):
        candidates = await self._get_candidate_pool(user_id, pool)
        scored = [await self._calc_composite_score(s) for s in candidates]
        scored.sort(key=lambda x: x['total_score'], reverse=True)
        top = scored[:top_n]
        
        from services.llm_gateway import call_llm
        reasons = await call_llm(
            prompt=self._build_reason_prompt(top),
            tier='llm_heavy', module='recommend')
        
        for i, item in enumerate(top):
            item['reason'] = reasons[i] if i < len(reasons) else ''
            item['suggested_position'] = self._calc_position(item)
        return {'recommendations': top}

    async def _calc_composite_score(self, stock):
        scores = {
            'valuation': self._score_valuation(stock),
            'earnings': self._score_earnings(stock),
            'technical': self._score_technical(stock),
            'capital': self._score_capital(stock),
            'risk': self._score_risk(stock),
        }
        total = sum(scores[k] * RECOMMEND_WEIGHTS[k] for k in RECOMMEND_WEIGHTS)
        return {**stock, 'total_score': round(total, 1), 'dimension_scores': scores}

    def _calc_position(self, item):
        score = item['total_score']
        if score >= 80: return {'action': '建议买入', 'position_pct': 0.05}
        elif score >= 70: return {'action': '可以关注', 'position_pct': 0.03}
        else: return {'action': '观望', 'position_pct': 0}
```

### API

```python
@app.get("/api/recommend/stocks")
async def recommend_stocks(userId: str, topN: int = 10, pool: str = 'all'):
    return await RecommendEngine().get_stock_recommendations(userId, topN, pool)

@app.get("/api/recommend/funds")
async def recommend_funds(userId: str, topN: int = 10):
    return await FundRecommendEngine().get_fund_recommendations(userId, topN)
```

---

## V7.2 模块 L：DCF 简化估值

```python
# services/dcf_valuation.py（新建）

class DCFValuation:
    DISCOUNT_RATE = 0.10
    TERMINAL_GROWTH = 0.03
    PROJECTION_YEARS = 5
    MARGIN_OF_SAFETY = 0.30

    async def estimate_intrinsic_value(self, stock_code):
        fcf = await self._get_free_cash_flow(stock_code)
        growth_rate = await self._get_growth_rate(stock_code)

        projected = []
        current = fcf[-1]
        for _ in range(self.PROJECTION_YEARS):
            current *= (1 + growth_rate)
            projected.append(current)

        pv_fcf = sum(f / (1 + self.DISCOUNT_RATE)**y for y, f in enumerate(projected, 1))
        terminal = projected[-1] * (1 + self.TERMINAL_GROWTH) / (self.DISCOUNT_RATE - self.TERMINAL_GROWTH)
        pv_terminal = terminal / (1 + self.DISCOUNT_RATE)**self.PROJECTION_YEARS

        shares = await self._get_shares(stock_code)
        intrinsic = (pv_fcf + pv_terminal) / shares if shares > 0 else 0
        buy_price = intrinsic * (1 - self.MARGIN_OF_SAFETY)
        current_price = await self._get_current_price(stock_code)

        if current_price <= buy_price: verdict, emoji = '低估（有安全边际）', '🟢'
        elif current_price <= intrinsic: verdict, emoji = '合理偏低', '🟡'
        elif current_price <= intrinsic * 1.2: verdict, emoji = '合理', '🟡'
        else: verdict, emoji = '高估', '🔴'

        return {
            'intrinsic_value': round(intrinsic, 2), 'buy_price': round(buy_price, 2),
            'current_price': current_price,
            'upside': round((intrinsic / current_price - 1) * 100, 1),
            'verdict': verdict, 'emoji': emoji,
        }
```

---

## V7.3 模块 M：买卖决策输出

### 决策流程

```
DecisionContextBuilder 自动收集：
├── 推荐引擎评分（V7.1）  ├── DCF 估值（V7.2）
├── 盈利预测（V6.5）       ├── 技术信号（signal.py）
├── 风险评估（risk.py）    ├── 地缘影响（V6）
├── 持仓现状               └── 市场 Regime
        ↓
   R1 综合决策 → 结构化 JSON
        ↓
├── 操作列表（买/卖/持有 + 理由 + 仓位）
├── 三情景分析（乐观/中性/悲观）
└── 决策日志存储（V8 复盘依赖）
```

```python
# services/decision_maker.py（新建）

class DecisionMaker:
    async def generate_decisions(self, user_id):
        from services.decision_context import DecisionContextBuilder
        context = await DecisionContextBuilder().build(user_id)
        recommend = await RecommendEngine().get_stock_recommendations(user_id, 5)
        
        dcf_results = {}
        for h in context.get('holdings', {}).get('active', []):
            dcf_results[h['code']] = await DCFValuation().estimate_intrinsic_value(h['code'])

        from services.llm_gateway import call_llm
        prompt = f"""专业投资顾问，基于数据生成操作建议。
持仓：{json.dumps(context.get('holdings'), ensure_ascii=False)}
推荐Top5：{json.dumps(recommend['recommendations'], ensure_ascii=False)}
DCF：{json.dumps(dcf_results, ensure_ascii=False)}
盈利预测：{json.dumps(context.get('earnings_forecast'), ensure_ascii=False)}
Regime：{json.dumps(context.get('regime'), ensure_ascii=False)}
地缘：{json.dumps(context.get('geopolitical'), ensure_ascii=False)}

输出JSON：{{"decisions":[{{"symbol":"..","name":"..","action":"buy|sell|hold|reduce|add","position_pct":0.05,"reason":"..","confidence":0.8,"risk_warning":".."}}],"scenarios":{{"optimistic":"..","neutral":"..","pessimistic":".."}}, "overall_strategy":".."}}"""

        result = await call_llm(prompt=prompt, tier='llm_heavy', module='decision')
        decisions = json.loads(result) if isinstance(result, str) else result
        self._save_decision_log(user_id, decisions)
        return decisions

    def _save_decision_log(self, user_id, decisions):
        log_dir = DATA_DIR / "decisions" / user_id
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{date.today()}.json").write_text(
            json.dumps({'date': str(date.today()), 'decisions': decisions},
                       ensure_ascii=False, indent=2), encoding='utf-8')
```

---

## V7 实施计划 + 验收

| Phase | 任务 | 时间 | 产出 |
|-------|------|------|------|
| V7.1 | 推荐引擎 | 2h | recommend_engine.py |
| V7.2 | DCF 估值 | 1.5h | dcf_valuation.py |
| V7.3 | 买卖决策 | 2h | decision_maker.py |
| V7.4 | 前端展示 | 1h | app.js 推荐/决策渲染 |
| V7.5 | 推送增强 | 0.5h | 早安简报加操作建议 |
| V7.6 | 联调 | 1h | 全链路验证 |

**验收**：
- [ ] 推荐引擎 Top N + 5维评分
- [ ] DCF 对主流个股 ±20%
- [ ] R1 决策输出结构化 JSON
- [ ] Simple 3秒看清买卖 / Pro 完整分析
- [ ] 决策日志正确存储（V8依赖）

---
---

# Part 5：V8 — AI 自主复盘系统

> 🎯 AI 从"建议"升级到"建议→验证→归因→自动调整"闭环
> ⏱️ 工时：9天
> 📋 前置：V7上线≥1月 + decisions/≥200条

---

## V8 能力分层

| 层级 | 能力 | V6-V7 | V8 |
|------|------|-------|-----|
| L1 记录 | 存每次分析 | ✅ | — |
| L2 对比 | 历史上下文 | ✅ | — |
| L3 验证 | 预测对不对？ | ❌ | ✅ V8.1 |
| L4 归因 | 错在哪？ | ❌ | ✅ V8.2 |
| L5 调整 | 自动修正 | ❌ | ✅ V8.3 |

---

## V8.1 预测验证引擎

```python
# services/review_engine.py（新建）

class ReviewEngine:
    REVIEW_WINDOWS = [7, 14, 30]

    async def run_review(self, user_id):
        decisions = self._load_past_decisions(user_id)
        reviews = [await self._verify(d) for d in decisions if self._is_reviewable(d)]
        stats = self._calc_accuracy(reviews)
        attribution = await self._attribute_errors(reviews)
        adjustments = await self._suggest_adjustments(reviews, attribution)
        self._save_review(user_id, {'stats': stats, 'attribution': attribution, 'adjustments': adjustments})
        return {'stats': stats, 'attribution': attribution, 'adjustments': adjustments}

    async def _verify(self, decision):
        """对比预测 vs 实际"""
        for w in self.REVIEW_WINDOWS:
            target = decision['date'] + timedelta(days=w)
            if target > date.today(): continue
            actual = await self._get_price_change(decision['symbol'], decision['date'], target)
            if decision['action'] in ('buy','add'): correct = actual > 0
            elif decision['action'] in ('sell','reduce'): correct = actual < 0
            else: correct = abs(actual) < 0.05
            decision[f'review_{w}d'] = {'actual': actual, 'correct': correct}
        return decision

    def _calc_accuracy(self, reviews):
        stats = {}
        for w in self.REVIEW_WINDOWS:
            k = f'{w}d'
            total = sum(1 for r in reviews if f'review_{k}' in r)
            correct = sum(1 for r in reviews if r.get(f'review_{k}',{}).get('correct'))
            stats[k] = {'total': total, 'correct': correct,
                        'accuracy': round(correct/total*100,1) if total else 0}
        return stats
```

## V8.2 R1 归因分析

```python
    async def _attribute_errors(self, reviews):
        errors = [r for r in reviews if not r.get('review_30d',{}).get('correct',True)]
        if not errors: return {'message': '暂无错误'}

        from services.llm_gateway import call_llm
        prompt = f"""复盘：以下错误决策，从6维度归因（估值/盈利/技术/资金/黑天鹅/时机）。
错误：{json.dumps(errors, ensure_ascii=False)}
输出JSON：{{"error_distribution":{{"valuation":30,...}}, "top_lessons":["..",".."]}}"""
        result = await call_llm(prompt=prompt, tier='llm_heavy', module='attribution')
        return json.loads(result) if isinstance(result, str) else result
```

## V8.3 策略自动调整

```python
    async def _suggest_adjustments(self, reviews, attribution):
        dist = attribution.get('error_distribution', {})
        adj = {'weight_adjustments': {}, 'param_adjustments': {}, 'new_rules': []}

        # 错得多的维度降权15%
        for dim, pct in dist.items():
            if pct > 25:
                key = self._map_dimension(dim)
                if key in RECOMMEND_WEIGHTS:
                    old = RECOMMEND_WEIGHTS[key]
                    adj['weight_adjustments'][key] = {'old':old, 'new':round(old*0.85,3)}

        # 时机错多 → 加大安全边际
        if dist.get('timing',0) > 20:
            adj['param_adjustments']['margin_of_safety'] = {'old':0.30, 'new':0.35}

        # 黑天鹅多 → 加地缘权重
        if dist.get('black_swan',0) > 20:
            adj['param_adjustments']['geopolitical_weight'] = {'old':0.10, 'new':0.15}

        # R1 生成新规则
        from services.llm_gateway import call_llm
        adj['new_rules'] = await call_llm(
            prompt=f"基于归因{json.dumps(attribution)}，建议新增可量化投资规则，最多3条。",
            tier='llm_heavy', module='adjustment')
        return adj

# 执行分级：
# A级（自动）：权重±15%、安全边际±5% → 直接改config
# B级（需确认）：新规则、大幅调整 → 推企微等确认
```

## V8.4 触发机制

```python
REVIEW_TRIGGERS = {
    'weekly':        {'schedule': 'sunday 02:00',     'min_decisions': 5},
    'monthly':       {'schedule': '1st 02:00',        'min_decisions': 20},
    'accuracy_drop': {'trigger': 'accuracy_7d < 40%', 'desc': '准确率下降紧急复盘'},
    'black_swan':    {'trigger': 'market_drop > 5%',  'desc': '黑天鹅复盘'},
}
```

## V8.5 前端 + API

```javascript
// Simple: "🎯 准确率 65%（近7天）"
// Pro: 趋势图 + 归因饼图 + 教训 + 策略调整记录
```

```python
@app.get("/api/review/latest")
async def get_latest_review(userId: str):
    review_dir = DATA_DIR / "reviews" / userId
    if not review_dir.exists(): return {'message': '暂无'}
    return json.loads(sorted(review_dir.glob("*.json"))[-1].read_text(encoding='utf-8'))

@app.post("/api/review/run")
async def run_review(userId: str):
    return await ReviewEngine().run_review(userId)
```

---

## V8 实施计划 + 验收

| Phase | 任务 | 时间 |
|-------|------|------|
| V8.1 | 预测验证引擎 | 2天 |
| V8.2 | R1归因分析 | 1.5天 |
| V8.3 | 策略自动调整 | 2天 |
| V8.4 | 触发机制 | 1天 |
| V8.5 | 前端+推送 | 1.5天 |
| **V8.6** | **研报追踪 + 券商评分 + 权重学习** | **6天** |
| V8.7 | 联调 | 1天 |

**验收**：
- [ ] 7/14/30天准确率正确
- [ ] R1归因输出JSON
- [ ] A级自动执行，B级推企微确认
- [ ] 周/月/紧急复盘触发
- [ ] Simple看准确率 / Pro看归因+调整
- [ ] V7日志→V8正确读取
- [ ] 研报每次拉取自动存档
- [ ] 券商准确率排名合理
- [ ] AI 盈利预测用加权一致预期（非简单平均）

**启动条件**：V7上线≥1月 + decisions/≥200条 + 用户确认

---

## V8.6 研报预测追踪 + 券商评分 + 权重学习

> 🎯 不盲信研报，AI 知道"谁说的话更可信"，越用越准  
> 📋 前置：V6.5 Tushare report_rc 接入 + 研报数据积累 ≥ 3 个月

### 核心闭环

```
拉取研报 → 自动存档 → 实际业绩公布 → 对比偏差 → 券商评分 → AI 加权引用
     ↑                                                        ↓
     └──────────── 每季度财报公布后自动触发一轮 ────────────────┘
```

### 数据存储

```python
# data/reports/{stock_code}/{date}.json
{
    "stock_code": "600519",
    "fetch_date": "2026-05-20",
    "predictions": [
        {
            "broker": "招商证券",
            "analyst": "张三",
            "rating": "买入",
            "target_price": 2100.0,
            "eps_forecast": {"2026": 68.5, "2027": 78.2},
            "revenue_forecast": {"2026": 1650, "2027": 1900},
            "report_date": "2026-05-15"
        },
        {
            "broker": "中信证券",
            "analyst": "李四",
            "rating": "增持",
            "target_price": 1950.0,
            "eps_forecast": {"2026": 65.0, "2027": 74.0},
            "report_date": "2026-05-18"
        }
    ]
}
```

### 后端实现

```python
# services/report_tracker.py（V8.6 新建）

class ReportTracker:
    """研报预测追踪 + 券商评分"""
    
    async def save_snapshot(self, stock_code: str):
        """每次拉取研报时自动存档"""
        predictions = await get_report_rc(stock_code)
        if not predictions:
            return
        
        snapshot_dir = DATA_DIR / "reports" / stock_code
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / f"{date.today()}.json").write_text(
            json.dumps({
                'stock_code': stock_code,
                'fetch_date': date.today().isoformat(),
                'predictions': predictions,
            }, ensure_ascii=False, indent=2), encoding='utf-8')
    
    async def verify_predictions(self, stock_code: str) -> dict:
        """验证研报预测 vs 实际业绩"""
        actual = await self._get_actual_financials(stock_code)
        if not actual:
            return {'message': '实际业绩尚未公布'}
        
        results = []
        snapshot_dir = DATA_DIR / "reports" / stock_code
        for f in sorted(snapshot_dir.glob("*.json")):
            snapshot = json.loads(f.read_text(encoding='utf-8'))
            for pred in snapshot['predictions']:
                for year, eps_pred in pred.get('eps_forecast', {}).items():
                    eps_actual = actual.get(f'eps_{year}')
                    if eps_actual and eps_actual != 0:
                        error_pct = (eps_pred - eps_actual) / abs(eps_actual) * 100
                        results.append({
                            'broker': pred['broker'],
                            'stock_code': stock_code,
                            'year': year,
                            'eps_predicted': eps_pred,
                            'eps_actual': eps_actual,
                            'error_pct': round(error_pct, 1),
                            'accurate': abs(error_pct) <= 10,  # ±10% 内算准确
                        })
        return {'results': results}
    
    def calc_broker_scores(self) -> dict:
        """券商预测准确率排名"""
        from collections import defaultdict
        stats = defaultdict(lambda: {'total': 0, 'accurate': 0, 'errors': []})
        
        reports_dir = DATA_DIR / "reports"
        if not reports_dir.exists():
            return {'scores': []}
        
        for stock_dir in reports_dir.iterdir():
            if not stock_dir.is_dir():
                continue
            result = self.verify_predictions(stock_dir.name)
            for r in result.get('results', []):
                b = stats[r['broker']]
                b['total'] += 1
                if r['accurate']:
                    b['accurate'] += 1
                b['errors'].append(r['error_pct'])
        
        scores = []
        for broker, s in stats.items():
            acc = s['accurate'] / s['total'] * 100 if s['total'] > 0 else 0
            avg_err = sum(s['errors']) / len(s['errors']) if s['errors'] else 0
            scores.append({
                'broker': broker,
                'total': s['total'],
                'accuracy': round(acc, 1),
                'avg_error_pct': round(avg_err, 1),
                'bias': '偏乐观' if avg_err > 5 else '偏悲观' if avg_err < -5 else '中性',
                'trust_weight': min(1.0, acc / 100 * 1.2),
            })
        
        scores.sort(key=lambda x: x['accuracy'], reverse=True)
        return {'scores': scores}
```

### AI 如何利用券商评分

```python
# V6.5 盈利预测中，加入券商加权一致预期

async def get_weighted_consensus(stock_code: str) -> dict:
    """准确率高的券商权重更大"""
    tracker = ReportTracker()
    weight_map = {s['broker']: s['trust_weight']
                  for s in tracker.calc_broker_scores().get('scores', [])}
    
    predictions = await get_report_rc(stock_code)
    weighted_eps, total_w = 0, 0
    
    for p in predictions:
        w = weight_map.get(p['broker'], 0.5)  # 没评分的默认 0.5
        eps = p.get('eps_forecast', {}).get('2026', 0)
        if eps > 0:
            weighted_eps += eps * w
            total_w += w
    
    return {
        'consensus_eps': round(weighted_eps / total_w, 2) if total_w > 0 else 0,
        'method': 'broker_weighted',
        'top_broker': tracker.calc_broker_scores()['scores'][0]['broker']
                      if tracker.calc_broker_scores()['scores'] else '无',
    }

# R1 Prompt 自动注入券商可信度提示：
# "注意：招商证券消费行业预测历史偏乐观18%，中信证券准确率最高(72%)，
#  请优先参考中信数据。以下一致预期已按券商准确率加权。"
```

### API

```python
@app.get("/api/reports/broker-scores")
async def broker_scores():
    """券商预测准确率排名"""
    return ReportTracker().calc_broker_scores()

@app.get("/api/reports/verify/{stock_code}")
async def verify_reports(stock_code: str):
    """验证研报预测 vs 实际"""
    return await ReportTracker().verify_predictions(stock_code)
```

### V8.6 实施计划

| 任务 | 时间 | 产出 | 前置 |
|------|------|------|------|
| 研报存档机制 | 1天 | save_snapshot() | V6.5 Tushare |
| 预测验证引擎 | 1.5天 | verify_predictions() | 积累≥3月研报 |
| 券商评分+权重 | 1.5天 | calc_broker_scores() | 验证引擎 |
| AI加权一致预期 | 1天 | get_weighted_consensus() + Prompt注入 | 券商评分 |
| 前端展示 | 0.5天 | 券商排名表（Pro） | API 完成 |
| 联调 | 0.5天 | - | 全部 |

---

**全景设计文档 完**

---
---

# Part 7：V10+ 远期能力规划

> 🎯 以下功能按需展开，不设固定时间表  
> 📋 前置：V9 模拟盘成熟后，根据实际需求选择性开发

---

## 可融入现有版本的能力（V6-V8 期间逐步加入）

### A. 另类数据情绪分析

> 散户情绪是经典反向指标：大家喊"牛市来了"往往该跑了

```python
# 数据源：雪球/东财股吧评论热度 + 情感分析
# 融入：V6 的 factor_data.py 新增情绪因子

class SentimentAnalyzer:
    """社交情绪分析"""
    
    SOURCES = {
        'xueqiu': '雪球热帖',      # 爬取雪球热门讨论
        'eastmoney': '东财股吧',    # 爬取股吧评论数
        'weibo': '微博财经',        # 财经大 V 观点
    }
    
    async def get_sentiment(self, stock_code: str) -> dict:
        """情绪指标：0-100（0 极度恐惧，100 极度贪婪）"""
        # 评论量突增 + 正面情绪占比 → 散户贪婪 → 反向信号
        return {
            'score': 75,
            'level': 'greedy',      # fearful | neutral | greedy
            'signal': 'contrarian_sell',  # 反向：该减仓
            'detail': '雪球讨论量 3 天增 200%，散户情绪过热',
        }

# AI Prompt 注入：
# "当前散户情绪：贪婪(75)，历史数据显示散户情绪>70时，
#  30天内下跌概率65%。请将此纳入风险评估。"
```

**放在**：V6 factor_data.py 新增维度，0.5 天

---

### B. 关联交易/产业链检测

> 茅台供应商/经销商异动 → 可能提前预警茅台

```python
# 数据源：Tushare 供应链数据 / Wind 产业链图谱（简化版用 AI 生成）

SUPPLY_CHAIN = {
    '600519': {  # 茅台
        'suppliers': ['包装材料', '高粱种植'],
        'distributors': ['贵州茅台经销商'],
        'competitors': ['000858', '000596'],  # 五粮液、古井贡
        'related_sectors': ['白酒', '消费'],
    }
}

# AI Prompt 注入：
# "你持有茅台，注意：五粮液今日跌5%，白酒板块整体承压，
#  请评估对茅台的传导影响。"
```

**放在**：V6 decision_context.py 扩展，1 天

---

### C. 宏观事件日历预警

> 不是事后分析，是**提前提醒**

```python
# 重要事件日历
MACRO_EVENTS = [
    {'event': '美联储议息会议', 'dates': ['2026-06-18', '2026-07-30'], 
     'impact': '利率决策影响全球市场', 'action': '提前 1 天提醒降低仓位'},
    {'event': 'CPI 数据公布', 'dates': ['每月 10-15 号'],
     'impact': '超预期则利空', 'action': '公布前不建仓'},
    {'event': '财报季', 'dates': ['4月/7月/10月/1月'],
     'impact': '个股波动加大', 'action': '持仓股财报前减仓或对冲'},
]

# 每日 08:00 检查明天有没有重要事件
# 有 → 推企微："⚠️ 明天美联储议息，建议今天不要新建仓位"
```

**放在**：V6 后台任务新增，0.5 天

---

### D. 主力/游资资金追踪

> 北向资金之外，还要看龙虎榜、大单净流入

```python
# 数据源：Tushare top_list（龙虎榜）/ AKShare 主力资金

async def get_smart_money_signal(stock_code: str) -> dict:
    """聪明钱信号"""
    return {
        'northbound': '+2.3亿',        # 北向
        'main_flow': '+1.5亿',         # 主力净流入
        'dragon_tiger': True,           # 今日上龙虎榜
        'top_buyer': '机构专用',        # 龙虎榜买方
        'signal': 'bullish',            # 机构在买
    }
```

**放在**：V6 factor_data.py 扩展，1 天

---

## 独立版本的能力（V10+）

### E. 跨市场联动分析

```python
# "美股昨晚涨了，A股今天大概率怎样？"
# "原油涨了，利好哪些？利空哪些？"
# "铜价创新高，对应 A 股受益标的？"

CROSS_MARKET_RULES = {
    'US_up_3pct': '美股暴涨 → A股高开概率 70%，但警惕冲高回落',
    'oil_up_5pct': '原油暴涨 → 利好：中石油/中海油，利空：航空/化工下游',
    'gold_up_3pct': '黄金暴涨 → 避险情绪升温，可能利空成长股',
    'copper_new_high': '铜创新高 → 利好：紫金矿业/江西铜业',
    'us_yield_invert': '美债收益率倒挂 → 衰退预警，防御为主',
}
```

---

### F. 财报 5 秒解读

```python
# 财报公布的瞬间，AI 自动解读

class EarningsFlash:
    """财报闪电解读"""
    
    async def analyze_earnings(self, stock_code: str) -> dict:
        """财报出来 5 秒出解读"""
        # 1. 拉取最新财报数据
        actual = await get_latest_financials(stock_code)
        
        # 2. 对比一致预期
        consensus = await get_weighted_consensus(stock_code)  # V8.6 的加权预期
        
        # 3. V3 快速生成解读（<3 秒）
        beat_or_miss = '超预期' if actual['eps'] > consensus['eps'] * 1.05 else \
                       '不及预期' if actual['eps'] < consensus['eps'] * 0.95 else '符合预期'
        
        return {
            'verdict': beat_or_miss,
            'actual_eps': actual['eps'],
            'consensus_eps': consensus['eps'],
            'surprise_pct': (actual['eps'] / consensus['eps'] - 1) * 100,
            'key_highlights': await self._generate_highlights(actual),
            'impact_on_holding': '盈利超预期，建议持有' if beat_or_miss == '超预期' else '观察',
        }

# 触发：Tushare 财报数据更新 → 自动检测 → 如果是持仓股 → 推企微
```

---

### G. 大宗交易/股东减持监控

```python
# 大股东减持 = 知道内情的人在卖 → 警惕

INSIDER_ALERTS = {
    'block_trade': '大宗交易折价 > 5% → ⚠️ 可能有利空',
    'insider_sell': '高管/大股东连续减持 → ⚠️ 信心不足',
    'pledge_ratio': '股权质押比 > 50% → ⚠️ 平仓风险',
}

# 数据源：Tushare stk_holdertrade（股东增减持）
# 触发：持仓股出现减持 → 推企微
```

---

### H. 可转债套利监控

```python
# 转股溢价率 < 0 → 折价套利机会

async def scan_cb_arbitrage():
    """可转债套利扫描"""
    cbs = await get_convertible_bonds()
    opportunities = [
        cb for cb in cbs
        if cb['premium_rate'] < -0.02  # 折价 > 2%
        and cb['volume'] > 1000000      # 成交量够
    ]
    return opportunities
```

---

### I. 因子历史回测

> `genetic_factor.py` 已有，但没跑过"这个因子赚不赚钱"

```python
# 激活 genetic_factor.py + backtest_engine.py
# 用 V9 模拟盘的框架跑因子回测

async def backtest_factor(factor_name: str, lookback_years: int = 3) -> dict:
    """因子回测：这个因子过去 3 年赚不赚钱"""
    # genetic_factor 生成因子 → backtest_engine 回测
    # 输出：年化收益、最大回撤、夏普比率、胜率
    return {
        'factor': factor_name,
        'annual_return': 0.15,
        'max_drawdown': -0.12,
        'sharpe_ratio': 1.3,
        'win_rate': 0.58,
        'verdict': '有效因子，建议纳入评分体系',
    }
```

---

### J. 多策略组合

```python
# V9 模拟盘成熟后，同时跑多个策略互相对冲

STRATEGIES = {
    'conservative': {'weights': {'valuation': 0.4, 'earnings': 0.3, 'risk': 0.3}},
    'momentum': {'weights': {'technical': 0.4, 'capital': 0.3, 'momentum': 0.3}},
    'contrarian': {'weights': {'sentiment': -0.3, 'valuation': 0.4, 'quality': 0.3}},  # 情绪反向
}

# 每个策略独立跑模拟盘 → 对比 → 最优组合
```

---

### K. 事件驱动策略

```python
# 从历史中挖规律

EVENT_PATTERNS = {
    'fed_cut': {
        'event': '美联储降息',
        'pattern': '降息后 30 天，沪深300 平均涨 4.2%',
        'sample_size': 12,
        'win_rate': 0.75,
        'action': '降息当天加仓宽基 ETF',
    },
    'spring_festival': {
        'event': '春节前 2 周',
        'pattern': '消费板块平均涨 3.1%',
        'sample_size': 10,
        'win_rate': 0.80,
        'action': '节前加配消费 ETF',
    },
}
```

---

## V10+ 能力融入时间表

| 能力 | 融入版本 | 工时 | 优先级 |
|------|---------|------|--------|
| A. 社交情绪分析 | V6 | 0.5 天 | 🔴 高 |
| B. 产业链检测 | V6 | 1 天 | 🔴 高 |
| C. 事件日历预警 | V6 | 0.5 天 | 🔴 高 |
| D. 主力/游资追踪 | V6 | 1 天 | 🔴 高 |
| E. 跨市场联动 | V7 | 1 天 | 🟡 中 |
| F. 财报闪电解读 | V8.6 | 1 天 | 🟡 中 |
| G. 大股东减持监控 | V7 | 0.5 天 | 🟡 中 |
| H. 可转债套利 | V10 | 1 天 | 🟢 低 |
| I. 因子回测 | V9 | 2 天 | 🟡 中 |
| J. 多策略组合 | V10 | 3 天 | 🟢 低 |
| K. 事件驱动策略 | V10 | 2 天 | 🟢 低 |

---

**全景设计文档 完**

---
---

# Part 6：V9 — AI 模拟交易 + 自主学习系统

> 🎯 目标：AI 拥有"练功房"，用虚拟资金自主建仓、追踪、总结、迭代，不花真钱  
> ⏱️ 工时：10-12 天  
> 📋 前置：V8 完成 + AI 复盘准确率趋于稳定  
> 🔴 **铁律：模拟盘数据与真实资产完全隔离，永远不混入首页/推送/家庭净资产**

---

## 核心理念

```
V7：AI 给你建议，你决定买不买
V8：AI 事后验证建议对不对
V9：AI 自己买自己卖自己验证自己学 ← 100% 建议都被验证，不花一分钱
```

---

## 数据隔离（铁律）

```
data/
├── users/LeiJiang.json        ← 真实资产 💰
├── users/BuLuoGeLi.json       ← 真实资产 💰
│
├── paper-trading/              ← AI 模拟盘 🤖（完全独立）
│   ├── portfolio.json          ← 模拟持仓
│   ├── transactions/           ← 模拟交易流水
│   ├── daily-pnl/              ← 每日盈亏
│   ├── strategy-logs/          ← 策略迭代日志
│   └── ab-tests/               ← A/B 策略对比
│
├── decisions/                  ← 真实决策日志（V7/V8 用）
└── reports/                    ← 研报存档（V8.6 用）

隔离规则：
├── GET /api/household/summary     → 只读 users/，不碰 paper-trading/
├── GET /api/paper-trading/summary → 只读 paper-trading/
├── 推送（企微）                    → 永远不提模拟盘数据
└── 首页 renderLanding()           → 永远不显示模拟盘
```

---

## V9.1 模拟账户引擎

```python
# services/paper_trading.py（V9 新建）

INITIAL_CAPITAL = 1_000_000  # 虚拟 100 万

class PaperTradingEngine:
    """AI 模拟交易引擎 — 完全隔离的虚拟账户"""
    
    PORTFOLIO_FILE = DATA_DIR / "paper-trading" / "portfolio.json"
    
    def __init__(self):
        self._ensure_dirs()
        self.portfolio = self._load_portfolio()
    
    def _ensure_dirs(self):
        for d in ['paper-trading', 'paper-trading/transactions',
                  'paper-trading/daily-pnl', 'paper-trading/strategy-logs',
                  'paper-trading/ab-tests']:
            (DATA_DIR / d).mkdir(parents=True, exist_ok=True)
    
    def _load_portfolio(self) -> dict:
        if self.PORTFOLIO_FILE.exists():
            return json.loads(self.PORTFOLIO_FILE.read_text(encoding='utf-8'))
        return {
            'cash': INITIAL_CAPITAL,
            'holdings': [],
            'total_value': INITIAL_CAPITAL,
            'created_at': datetime.now().isoformat(),
            'version': 1,
        }
    
    def _save_portfolio(self):
        self.PORTFOLIO_FILE.write_text(
            json.dumps(self.portfolio, ensure_ascii=False, indent=2), encoding='utf-8')
    
    async def execute_decision(self, decision: dict) -> dict:
        """执行 AI 的模拟交易决策"""
        action = decision['action']
        symbol = decision['symbol']
        
        if action in ('buy', 'add'):
            return await self._sim_buy(symbol, decision)
        elif action in ('sell', 'reduce'):
            return await self._sim_sell(symbol, decision)
        else:
            return {'action': 'hold', 'symbol': symbol}
    
    async def _sim_buy(self, symbol: str, decision: dict) -> dict:
        """模拟买入"""
        price = await self._get_current_price(symbol)
        position_pct = decision.get('position_pct', 0.05)
        amount = self.portfolio['total_value'] * position_pct
        
        if amount > self.portfolio['cash']:
            amount = self.portfolio['cash']  # 不够就全买
        
        if amount <= 0:
            return {'error': True, 'message': '模拟资金不足'}
        
        shares = amount / price
        
        # 更新持仓
        existing = next((h for h in self.portfolio['holdings'] 
                        if h['symbol'] == symbol), None)
        if existing:
            # 加仓
            total_cost = existing['cost'] * existing['shares'] + amount
            existing['shares'] += shares
            existing['cost'] = total_cost / existing['shares']
        else:
            self.portfolio['holdings'].append({
                'symbol': symbol,
                'name': decision.get('name', symbol),
                'shares': shares,
                'cost': price,
                'buy_date': date.today().isoformat(),
            })
        
        self.portfolio['cash'] -= amount
        self._save_portfolio()
        self._log_transaction('buy', symbol, shares, price, amount)
        
        return {'action': 'buy', 'symbol': symbol, 'shares': round(shares, 2),
                'price': price, 'amount': round(amount, 2)}
    
    async def _sim_sell(self, symbol: str, decision: dict) -> dict:
        """模拟卖出"""
        existing = next((h for h in self.portfolio['holdings'] 
                        if h['symbol'] == symbol), None)
        if not existing:
            return {'error': True, 'message': f'模拟盘未持有 {symbol}'}
        
        price = await self._get_current_price(symbol)
        sell_shares = existing['shares']  # 默认全卖
        amount = sell_shares * price
        
        # 计算盈亏
        cost_total = sell_shares * existing['cost']
        pnl = amount - cost_total
        
        # 更新
        self.portfolio['holdings'].remove(existing)
        self.portfolio['cash'] += amount
        self._save_portfolio()
        self._log_transaction('sell', symbol, sell_shares, price, amount, pnl)
        
        return {'action': 'sell', 'symbol': symbol, 'shares': round(sell_shares, 2),
                'price': price, 'pnl': round(pnl, 2)}
    
    def _log_transaction(self, action, symbol, shares, price, amount, pnl=None):
        """记录模拟交易流水"""
        tx = {
            'date': date.today().isoformat(),
            'time': datetime.now().isoformat(),
            'action': action,
            'symbol': symbol,
            'shares': shares,
            'price': price,
            'amount': amount,
            'pnl': pnl,
        }
        tx_file = DATA_DIR / "paper-trading" / "transactions" / f"{date.today()}.jsonl"
        with open(tx_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(tx, ensure_ascii=False) + '\n')
```

---

## V9.2 每日自动决策 + 复盘（凌晨 02:50）

```python
class DailyPaperTradingTask:
    """每日模拟交易任务 — 在 R1 凌晨分析之后运行"""
    
    async def run(self):
        engine = PaperTradingEngine()
        
        # 1. 更新昨日盈亏
        daily_pnl = await self._calc_daily_pnl(engine)
        self._save_daily_pnl(daily_pnl)
        
        # 2. AI 自主决策（用 V7 决策引擎，但针对模拟盘）
        from services.decision_maker import DecisionMaker
        decisions = await DecisionMaker().generate_decisions('AI_SIM')
        
        # 3. 执行模拟交易
        results = []
        for d in decisions.get('decisions', []):
            result = await engine.execute_decision(d)
            results.append(result)
        
        # 4. 自动复盘昨天的决策
        review = await self._review_yesterday(engine)
        
        # 5. 写策略日志
        self._write_strategy_log(daily_pnl, decisions, results, review)
        
        logger.info(f"🤖 模拟交易完成：{len(results)} 笔操作，"
                    f"今日净值 ¥{engine.portfolio['total_value']:,.0f}")
    
    async def _review_yesterday(self, engine) -> dict:
        """复盘昨天的决策"""
        from services.llm_gateway import call_llm
        
        yesterday_pnl = self._load_daily_pnl(date.today() - timedelta(days=1))
        if not yesterday_pnl:
            return {}
        
        prompt = f"""你是 AI 投资复盘专家。以下是你的模拟盘昨日表现，请自我复盘。

## 昨日模拟盘
{json.dumps(yesterday_pnl, ensure_ascii=False, indent=2)}

## 当前持仓
{json.dumps(engine.portfolio['holdings'], ensure_ascii=False, indent=2)}

请输出：
1. 昨天做对了什么
2. 昨天做错了什么
3. 今天应该怎么调整
4. 学到了什么教训（一句话）
"""
        result = await call_llm(prompt=prompt, tier='llm_heavy', module='paper_review')
        return {'review': result, 'date': date.today().isoformat()}
    
    def _write_strategy_log(self, pnl, decisions, results, review):
        """策略迭代日志 — AI 的学习笔记"""
        log = {
            'date': date.today().isoformat(),
            'portfolio_value': pnl.get('total_value', 0),
            'daily_return': pnl.get('daily_return_pct', 0),
            'cumulative_return': pnl.get('cumulative_return_pct', 0),
            'decisions_made': len(results),
            'review': review.get('review', ''),
            'holdings_count': len(PaperTradingEngine().portfolio['holdings']),
        }
        
        log_dir = DATA_DIR / "paper-trading" / "strategy-logs"
        (log_dir / f"{date.today()}.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding='utf-8')

# 注册到凌晨任务（02:50，在 R1 分析 02:00-02:45 之后）
BACKGROUND_TASKS['paper_trading'] = {
    'task': DailyPaperTradingTask,
    'schedule': '02:50',
    'description': 'AI 模拟交易 + 自动复盘',
}
```

---

## V9.3 策略迭代日志展示

```python
# 策略日志示例（AI 自动生成）

# data/paper-trading/strategy-logs/2026-06-01.json
{
    "date": "2026-06-01",
    "portfolio_value": 1052300,
    "daily_return": "+0.35%",
    "cumulative_return": "+5.23%",
    "decisions_made": 2,
    "review": "昨天买入的消费ETF今天涨了1.2%，验证了'恐贪指数<30时加仓'的策略有效。但银行股继续跌，应该更重视北向资金流出信号。教训：资金面权重不够。",
    "holdings_count": 6
}

# data/paper-trading/strategy-logs/2026-06-15.json
{
    "date": "2026-06-15",
    "portfolio_value": 1078500,
    "cumulative_return": "+7.85%",
    "review": "第3周总结：把资金面权重从15%提到20%后，回撤明显减小。估值判断准确率从50%提升到62%。下周尝试加入'连续3日北向流出>50亿则减仓'的新规则。"
}
```

---

## V9.4 A/B 策略对比

```python
class ABTestEngine:
    """A/B 策略对比 — 同时跑两套策略，看谁更好"""
    
    async def setup_test(self, test_name: str, strategy_a: dict, strategy_b: dict):
        """创建 A/B 测试"""
        test = {
            'name': test_name,
            'start_date': date.today().isoformat(),
            'duration_days': 30,
            'strategy_a': {**strategy_a, 'portfolio': {'cash': 500000, 'holdings': []}},
            'strategy_b': {**strategy_b, 'portfolio': {'cash': 500000, 'holdings': []}},
        }
        
        test_file = DATA_DIR / "paper-trading" / "ab-tests" / f"{test_name}.json"
        test_file.write_text(json.dumps(test, ensure_ascii=False, indent=2))
    
    # A/B 测试示例：
    # 策略 A：保守（安全边际 30%，止损 -5%）
    # 策略 B：激进（安全边际 15%，止损 -10%）
    # 跑 30 天 → 自动生成对比报告
    #
    # 报告：
    # "策略A：收益+3.2%，最大回撤-2.1%，夏普1.8"
    # "策略B：收益+5.8%，最大回撤-6.3%，夏普1.2"
    # "结论：策略A风险调整后收益更优，建议采用"
```

---

## V9.5 毕业机制

```python
GRADUATION_CRITERIA = {
    'min_days': 90,                    # 至少跑 90 天
    'min_accuracy_7d': 0.60,           # 7 天准确率 ≥ 60%
    'min_accuracy_30d': 0.55,          # 30 天准确率 ≥ 55%
    'max_drawdown': -0.15,             # 最大回撤 ≤ 15%
    'min_sharpe': 1.0,                 # 夏普比率 ≥ 1.0
    'consecutive_positive_weeks': 6,   # 连续 6 周正收益
}

def check_graduation(strategy_logs: list) -> dict:
    """检查是否达到毕业标准"""
    if len(strategy_logs) < GRADUATION_CRITERIA['min_days']:
        return {'graduated': False, 'reason': f'运行不足 {GRADUATION_CRITERIA["min_days"]} 天'}
    
    # ... 检查各项指标 ...
    
    if all_passed:
        return {
            'graduated': True,
            'message': '🎓 策略成熟！模拟盘 90 天准确率 62%，夏普 1.3，建议正式采用',
            'stats': stats,
        }

# 毕业后：
# 1. 推企微通知你："AI 模拟盘跑了 90 天，准确率 62%，要不要让它影响真实建议？"
# 2. 你确认后，模拟盘的策略参数（权重/阈值）同步到真实决策引擎
# 3. 模拟盘继续跑（永远不停），作为"影子策略"持续验证
```

---

## V9 API

```python
@app.get("/api/paper-trading/summary")
async def paper_trading_summary():
    """模拟盘概览（⚠️ 不混入 /api/household/summary）"""
    engine = PaperTradingEngine()
    p = engine.portfolio
    return {
        'is_simulation': True,  # 明确标记这是模拟
        'total_value': p['total_value'],
        'cash': p['cash'],
        'holdings': p['holdings'],
        'cumulative_return': ((p['total_value'] / INITIAL_CAPITAL) - 1) * 100,
    }

@app.get("/api/paper-trading/strategy-logs")
async def paper_trading_logs(days: int = 30):
    """策略迭代日志"""
    log_dir = DATA_DIR / "paper-trading" / "strategy-logs"
    logs = []
    for f in sorted(log_dir.glob("*.json"), reverse=True)[:days]:
        logs.append(json.loads(f.read_text(encoding='utf-8')))
    return {'logs': logs}

@app.get("/api/paper-trading/graduation")
async def paper_trading_graduation():
    """毕业检查"""
    logs = load_all_strategy_logs()
    return check_graduation(logs)
```

---

## V9 前端展示（Pro Only，独立 Tab）

```javascript
// ⚠️ 只在 Pro 模式展示，不混入首页
// 底部 Tab 不变（5 个），在 AI分析 页内加子 Tab

async function renderPaperTrading() {
    const data = await apiCall('/api/paper-trading/summary');
    
    const returnPct = data.cumulative_return.toFixed(1);
    const emoji = returnPct >= 0 ? '📈' : '📉';
    
    return `
    <div class="paper-trading">
        <div class="warning-bar">🤖 这是 AI 模拟盘，不是真实资产</div>
        
        <h3>AI 练功房</h3>
        <p>虚拟本金：¥1,000,000</p>
        <p>当前净值：¥${data.total_value.toLocaleString()}</p>
        <p>${emoji} 累计收益：${returnPct}%</p>
        <p>持仓：${data.holdings.length} 只</p>
        
        <h3>策略日志（AI 的学习笔记）</h3>
        ${await renderStrategyLogs()}
        
        <h3>毕业状态</h3>
        ${await renderGraduation()}
    </div>`;
}
```

---

## V9 实施计划

| Phase | 任务 | 时间 | 产出 |
|-------|------|------|------|
| V9.1 | 模拟账户引擎 | 2 天 | `paper_trading.py` |
| V9.2 | 每日自动决策+复盘 | 2 天 | 凌晨 02:50 定时任务 |
| V9.3 | 策略迭代日志 | 1.5 天 | 日志存储 + API |
| V9.4 | A/B 策略对比 | 2 天 | 双策略并行 + 对比报告 |
| V9.5 | 毕业机制 | 1.5 天 | 毕业检查 + 策略同步 |
| V9.6 | 前端展示 | 1.5 天 | Pro 模式"AI 练功房" |
| V9.7 | 联调 | 1.5 天 | 全链路 |

## V9 验收标准

- [ ] 模拟盘数据 100% 与真实资产隔离
- [ ] 首页/推送永远不出现模拟盘数据
- [ ] AI 每日自动决策 + 执行模拟交易
- [ ] 每日自动复盘，策略日志正确写入
- [ ] A/B 测试可创建 + 自动对比
- [ ] 毕业检查逻辑正确
- [ ] Pro 模式"AI 练功房"展示正常，标明"模拟"
- [ ] 累计收益计算正确

## V9 启动条件

```
✅ V8 上线 ≥ 2 周（复盘系统稳定运行）
✅ AI 自主复盘准确率趋于稳定
✅ 用户确认"开始模拟交易"
```
