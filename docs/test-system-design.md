# MoneyBag 持久化测试体系设计

> 📅 2026-04-16 | 配套文档：`MoneyBag-全景设计文档.md`
> 🎯 目标：一个能一直用的测试系统，每次改完代码一键验证"没炸"
> 📐 原则：适配 2 用户 + 个人项目规模，不搞 CI/CD 流水线那套重型方案

---

## 一、测试体系总览

```
┌─────────────────────────────────────────────────────────────┐
│                    钱袋子测试体系                              │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ L1 健康   │  │ L2 API   │  │ L3 数据源 │  │ L4 端到端 │   │
│  │ 检查      │  │ 功能测试  │  │ 验证      │  │ 场景测试  │   │
│  │ 10秒      │  │ 2分钟    │  │ 1分钟     │  │ 5分钟     │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│       │              │              │              │         │
│       ▼              ▼              ▼              ▼         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │          scripts/test_runner.py（统一入口）           │   │
│  │          python test_runner.py --level all           │   │
│  └─────────────────────────────────────────────────────┘   │
│       │                                                     │
│       ▼                                                     │
│  ┌─────────────────────────────────────────────────────┐   │
│  │    data/test_results/YYYY-MM-DD.json（结果存档）      │   │
│  │    + /api/test/results（前端查看）                     │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 四级测试，由快到慢

| 级别 | 名称 | 耗时 | 什么时候跑 | 测什么 |
|------|------|------|-----------|--------|
| **L1** | 健康检查 | ~10秒 | 每次部署后、每天凌晨 | 服务存活 + Key 有效 + 基础 API 通 |
| **L2** | API 功能测试 | ~2分钟 | 每次改代码后 | 所有 API 端点返回正确格式 |
| **L3** | 数据源验证 | ~1分钟 | 每天凌晨 + 手动触发 | AKShare/Tushare/DeepSeek 数据可用性 + 准确性 |
| **L4** | 端到端场景 | ~5分钟 | 新功能上线前 | 完整用户场景（登录→看首页→聊天→看持仓→分析） |

---

## 二、L1 健康检查（≤3 秒，每次必跑）

> **业界最佳实践参考**：[Nurbak — Health Check Endpoint: Build It Right (2026)](https://nurbak.com/en/blog/health-check-endpoint/)
> 核心原则：**每项超时 3s + 并行执行 + 不健康返 503 + 禁止缓存**

### 后端 /api/health 改造（Phase 0 任务 1.1）

```python
# main.py — /api/health 改造为深度检查

import asyncio, time, psutil
from fastapi.responses import JSONResponse

async def _check_with_timeout(name: str, coro, timeout_s: float = 3.0) -> dict:
    """每个检查项独立超时，不会互相拖累"""
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(coro(), timeout=timeout_s)
        return {"name": name, "status": "healthy", "latency_ms": round((time.monotonic() - start) * 1000), "detail": result}
    except asyncio.TimeoutError:
        return {"name": name, "status": "unhealthy", "latency_ms": round(timeout_s * 1000), "error": f"超时（{timeout_s}s）"}
    except Exception as e:
        return {"name": name, "status": "unhealthy", "latency_ms": round((time.monotonic() - start) * 1000), "error": str(e)}

async def _check_deepseek():
    """DeepSeek API Key 有效性（发一个 max_tokens=1 的请求）"""
    # ... 现有 check_api_keys 中的 DeepSeek 部分
    return "🟢"

async def _check_tushare():
    """Tushare Token 有效性（拉 1 条交易日历）"""
    return "🟢"

async def _check_wxwork():
    """企微 Secret 有效性（获取 access_token）"""
    return "🟢"

async def _check_memory():
    """服务器内存（>90% 视为不健康）"""
    mem = psutil.virtual_memory()
    if mem.percent > 90:
        raise Exception(f"内存 {mem.percent}%，超过 90% 阈值")
    return f"{mem.percent}%"

async def _check_disk():
    """磁盘空间（<500MB 视为不健康）"""
    disk = psutil.disk_usage("/opt/moneybag")
    free_mb = disk.free / 1024 / 1024
    if free_mb < 500:
        raise Exception(f"磁盘仅剩 {free_mb:.0f}MB")
    return f"{free_mb:.0f}MB free"

@app.get("/api/health")
async def health_check():
    start = time.monotonic()
    
    # ★ 并行执行所有检查（总耗时 = 最慢的一项，而非加总）
    checks = await asyncio.gather(
        _check_with_timeout("deepseek", _check_deepseek),
        _check_with_timeout("tushare", _check_tushare),
        _check_with_timeout("wxwork", _check_wxwork),
        _check_with_timeout("memory", _check_memory),
        _check_with_timeout("disk", _check_disk),
    )
    
    is_healthy = all(c["status"] == "healthy" for c in checks)
    
    body = {
        "status": "healthy" if is_healthy else "unhealthy",
        "version": APP_VERSION,
        "timestamp": datetime.now().isoformat(),
        "total_latency_ms": round((time.monotonic() - start) * 1000),
        "checks": checks,
        "keys_status": {c["name"]: {"status": c.get("detail", "🔴"), "latency_ms": c["latency_ms"]} 
                        for c in checks if c["name"] in ("deepseek", "tushare", "wxwork")},
        "llm_usage": _get_llm_usage(),  # Token 预算（E.2）
    }
    
    # ★ 不健康时返回 503（而非 200）
    return JSONResponse(
        content=body,
        status_code=200 if is_healthy else 503,
        # ★ 禁止缓存（防止 CDN/浏览器缓存旧状态）
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
```

### L1 测试脚本（打 /api/health 验证）

```python
# scripts/tests/test_l1_health.py

L1_CHECKS = [
    # --- 深度健康检查 ---
    {"name": "服务存活",      "GET": "/api/health",  "expect": [200, 503],  # 200 或 503 都说明服务活着
     "check": lambda r: r.get("status") in ("healthy", "unhealthy")},
    {"name": "版本号",        "GET": "/api/health",  "check": lambda r: r.get("version") is not None},
    {"name": "DeepSeek Key",  "GET": "/api/health",  "check": lambda r: _key_ok(r, "deepseek")},
    {"name": "Tushare Key",   "GET": "/api/health",  "check": lambda r: _key_ok(r, "tushare")},
    {"name": "企微 Key",      "GET": "/api/health",  "check": lambda r: _key_ok(r, "wxwork")},
    {"name": "内存",          "GET": "/api/health",  "check": lambda r: _check_ok(r, "memory")},
    {"name": "磁盘",          "GET": "/api/health",  "check": lambda r: _check_ok(r, "disk")},
    {"name": "Token 预算",    "GET": "/api/health",  "check": lambda r: "llm_usage" in r},
    {"name": "延迟 <3s",      "GET": "/api/health",  "check": lambda r: r.get("total_latency_ms", 9999) < 3000},
    
    # --- 基础 API 可达 ---
    {"name": "用户列表",      "GET": "/api/profiles",                        "expect": 200},
    {"name": "首页数据",      "GET": "/api/unified-networth?userId=LeiJiang", "expect": 200},
]

def _key_ok(r, name):
    return r.get("keys_status", {}).get(name, {}).get("status") == "🟢"

def _check_ok(r, name):
    checks = r.get("checks", [])
    return any(c["name"] == name and c["status"] == "healthy" for c in checks)
```

### 运行方式

```bash
# 本地
python scripts/test_runner.py --level l1

# 服务器
ssh ubuntu@150.158.47.189 "cd /opt/moneybag && venv/bin/python scripts/test_runner.py --level l1"

# 输出示例：
# ✅ [L1] 健康检查 — 11/11 通过（2.1秒）
# ✅ 服务存活          healthy
# ✅ 版本号            v6.0.0
# ✅ DeepSeek Key      🟢 (234ms)
# ✅ Tushare Key       🟢 (156ms)
# ✅ 企微 Key          🟢 (89ms)
# ✅ 内存              67%
# ✅ 磁盘              12.3GB free
# ✅ Token 预算        ¥0.35/¥3 today
# ✅ 延迟 <3s          2104ms
# ✅ 用户列表          200 OK
# ✅ 首页数据          200 OK
```

---

## 三、L2 API 功能测试（2 分钟，改代码必跑）

### 设计思路

不写传统单元测试（你的代码没有解耦到那个程度），而是**直接打真实 API**验证返回格式。

### 测试用例

```python
# scripts/tests/test_l2_api.py

L2_TESTS = {
    # ═══ 用户与身份 ═══
    "profiles": [
        {"name": "获取用户列表",     "GET":  "/api/profiles",                "expect_keys": ["profiles"]},
    ],
    
    # ═══ 持仓 ═══
    "holdings": [
        {"name": "股票持仓列表",     "GET":  "/api/stock-holdings?userId=LeiJiang",   "expect_keys": ["holdings"]},
        {"name": "基金持仓列表",     "GET":  "/api/fund-holdings?userId=LeiJiang",    "expect_keys": ["holdings"]},
        {"name": "持仓扫描",         "GET":  "/api/stock-holdings/scan?userId=LeiJiang", "expect": 200},
        {"name": "统一净资产",       "GET":  "/api/unified-networth?userId=LeiJiang",  "expect_keys": ["totalNetWorth"]},
    ],
    
    # ═══ 资讯中心 ═══
    "insight": [
        {"name": "恐贪指数",         "GET":  "/api/fear-greed",             "expect_keys": ["score", "level"]},
        {"name": "估值百分位",       "GET":  "/api/valuation",              "expect": 200},
        {"name": "技术指标",         "GET":  "/api/technical",              "expect_keys": ["rsi"]},
        {"name": "宏观日历",         "GET":  "/api/macro-calendar",         "expect": 200},
        {"name": "全球市场",         "GET":  "/api/global-market",          "expect": 200},
        {"name": "12维信号",         "GET":  "/api/daily-signal",           "expect_keys": ["composite_score"]},
        {"name": "新闻列表",         "GET":  "/api/news",                   "expect_keys": ["news"]},
    ],
    
    # ═══ AI 聊天 ═══
    "chat": [
        {"name": "非流式聊天",       "POST": "/api/chat",                   
         "body": {"message": "你好", "userId": "LeiJiang"},
         "expect_keys": ["reply"]},
    ],
    
    # ═══ 选股选基 ═══
    "screen": [
        {"name": "基金筛选",         "GET":  "/api/fund-screen?top=3",      "expect": 200},
        {"name": "股票筛选",         "GET":  "/api/stock-screen?top=3",     "expect": 200},
    ],
    
    # ═══ 量化引擎 ═══
    "quant": [
        {"name": "因子IC检验",       "GET":  "/api/factor-ic",              "expect": 200},
        {"name": "回测",             "GET":  "/api/backtest?code=110020&months=6", "expect": 200},
    ],
    
    # ═══ 配置建议 ═══
    "allocation": [
        {"name": "推荐配置",         "GET":  "/api/recommend-alloc?profile=balanced", "expect": 200},
    ],
    
    # ═══ 风控 ═══
    "risk": [
        {"name": "风控指标",         "POST": "/api/risk-metrics",           
         "body": {"userId": "LeiJiang"},
         "expect": 200},
    ],
    
    # ═══ 基础设施 ═══
    "infra": [
        {"name": "数据审计",         "GET":  "/api/health/data-audit",      "expect": 200},
        {"name": "Token 用量",       "GET":  "/api/health",                 "check": lambda r: "llm_usage" in r},
    ],
}

# 统计：~25 个 API 端点覆盖（现有 142+ 的核心子集）
# 每个新 Phase 做完后往这里加对应的新 API 测试
```

### 新功能注册机制

```python
# 每次新增 API 时，在测试文件对应分组里加一行即可
# 例如 V6 Phase 1 新增地缘政治模块：

"geopolitical": [  # V6 Phase 1 新增
    {"name": "地缘事件列表",   "GET":  "/api/geopolitical/events",      "expect_keys": ["events"]},
    {"name": "地缘影响评估",   "GET":  "/api/geopolitical/impact",      "expect_keys": ["impact_score"]},
],
```

### 运行方式

```bash
# 跑全部 L2
python scripts/test_runner.py --level l2

# 只跑某个分组（改了哪块跑哪块）
python scripts/test_runner.py --level l2 --group holdings

# 输出示例：
# ✅ [L2] API 功能测试 — 25/25 通过（1分48秒）
# 
# 📦 profiles      1/1  ✅
# 📦 holdings      4/4  ✅
# 📦 insight       7/7  ✅
# 📦 chat          1/1  ✅
# 📦 screen        2/2  ✅
# 📦 quant         2/2  ✅
# 📦 allocation    1/1  ✅
# 📦 risk          1/1  ✅
# 📦 infra         2/2  ✅
#
# ⏱️  总耗时: 1m48s | 平均: 4.3s/项
```

---

## 四、L3 数据源验证（1 分钟，每天自动跑）

> **业界最佳实践参考**：
> - [Intrinio — Financial Data Validation Best Practices](https://intrinio.com/blog/testing-financial-data-accuracy-in-your-api-integration)：定义真实数据源 + 样本比对 + 容差阈值
> - [CSDN — AKShare 接口异常修复指南](https://blog.csdn.net/gitblog_00993/article/details/159813942)：版本检测 + 字段完整性 + 多源冗余

### 把现有 `verify_data.py` 升级为标准化格式

```python
# scripts/tests/test_l3_datasource.py

import akshare as ak
import importlib

# ═══════════════════════════════════════════
#  第一部分：环境检测（来源：AKShare 修复指南）
# ═══════════════════════════════════════════

L3_ENV_CHECKS = [
    {
        "name": "AKShare 版本",
        "test": lambda: ak.__version__,
        "validate": lambda v: _version_gte(v, "1.18.50"),  # 最低要求版本
        "on_fail": "pip install --upgrade akshare（当前版本过旧，接口可能不兼容）",
    },
    {
        "name": "Tushare 版本",
        "test": lambda: importlib.import_module("tushare").__version__,
        "validate": lambda v: _version_gte(v, "1.4.0"),
        "on_fail": "pip install --upgrade tushare",
    },
    {
        "name": "Python 版本",
        "test": lambda: f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "validate": lambda v: _version_gte(v, "3.11.0"),
        "on_fail": "需要 Python 3.11+",
    },
]

def _version_gte(current: str, minimum: str) -> bool:
    """版本号比较：1.18.55 >= 1.18.50"""
    from packaging.version import Version
    return Version(current) >= Version(minimum)


# ═══════════════════════════════════════════
#  第二部分：数据源可用性 + 合理性检查
# ═══════════════════════════════════════════

L3_DATASOURCE_CHECKS = [
    # ═══ Tushare（付费，最稳定）═══
    {
        "name": "Tushare PE-TTM",
        "source": "tushare",
        "test": "fetch_pe_ttm_399300",
        "validate": lambda v: 8 < v < 80,
        "fallback": "AKShare",
    },
    {
        "name": "Tushare 券商预测",
        "source": "tushare",
        "test": "fetch_report_rc_600519",
        "validate": lambda v: v is not None and len(v) > 0,
        "fallback": None,
    },
    
    # ═══ AKShare（免费，常变）═══
    {
        "name": "AKShare 沪深300行情",
        "source": "akshare",
        "test": "fetch_index_300",
        "validate": lambda v: 2000 < v < 10000,
        "fallback": "Tushare",
    },
    {
        "name": "AKShare 新闻",
        "source": "akshare",
        "test": "fetch_news",
        "validate": lambda v: isinstance(v, list) and len(v) > 0,
        "fallback": None,
    },
    {
        "name": "AKShare SHIBOR",
        "source": "akshare",
        "test": "fetch_shibor",
        "validate": lambda v: 0 < v < 10,
        "fallback": None,
    },
    
    # ═══ DeepSeek API ═══
    {
        "name": "DeepSeek V3 可用",
        "source": "deepseek",
        "test": "ping_v3",
        "validate": lambda v: v is not None,
        "fallback": "规则引擎",
    },
    {
        "name": "DeepSeek R1 可用",
        "source": "deepseek",
        "test": "ping_r1",
        "validate": lambda v: v is not None,
        "fallback": "V3 降级",
    },
]


# ═══════════════════════════════════════════
#  第三部分：AKShare 字段完整性检查（来源：AKShare 修复指南）
# ═══════════════════════════════════════════

L3_FIELD_INTEGRITY = [
    {
        "name": "A 股实时行情字段完整性",
        "test": "fetch_a_stock_spot",
        "required_fields": ["代码", "名称", "最新价", "涨跌幅", "成交量"],
        "min_records": 4000,  # A 股正常应有 5000+ 只
        "on_fail": "AKShare 接口可能变更，检查版本或换用 Tushare",
    },
    {
        "name": "沪深300历史数据字段",
        "test": "fetch_index_300_history",
        "required_fields": ["date", "open", "high", "low", "close", "volume"],
        "min_records": 200,
        "on_fail": "历史数据接口异常",
    },
]

# 字段完整性检查函数
def check_field_integrity(test_name, required_fields, min_records):
    """
    检查返回的 DataFrame 是否包含所有必需字段，记录数是否足够
    来源：AKShare 修复指南的"数据维度分析"方法
    """
    df = _fetch_data(test_name)
    
    # 字段检查
    missing = [f for f in required_fields if f not in df.columns]
    if missing:
        return {"passed": False, "error": f"缺失字段: {missing}"}
    
    # 记录数检查
    if len(df) < min_records:
        return {"passed": False, "error": f"记录数 {len(df)} < {min_records}（可能接口被限流）"}
    
    return {"passed": True, "detail": f"{len(df)} 条, {len(df.columns)} 字段"}


# ═══════════════════════════════════════════
#  第四部分：数据交叉验证（来源：Intrinio 金融数据最佳实践）
#  "用第二个可信源验证第一个源的数据准确性"
# ═══════════════════════════════════════════

L3_CROSS_VALIDATION = [
    {
        "name": "RSI 交叉验证",
        "source_a": "我们的计算",
        "source_b": "AKShare 原始数据手算",
        "test": "compare_rsi",
        "tolerance": 5,  # 差异 < 5 即通过
    },
    {
        "name": "布林带交叉验证",
        "source_a": "我们的计算",
        "source_b": "手算 MA20±2σ",
        "test": "compare_bollinger",
        "tolerance": 20,
    },
    {
        "name": "收盘价交叉验证（Tushare vs AKShare）",
        "source_a": "Tushare index_daily",
        "source_b": "AKShare stock_zh_index_daily_em",
        "test": "compare_close_price",
        "tolerance": 0.5,  # 差异 < ¥0.5（同一天的收盘价应该完全一致）
        "description": "两个独立数据源的沪深300最新收盘价应该一致",
    },
    {
        "name": "PE 交叉验证（Tushare vs 我们的计算）",
        "source_a": "Tushare index_dailybasic",
        "source_b": "/api/valuation 返回的 PE",
        "test": "compare_pe",
        "tolerance": 3,  # PE 百分位差异 < 3%
    },
]
```
        "name": "RSI 交叉验证",
        "source": "cross_check",
        "test": "compare_rsi_akshare_vs_ours",
        "validate": lambda diff: diff < 5,  # 差异 < 5
    },
    {
        "name": "布林带交叉验证",
        "source": "cross_check",
```

### 自动调度

```python
# 集成到 night_worker.py 的凌晨任务中

# 01:00 — 数据源健康检查（在数据预热之前跑）
async def nightly_l3_check():
    """凌晨自动跑 L3，结果存档 + 异常推企微"""
    results = await run_l3_tests()
    
    # 存档
    save_test_results("L3", results)
    
    # 有失败就推企微
    failed = [r for r in results if not r["passed"]]
    if failed:
        msg = f"⚠️ 数据源巡检 {len(failed)} 项异常：\n"
        msg += "\n".join(f"  ❌ {r['name']}: {r['error']}" for r in failed)
        await push_to_wxwork("LeiJiang", msg)
```

---

## 五、L4 端到端场景测试（5 分钟，上线前跑）

### 模拟真实用户操作流程

```python
# scripts/tests/test_l4_e2e.py

L4_SCENARIOS = [
    {
        "name": "厉害了哥的完整使用流程",
        "user_id": "LeiJiang",
        "mode": "pro",
        "steps": [
            # 1. 首页加载
            {"action": "GET", "url": "/api/unified-networth?userId=LeiJiang",
             "assert": "totalNetWorth is number"},
            {"action": "GET", "url": "/api/daily-focus?userId=LeiJiang",
             "assert": "status 200"},
            
            # 2. 看持仓
            {"action": "GET", "url": "/api/stock-holdings/scan?userId=LeiJiang",
             "assert": "status 200"},
            {"action": "GET", "url": "/api/fund-holdings/scan?userId=LeiJiang",
             "assert": "status 200"},
            
            # 3. 看资讯
            {"action": "GET", "url": "/api/fear-greed",
             "assert": "score between 0 and 100"},
            {"action": "GET", "url": "/api/daily-signal",
             "assert": "composite_score between -100 and 100"},
            
            # 4. AI 聊天
            {"action": "POST", "url": "/api/chat",
             "body": {"message": "现在适合入场吗", "userId": "LeiJiang"},
             "assert": "reply is not empty",
             "timeout": 30},
            
            # 5. 风控
            {"action": "POST", "url": "/api/risk-metrics",
             "body": {"userId": "LeiJiang"},
             "assert": "status 200"},
        ],
    },
    {
        "name": "部落格里的 Simple 模式流程",
        "user_id": "BuLuoGeLi",
        "mode": "simple",
        "steps": [
            {"action": "GET", "url": "/api/unified-networth?userId=BuLuoGeLi",
             "assert": "status 200"},
            {"action": "GET", "url": "/api/recommend-alloc?profile=balanced",
             "assert": "status 200"},
            {"action": "POST", "url": "/api/chat",
             "body": {"message": "我的基金怎么样", "userId": "BuLuoGeLi"},
             "assert": "reply is not empty",
             "timeout": 30},
        ],
    },
    {
        "name": "DeepSeek 挂了的降级场景",
        "mock": {"LLM_API_KEY": "invalid_key_for_test"},
        "steps": [
            {"action": "POST", "url": "/api/chat",
             "body": {"message": "你好", "userId": "LeiJiang"},
             "assert": "reply is not empty and source == rule_engine"},
        ],
    },
]
```

---

## 六、统一测试入口 test_runner.py

```python
#!/usr/bin/env python3
"""
钱袋子统一测试入口

用法：
  python scripts/test_runner.py --level all          # 跑全部（~8分钟）
  python scripts/test_runner.py --level l1            # 只跑健康检查（≤3秒）
  python scripts/test_runner.py --level l2            # 只跑 API 测试（2分钟）
  python scripts/test_runner.py --level l2 --group holdings  # 只跑持仓相关
  python scripts/test_runner.py --level l3            # 只跑数据源（1分钟）
  python scripts/test_runner.py --level l4            # 只跑端到端（5分钟）
  python scripts/test_runner.py --level l1,l2         # 组合跑
  python scripts/test_runner.py --level all --save    # 跑完存档
  python scripts/test_runner.py --level all --push    # 跑完推企微
  python scripts/test_runner.py --trend               # 查看最近 30 天错误率趋势
  python scripts/test_runner.py --trend --days 7      # 查看最近 7 天
"""

import argparse, json, time, httpx, os, sys
from datetime import datetime, date
from pathlib import Path

# 默认目标
API_BASE = os.getenv("TEST_API_BASE", "http://150.158.47.189:8000")

def main():
    parser = argparse.ArgumentParser(description="钱袋子测试体系")
    parser.add_argument("--level", default="l1", help="l1/l2/l3/l4/all")
    parser.add_argument("--group", default=None, help="L2 分组名")
    parser.add_argument("--base", default=API_BASE, help="API 地址")
    parser.add_argument("--save", action="store_true", help="结果存档")
    parser.add_argument("--push", action="store_true", help="推企微")
    parser.add_argument("--local", action="store_true", help="测本地 localhost:8000")
    args = parser.parse_args()
    
    if args.local:
        args.base = "http://localhost:8000"
    
    levels = args.level.split(",") if "," in args.level else (
        ["l1", "l2", "l3", "l4"] if args.level == "all" else [args.level]
    )
    
    all_results = {}
    total_pass = 0
    total_fail = 0
    start = time.time()
    
    for level in levels:
        results = run_level(level, args.base, args.group)
        all_results[level] = results
        passed = sum(1 for r in results if r["passed"])
        failed = len(results) - passed
        total_pass += passed
        total_fail += failed
        
        # 打印结果
        icon = "✅" if failed == 0 else "❌"
        print(f"\n{icon} [{level.upper()}] {passed}/{len(results)} 通过")
        for r in results:
            status = "✅" if r["passed"] else "❌"
            detail = r.get("detail", "")
            print(f"  {status} {r['name']:20s} {detail}")
    
    elapsed = time.time() - start
    print(f"\n{'='*50}")
    print(f"  总计: {total_pass} 通过 / {total_fail} 失败 / {elapsed:.1f}秒")
    print(f"{'='*50}")
    
    # 存档
    if args.save:
        save_results(all_results, elapsed)
        print(f"  📁 结果已存档: data/test_results/{date.today().isoformat()}.json")
    
    # 推企微
    if args.push and total_fail > 0:
        push_wxwork_alert(all_results, total_pass, total_fail)
    
    sys.exit(1 if total_fail > 0 else 0)


def run_level(level, base, group=None):
    """运行指定级别的测试"""
    if level == "l1":
        from tests.test_l1_health import run_l1
        return run_l1(base)
    elif level == "l2":
        from tests.test_l2_api import run_l2
        return run_l2(base, group)
    elif level == "l3":
        from tests.test_l3_datasource import run_l3
        return run_l3(base)
    elif level == "l4":
        from tests.test_l4_e2e import run_l4
        return run_l4(base)


def save_results(results, elapsed):
    """存档到 data/test_results/"""
    dir_path = Path("data/test_results")
    dir_path.mkdir(parents=True, exist_ok=True)
    filepath = dir_path / f"{date.today().isoformat()}.json"
    
    # 追加模式（一天可能跑多次）
    existing = []
    if filepath.exists():
        with open(filepath, "r") as f:
            existing = json.load(f)
    
    existing.append({
        "time": datetime.now().isoformat(),
        "elapsed": round(elapsed, 1),
        "results": results,
    })
    
    with open(filepath, "w") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
```

---

## 七、前端展示：Pro 模式「系统体检」页

### API 端点

```python
# main.py 新增

@app.get("/api/test/results")
async def get_test_results(days: int = 7):
    """获取最近 N 天的测试结果"""
    results_dir = Path("data/test_results")
    results = []
    for i in range(days):
        d = date.today() - timedelta(days=i)
        filepath = results_dir / f"{d.isoformat()}.json"
        if filepath.exists():
            with open(filepath, "r") as f:
                day_results = json.load(f)
                results.append({"date": d.isoformat(), "runs": day_results})
    return {"test_results": results}

@app.post("/api/test/run")
async def trigger_test(level: str = "l1"):
    """Pro 模式手动触发测试（限 L1/L2，不允许前端触发 L3/L4）"""
    if level not in ("l1", "l2"):
        raise HTTPException(400, "前端只能触发 L1/L2 测试")
    # 异步跑，不阻塞
    asyncio.create_task(_run_test_async(level))
    return {"status": "started", "level": level}
```

### 前端 UI（Pro 模式专属）

```
┌─────────────────────────────────────────┐
│  🔬 系统体检                    [跑一次]  │
│                                         │
│  ┌─────────────────────────────────────┐│
│  │ 今日 14:15                          ││
│  │ L1 健康检查    ✅ 8/8    9.2秒      ││
│  │ L2 API测试     ✅ 25/25  1m48s      ││
│  │ L3 数据源      ✅ 10/10  52s        ││
│  │ L4 端到端      ✅ 3/3    4m22s      ││
│  └─────────────────────────────────────┘│
│                                         │
│  ┌─────────────────────────────────────┐│
│  │ 昨日 01:00（凌晨自动）              ││
│  │ L1 ✅  L2 ✅  L3 ⚠️ 1项异常        ││
│  │  └ ❌ AKShare SHIBOR: 接口超时      ││
│  └─────────────────────────────────────┘│
│                                         │
│  📊 最近 7 天: ✅✅✅⚠️✅✅✅          │
└─────────────────────────────────────────┘
```

---

## 八、使用工作流

### 日常开发（每次改代码后）

```bash
# 1. 改完代码，先本地跑 L1+L2
python scripts/test_runner.py --level l1,l2 --local

# 2. 全通过 → 部署服务器
# 3. 部署后跑线上 L1
python scripts/test_runner.py --level l1

# 4. 通过 → 安心收工
```

### 新 Phase 上线

```bash
# 1. 先把新 API 的测试加到 test_l2_api.py
# 2. 跑全量测试
python scripts/test_runner.py --level all --save --push

# 3. 如果有失败 → 修 → 再跑
# 4. 全通过 → git commit + push
```

### 凌晨自动巡检

```bash
# cron / systemd timer
# 01:00 每天自动跑 L1+L3，失败推企微
0 1 * * * cd /opt/moneybag && venv/bin/python scripts/test_runner.py --level l1,l3 --save --push
```

---

## 九、错误率趋势分析（来源：Intrinio 最佳实践）

> **"追踪错误率随时间变化的趋势，分析模式——AKShare 是否比 Tushare 更容易出错？错误是否集中在非交易时段？"**

### 命令行使用

```bash
# 最近 30 天趋势
python scripts/test_runner.py --trend

# 最近 7 天
python scripts/test_runner.py --trend --days 7

# 输出示例：
# 📊 最近 30 天测试趋势（2026-03-18 ~ 2026-04-16）
#
# 日期         L1    L2      L3     总体
# 2026-04-16  ✅    ✅      ✅     ✅
# 2026-04-15  ✅    ✅      ⚠️1    ⚠️
# 2026-04-14  ✅    ✅      ✅     ✅
# ...
#
# 📈 统计摘要：
# ├── 总体通过率: 93.3%（28/30 天）
# ├── L1 通过率: 100%
# ├── L2 通过率: 100%
# ├── L3 通过率: 80%（6 次失败）
# │   ├── AKShare SHIBOR: 4 次失败（最不稳定！）
# │   ├── AKShare 新闻: 2 次失败
# │   └── 失败集中在: 周末/节假日（非交易时段）
# └── L4 通过率: 100%
#
# 🔍 建议：
# ├── AKShare SHIBOR 失败率 13.3%，建议加 Tushare 降级源
# └── 非交易时段 L3 可跳过行情类检查
```

### 实现逻辑

```python
# scripts/test_runner.py — 趋势分析

def show_trend(days: int = 30):
    """分析最近 N 天的测试结果趋势"""
    results_dir = Path("data/test_results")
    
    daily_stats = []
    fail_counter = {}  # {"AKShare SHIBOR": 4, ...}
    
    for i in range(days):
        d = date.today() - timedelta(days=i)
        filepath = results_dir / f"{d.isoformat()}.json"
        if not filepath.exists():
            daily_stats.append({"date": d, "status": "⬜", "detail": "未跑"})
            continue
        
        with open(filepath, "r") as f:
            runs = json.load(f)
        
        # 取最后一次运行结果
        last_run = runs[-1]
        all_passed = True
        for level, checks in last_run.get("results", {}).items():
            for check in checks:
                if not check.get("passed"):
                    all_passed = False
                    name = check.get("name", "unknown")
                    fail_counter[name] = fail_counter.get(name, 0) + 1
        
        daily_stats.append({
            "date": d, 
            "status": "✅" if all_passed else "⚠️",
        })
    
    # 打印日历视图
    print(f"\n📊 最近 {days} 天测试趋势")
    for s in daily_stats[:14]:  # 只显示最近 14 天的日历
        print(f"  {s['date']}  {s['status']}")
    
    # 统计摘要
    total = len([s for s in daily_stats if s["status"] != "⬜"])
    passed = len([s for s in daily_stats if s["status"] == "✅"])
    print(f"\n📈 通过率: {passed}/{total}（{passed/total*100:.0f}%）" if total > 0 else "")
    
    # 失败排行
    if fail_counter:
        print(f"\n🔴 失败排行（最不稳定的检查项）：")
        for name, count in sorted(fail_counter.items(), key=lambda x: -x[1]):
            print(f"  {count}次  {name}")
    
    # 自动建议
    for name, count in fail_counter.items():
        rate = count / total * 100 if total > 0 else 0
        if rate > 10:
            print(f"\n💡 建议：{name} 失败率 {rate:.0f}%，考虑加备用数据源或调整检查策略")
```

### 前端趋势展示（Pro 模式体检页）

```
┌─────────────────────────────────────────┐
│  📊 最近 30 天                           │
│  ✅✅✅⚠️✅✅✅✅✅✅⚠️✅✅✅✅✅✅✅✅✅✅✅✅✅✅⚠️✅✅✅✅│
│  通过率 90% | L3 最弱（AKShare SHIBOR）   │
│                                         │
│  🔴 不稳定排行：                          │
│  ├── AKShare SHIBOR    4/30 失败         │
│  └── AKShare 新闻      2/30 失败         │
└─────────────────────────────────────────┘
```

---

## 十、业界参考汇总（补全后的改进清单）

| # | 遗漏点 | 来源 | 修复状态 |
|---|--------|------|---------|
| 1 | Health 每项 3 秒超时 | Nurbak 2026 | ✅ 已补入 L1 `_check_with_timeout` |
| 2 | Health 并行执行 | Nurbak 2026 | ✅ 已补入 L1 `asyncio.gather` |
| 3 | 不健康返 503 | Nurbak 2026 | ✅ 已补入 L1 `status_code=503` |
| 4 | 禁止缓存 | Nurbak 2026 | ✅ 已补入 L1 `Cache-Control: no-cache` |
| 5 | 金融数据交叉验证 | Intrinio 2025 | ✅ 已补入 L3 收盘价+PE 双源验证 |
| 6 | AKShare 版本+字段完整性 | CSDN/AKShare 2026 | ✅ 已补入 L3 环境检测+字段检查 |
| 7 | 错误率趋势分析 | Intrinio 2025 | ✅ 已补入 `--trend` 命令 |

---

## 十一、文件结构

```
backend/
├── scripts/
│   ├── test_runner.py          ← 统一入口（新建）
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_l1_health.py   ← L1 健康检查（新建）
│   │   ├── test_l2_api.py      ← L2 API 测试（新建）
│   │   ├── test_l3_datasource.py ← L3 数据源（升级自 verify_data.py）
│   │   └── test_l4_e2e.py      ← L4 端到端（新建）
│   ├── verify_data.py          ← 保留，L3 内部复用
│   ├── cache_warmer.py         ← 现有
│   └── stock_monitor_cron.py   ← 现有
├── data/
│   └── test_results/           ← 测试结果存档（新建目录）
│       ├── 2026-04-16.json
│       └── ...
```

---

## 十二、实施计划

### 放入哪个步骤

| 任务 | 放入 | 工时 | 前置依赖 |
|------|------|------|---------|
| **test_runner.py + L1 健康检查** | Phase 0 Day 1（1.1 数据源巡检脚本 合并） | 1h | 无 |
| **L2 API 测试（核心 25 个）** | Phase 0 Day 3（最后一天，所有 API 接完再写测试） | 1.5h | Day 2 完成 |
| **L3 数据源验证** | Phase 0 Day 1（升级现有 verify_data.py） | 0.5h | 无 |
| **L4 端到端场景** | V6 Phase 4 完成后 | 1h | V6 全部完成 |
| **前端体检页** | Phase 0 Day 3（跟 Pro 模式一起做） | 0.5h | L1/L2 完成 |
| **凌晨自动巡检 cron** | Phase 0 部署时一起配 | 0.5h | test_runner 完成 |
| **总计** | | **5h** | |

### 每个 Phase 的测试责任

| Phase | 测试动作 |
|-------|---------|
| Phase 0 完成 | 建好 L1+L2+L3 + 跑一次全量 → 存档为"基线" |
| V6 每个子 Phase | 往 L2 加新 API 测试 → 跑 L1+L2 |
| V6 全部完成 | 建好 L4 → 跑全量 |
| V6.5 / V7 | 往 L2 加新 API → 跑全量 |
| V8 | 加复盘相关测试 → 跑全量 |

---

## 十三、验证清单

| # | 验证项 | 方法 | 通过标准 |
|---|--------|------|---------|
| ① | test_runner 可运行 | `python scripts/test_runner.py --level l1 --local` | 退出码 0 |
| ② | L1 并行+超时 | 故意让 Tushare 慢（改 host），看总耗时 | 总耗时 ≤ 3秒（不是 30 秒） |
| ③ | L1 不健康返 503 | 清空 LLM_API_KEY → 调 /api/health | HTTP 503 + status=unhealthy |
| ④ | L1 no-cache | `curl -v /api/health` 看 header | 有 `Cache-Control: no-cache` |
| ⑤ | L2 覆盖核心 API | 数 L2_TESTS 里的条目 | ≥ 25 个 |
| ⑥ | L3 版本检测 | 改 AKShare 最低版本为 99.0.0 | 检测到版本过旧 + 输出升级命令 |
| ⑦ | L3 字段完整性 | 跑 L3 字段检查 | A 股 ≥4000 条 + 5 个必需字段 |
| ⑧ | L3 交叉验证 | 跑收盘价对比（Tushare vs AKShare） | 差异 < ¥0.5 |
| ⑨ | 结果存档 | `--save` 后检查 `data/test_results/` | JSON 文件正确 |
| ⑩ | 趋势分析 | `--trend --days 7` | 输出通过率 + 失败排行 |
| ⑪ | 企微告警 | `--push` + 故意让一个测试失败 | 收到 ⚠️ 推送 |
| ⑫ | 前端体检页 | Pro 模式打开 | 显示测试历史 + 趋势条 + 可手动触发 L1 |
| ⑬ | cron 配置 | `crontab -l` | 01:00 自动跑 L1+L3 |
| ⑭ | 新 API 注册 | V6 Phase 1 加地缘 API 后跑 L2 | 新测试出现在结果中 |

---

## 十四、铁律合规

| 铁律 | 状态 |
|------|------|
| **#18** 后端做了前端必须接 | ✅ `/api/test/results` + `/api/test/run` → Pro 体检页 |
| **#3** 改完立即验证 | ✅ 这就是为"改完验证"服务的基础设施 |
| **#5** linter ≠ 编译器 | ✅ L2 直接打真实 API，不是 mock |
| **#9** 最小可用先交 | ✅ Phase 0 先建 L1+L2+L3，L4 到 V6 再建 |
| **代码组织约束** | ✅ 测试代码独立在 `scripts/tests/` 目录，不污染 services/ |
