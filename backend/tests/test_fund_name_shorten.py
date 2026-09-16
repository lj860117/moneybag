"""
基金名截断括号配平回归测试（2026-09-16 线上事故）
=================================================

背景：晨报「持仓明细」原先对基金名做裸 `name[:12]` 盲截。名称 ≥13 字且第
12 个字符落在括号内时留下半截括号：

    浦银安盛全球智能科技(QDII)A  →  浦银安盛全球智能科技(Q

后果（生产实测）：① 用户看到残缺基金名，丢掉 QDII 这个关键信息；② 括号不
闭合，`scripts/daily_push_quality_check.py::check_truncation()` 统计
开/闭括号数量不等，09-16 整篇晨报被判「⚠️ 括号不匹配：开放 30，闭合 28」，
质量 score=90 FAIL。

2026-09-16 修复范围（**三处调用点，本文件全部覆盖**）：
  1. scripts/night_worker.py 持仓明细 —— `_shorten_fund_name(r["name"], 12)`
  2. api/shared_helpers.py 选基推荐 TOP3 —— 原先是裸 `f.get('name','')[:12]`
  3. scripts/monthly_report.py 家庭重叠基金 —— 原先是裸 `h["name"][:6]`
     （limit=6 比 12 更容易断在括号里，属于本次补修的遗漏点）

三处共用 `services/fund_name_util.py` 里的**同一份实现**；`api/` 不允许反向
import `scripts/`（scripts 带 CLI 副作用），所以实现下沉到 services 层。

设计原则（与本仓其它回归测试一致）：
1. **不复制实现**。所有用例都调用真实的 `shorten_fund_name`（实现按路径
   importlib 加载，night_worker 按路径加载），实现一改测试立刻能感知。
2. **质检口径复用真实实现**。括号是否"算失衡"直接用 `check_truncation()`，
   而不是在测试里另写一份计数规则。
3. **带故障注入**。用 monkeypatch 把辅助函数换成"裸 `[:12]` 不配平"的退化
   版本，断言同一套不变式**必须变红** —— 否则说明断言是恒绿的死测试。
   源码护栏另有源码级故障注入（把调用点源码退化为裸切片，断言护栏必报）。

注意：本文件不需要数据隔离 fixture —— backend/tests/conftest.py 已在模块顶层
把 DATA_DIR 指向 pytest 会话专属临时目录。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import List, Optional

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

NIGHT_WORKER_PATH = BACKEND_DIR / "scripts" / "night_worker.py"
MONTHLY_REPORT_PATH = BACKEND_DIR / "scripts" / "monthly_report.py"
SHARED_HELPERS_PATH = BACKEND_DIR / "api" / "shared_helpers.py"
FUND_NAME_UTIL_PATH = BACKEND_DIR / "services" / "fund_name_util.py"

from scripts.daily_push_quality_check import check_truncation  # noqa: E402


# ============================================================
# 语料
# ============================================================
# 09-16 线上晨报里真实出现的持仓名（前两条即事故受害者）
PROD_NAMES_0916: List[str] = [
    "浦银安盛全球智能科技(QDII)A",
    "华夏全球科技先锋混合(QDII)A(人民币)",
    "东方惠新灵活配置混合C",
    "华夏先进制造龙头混合A",
]

# 额外语料：覆盖全角括号、括号在末尾、短名、超长名
EXTRA_NAMES: List[str] = [
    "易方达亚洲精选股票（QDII）A",
    "华夏纳斯达克100ETF联接(QDII)A",
    "天弘中证食品饮料ETF联接A",
    "广发纳斯达克100指数A（人民币份额）",
    "华宝标普美国品质消费人民币A",
    "A",
]

ALL_NAMES: List[str] = PROD_NAMES_0916 + EXTRA_NAMES

# 半角/全角括号配对表（测试**独立**维护，不 import 实现的常量，
# 避免实现把配对表改错时测试跟着一起错）
_BRACKET_PAIRS = {"(": ")", "（": "）"}


def _is_balanced(text: str) -> bool:
    """栈式判定：括号必须按开闭顺序配平，且不允许半角/全角混配。

    比"开闭数量相等"更严 —— `ABC)DEF(` 数量相等但顺序错乱，仍算不配平。
    """
    stack: List[str] = []
    for ch in text:
        if ch in _BRACKET_PAIRS:
            stack.append(ch)
        elif ch in _BRACKET_PAIRS.values():
            if not stack or _BRACKET_PAIRS[stack.pop()] != ch:
                return False
    return not stack


# ============================================================
# 加载被测脚本
# ============================================================
def _load_script(path: Path, mod_name: str):
    """以文件路径方式加载 scripts/ 下的脚本。

    它们是 scripts/ 下的**脚本**而不是包内模块，用普通 import 需要把 scripts/
    塞进 sys.path（会污染整场 pytest 的模块解析）。改用 importlib 按路径加载：
    脚本内部自带的 sys.path 引导会保证 `import config` 正常。
    """
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def night_worker():
    """被测脚本 scripts/night_worker.py（调用点 1：持仓明细 limit=12）。"""
    return _load_script(NIGHT_WORKER_PATH, "_mb_night_worker_shorten_sut")


@pytest.fixture(scope="module")
def monthly_report():
    """被测脚本 scripts/monthly_report.py（调用点 3：家庭重叠基金 limit=6）。"""
    return _load_script(MONTHLY_REPORT_PATH, "_mb_monthly_report_shorten_sut")


@pytest.fixture(scope="module")
def fund_name_util():
    """共享实现本体 services/fund_name_util.py。

    这里必须用**普通 import**而不是按路径加载：三处调用点都是通过
    `from services.fund_name_util import shorten_fund_name` 拿的函数对象，
    按路径 importlib 加载会得到另一个 module 实例（函数对象不相等），
    "三处共用同一份实现"的恒等断言就永远为真、失去意义。
    """
    import services.fund_name_util as fu  # noqa: PLC0415
    return fu


@pytest.fixture(scope="module")
def shared_helpers():
    """被测模块 api/shared_helpers.py（调用点 2：选基推荐 TOP3 limit=12）。

    它在包内（`backend/api/`），backend/ 已在 sys.path 里，直接 import 即可。
    """
    from api import shared_helpers as sh  # noqa: PLC0415  (延迟导入，避开 config 时序)
    return sh


@pytest.fixture
def shorten(night_worker):
    """被测函数：`_shorten_fund_name(name, limit=12)`。"""
    return night_worker._shorten_fund_name


def _naive_slice_shorten(name: Optional[str], limit: int = 12) -> str:
    """故障注入用的退化实现：即修复前的裸 `[:12]`，不配平括号。"""
    return (name or "")[:limit]


# ============================================================
# 1. 核心行为：截断 + 括号配平
# ============================================================
def test_broken_half_width_bracket_is_dropped(shorten):
    """事故本尊：半角 `(QDII)` 被盲截成 `(Q`，必须回退到 `浦银安盛全球智能科技`。"""
    out = shorten("浦银安盛全球智能科技(QDII)A")
    assert out == "浦银安盛全球智能科技", (
        f"得到 {out!r} —— 盲截未回退，09-16「浦银安盛全球智能科技(Q」事故复发"
    )
    assert _is_balanced(out)


def test_broken_full_width_bracket_is_dropped(shorten):
    """全角 `（QDII）` 同样处理，不许留下 `（Q`。"""
    out = shorten("华夏全球科技先锋混合（QDII）A")
    assert out == "华夏全球科技先锋混合", f"得到 {out!r} —— 全角括号未被配平"
    assert _is_balanced(out)


def test_prod_0916_victim_names(shorten):
    """09-16 两条受害者基金名的最终产物（含第二条的 `(人民币)` 尾巴）。"""
    assert shorten("浦银安盛全球智能科技(QDII)A") == "浦银安盛全球智能科技"
    assert shorten("华夏全球科技先锋混合(QDII)A(人民币)") == "华夏全球科技先锋混合"


@pytest.mark.parametrize(
    "name",
    [
        "东方惠新灵活配置混合C",     # 11 字
        "华夏先进制造龙头混合A",     # 11 字
        "天弘中证食品饮料ETF联接A",  # 13 字但第 12 字不在括号内
        "易方达黄金ETF联接A",       # 短名
        "A",                       # 极短名
    ],
)
def test_short_or_balanced_name_returned_as_is(shorten, name):
    """短于 limit、或截断后本身括号就配平的名称必须原样返回，不做无谓截断。"""
    out = shorten(name)
    assert out == name[:12], f"{name!r} 被改成 {out!r} —— 不该动的名字被改了"
    assert _is_balanced(out)


@pytest.mark.parametrize("name", ["易方达亚洲精选股票（QDII）A", "华宝标普美国品质消费人民币A"])
def test_plain_long_name_is_hard_capped(shorten, name):
    """无括号长名：截到 limit 且是原名的前缀。"""
    out = shorten(name)
    assert len(out) <= 12, f"{name!r} 截出 {len(out)} 字，超过 limit=12"
    assert name.startswith(out), f"{out!r} 不是 {name!r} 的前缀"


@pytest.mark.parametrize("empty_value", ["", None])
def test_empty_and_none_return_empty(shorten, empty_value):
    """空串 / None 必须返回空串（由调用方回落显示基金代码），不得抛异常。"""
    assert shorten(empty_value) == ""


@pytest.mark.parametrize(
    "name, expected",
    [
        ("（QDII）", "（QDII）"),  # 全是括号但自身配平 → 原样
        ("(QDII)", "(QDII)"),     # 半角同理
        ("(((( ", ""),            # 只有开括号 → 全被吃光，回落代码
        ("））））", ""),          # 只有闭括号 → 同上
        ("(QDII）", ""),          # 半角开 + 全角闭（混配）→ 不配平
    ],
)
def test_all_bracket_names(shorten, name, expected):
    """名称全是括号的极端边界：配平则留，不配平则清空回落代码。"""
    assert shorten(name) == expected, f"{name!r} 期望 {expected!r}"


@pytest.mark.parametrize("limit", [1, 2, 3, 5, 8, 12, 20, 50])
def test_limit_always_respected_and_balanced(shorten, limit):
    """任意 limit 下：长度不超、且产物永远括号配平。"""
    for name in ALL_NAMES:
        out = shorten(name, limit)
        assert len(out) <= limit, f"limit={limit} 时 {name!r} 截出 {len(out)} 字"
        assert _is_balanced(out), f"limit={limit} 时 {name!r} → {out!r} 括号不配平"


def test_default_limit_is_twelve(shorten):
    """默认宽度必须是 12 —— 不得为了躲括号问题偷偷放宽（推送已逼近 4096 字节）。"""
    assert shorten("浦银安盛全球智能科技人民币精选份额A") == "浦银安盛全球智能科技人民"


# ============================================================
# 2. 全语料不变式
# ============================================================
def test_all_names_produce_balanced_output(shorten):
    """全部语料：产物括号必须配平（这条断言的"活性"由故障注入用例保证）。"""
    offenders = [
        (name, shorten(name))
        for name in ALL_NAMES
        if not _is_balanced(shorten(name))
    ]
    assert not offenders, f"以下基金名截出未闭合括号：{offenders}"


def test_rendered_holdings_lines_pass_quality_check(shorten):
    """端到端：按生产格式渲染整段持仓明细，`check_truncation()` 不得报括号不匹配。

    这是 09-16 的 FAIL 现场 —— 两条 `(Q` 让全文「开放 30，闭合 28」。
    """
    lines = [
        f"  • {shorten(name)}({code})  买入3.480 → 现3.435  ▼1.3%  ¥97.9"
        for name, code in zip(PROD_NAMES_0916, ["006555", "005698", "001198", "011369"])
    ]
    content = "持仓明细：\n" + "\n".join(lines)
    issues = check_truncation(content)
    assert not [i for i in issues if "括号不匹配" in i], (
        f"质检仍报括号问题: {issues}\n渲染产物:\n{content}"
    )


# ============================================================
# 3. 故障注入：证明上面的断言是活的
# ============================================================
def test_fault_injection_naive_slice_breaks_balance_invariant(night_worker, monkeypatch):
    """注入退化实现（裸 `[:12]`）后，第 2 节的不变式必须**变红**。

    若这条用例哪天变成"退化实现也能通过"，说明上面的断言已经退化成恒绿。
    """
    monkeypatch.setattr(
        night_worker, "_shorten_fund_name", _naive_slice_shorten, raising=True
    )
    degraded = night_worker._shorten_fund_name
    offenders = [
        (name, degraded(name)) for name in ALL_NAMES if not _is_balanced(degraded(name))
    ]
    assert offenders, (
        "退化实现（裸切片）竟然没截出不闭合括号 —— 第 2 节断言是恒绿的死测试，"
        "语料或 _is_balanced 出了问题"
    )
    # 顺带锁定事故现场：退化实现必须恰好复现 `浦银安盛全球智能科技(Q`
    assert degraded("浦银安盛全球智能科技(QDII)A") == "浦银安盛全球智能科技(Q"


def test_fault_injection_naive_slice_trips_quality_check(night_worker, monkeypatch):
    """注入退化实现后，质检 `check_truncation()` 必须报「括号不匹配」。"""
    monkeypatch.setattr(
        night_worker, "_shorten_fund_name", _naive_slice_shorten, raising=True
    )
    degraded = night_worker._shorten_fund_name
    lines = [
        f"  • {degraded(name)}({code})  买入3.480 → 现3.435  ▼1.3%  ¥97.9"
        for name, code in zip(PROD_NAMES_0916, ["006555", "005698", "001198", "011369"])
    ]
    issues = check_truncation("持仓明细：\n" + "\n".join(lines))
    assert [i for i in issues if "括号不匹配" in i], (
        f"退化实现渲染出的内容质检却没报警，说明 check_truncation 的括号口径已变：{issues}"
    )


# ============================================================
# 4. 共享实现本体：services/fund_name_util.py
# ============================================================
def test_shared_util_exposes_same_semantics_as_night_worker(night_worker, fund_name_util):
    """三处调用点必须共用同一份实现：night_worker 里的名字就是共享模块的那个函数。

    若哪天有人为了"api 不方便 import"而在 night_worker 里另抄一份，这个
    恒等断言会立刻变红。
    """
    assert night_worker._shorten_fund_name is fund_name_util.shorten_fund_name, (
        "night_worker._shorten_fund_name 不是 services.fund_name_util.shorten_fund_name "
        "—— 实现被复制了一份，修一处漏两处又会重演"
    )


def test_shared_util_used_by_api_layer(shared_helpers, fund_name_util):
    """api 层引用的是共享模块里的同一个函数对象（不是本地副本）。"""
    assert shared_helpers.shorten_fund_name is fund_name_util.shorten_fund_name, (
        "api/shared_helpers.shorten_fund_name 与共享实现不是同一对象"
    )


def test_shared_util_used_by_monthly_report(monthly_report, fund_name_util):
    """scripts/monthly_report.py 引用的也是共享模块里的同一个函数对象。"""
    assert monthly_report.shorten_fund_name is fund_name_util.shorten_fund_name, (
        "scripts/monthly_report.shorten_fund_name 与共享实现不是同一对象"
    )


@pytest.mark.parametrize(
    "name, expected",
    [
        # limit=6 比 12 更容易断在括号里 —— 这是本次补修 monthly_report 的动机
        ("南方原油(QDII-FOF)A", "南方原油"),      # [:6] == "南方原油(" → 回退
        ("南方原油（QDII-FOF）A", "南方原油"),    # 全角同理
        ("易方达原油(QDII)A", "易方达原油"),      # [:6] == "易方达原油(" → 回退
        ("华夏全球科技先锋混合(QDII)A", "华夏全球科技"),  # [:6] 本身配平，不动
    ],
)
def test_limit_six_never_leaves_half_bracket(shorten, name, expected):
    """limit=6（家庭重叠基金用）截断后不得留下半截括号。"""
    out = shorten(name, 6)
    assert out == expected, f"limit=6 时 {name!r} 期望 {expected!r}，实际 {out!r}"
    assert len(out) <= 6
    assert _is_balanced(out), f"{name!r} → {out!r} 括号不配平"


def test_limit_six_all_bracket_name_falls_back_to_code(shorten):
    """limit=6 下名字被括号吃光 → 返回空串，由调用方回落基金代码。"""
    # 半角开 + 全角闭（混配）：栈判定下第一个可配平位置是 0 → 空串
    assert shorten("（QDII)A", 6) == ""
    assert shorten("", 6) == "" and shorten(None, 6) == ""


# ============================================================
# 5. 调用点 2：api/shared_helpers.py 选基推荐 TOP3
# ============================================================
# 选基缓存里真实存在过的形态（name 可能缺、可能带 (QDII)）
TOP3_FUNDS = [
    {"code": "006555", "name": "浦银安盛全球智能科技(QDII)A", "score": 88.4, "returns": {"1y": 12.3}},
    {"code": "161125", "name": "", "score": 70.0, "returns": {"1y": 5.0}},  # 缺名 → 回落代码
    {"code": "000216", "name": "华宝标普美国品质消费人民币A", "score": 65.0, "returns": {"1y": 3.2}},
]

# _build_market_context 里会在 try/except 中调用的取数函数：单测里一律打成"联网即炸"，
# 只留选基 TOP3 这一段真实执行（打不到的分支 = 空行，不影响断言）
_MARKET_DATA_FUNCS = [
    "get_fear_greed_index", "get_valuation_percentile", "get_technical_indicators",
    "get_fund_news", "get_market_news", "get_macro_calendar", "get_northbound_flow",
    "get_margin_trading", "get_shibor", "get_dividend_yield", "get_news_sentiment_score",
    "get_policy_news", "analyze_news_impact", "calc_smart_dca",
]
# 函数体内延迟 import 的那些（monkeypatch 源模块属性即可，因为 import 发生在调用时）
_LAZY_FUNCS = [
    ("services.global_market", "get_global_snapshot"),
    ("services.policy_data", "get_policy_summary_for_context"),
    ("services.macro_v8", "get_v8_macro_summary"),
    ("services.market_factors", "get_commodity_prices"),
    ("services.market_factors", "get_etf_fund_flow"),
    ("services.sector_rotation", "get_sector_ranking"),
    ("services.precomputed_cache", "get_precomputed"),
]


@pytest.fixture
def market_ctx_env(shared_helpers, monkeypatch, tmp_path):
    """把 _build_market_context 里除「选基 TOP3」外的取数全部隔离掉。"""
    def _boom(*_a, **_k):
        raise RuntimeError("单测禁止联网/读行情")

    for fn in _MARKET_DATA_FUNCS:
        if hasattr(shared_helpers, fn):
            monkeypatch.setattr(shared_helpers, fn, _boom, raising=True)
    # 这段**不在** try 里：必须给一个"无净值"的返回值，否则整个函数会抛
    monkeypatch.setattr(
        shared_helpers, "get_fund_nav",
        lambda code: {"nav": "N/A", "change": "0"}, raising=True,
    )
    for mod_name, fn in _LAZY_FUNCS:
        mod = importlib.import_module(mod_name)
        monkeypatch.setattr(mod, fn, _boom, raising=True)

    # 缓存隔离：别把污染写进真实 DATA_DIR / 内存缓存
    from infra.cache import MemoryCache  # noqa: PLC0415
    monkeypatch.setattr(shared_helpers, "_market_ctx_cache", MemoryCache(default_ttl=300), raising=True)
    monkeypatch.setattr(
        shared_helpers, "_MARKET_CTX_FILE", str(tmp_path / "market_context.txt"), raising=True
    )

    import config  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    cache_dir = Path(config.DATA_DIR) / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "fund_screen_all_score_TestUser.json").write_text(
        _json.dumps({"data": {"funds": TOP3_FUNDS}}, ensure_ascii=False), encoding="utf-8"
    )
    return shared_helpers


def test_shared_helpers_top3_line_has_no_half_bracket(market_ctx_env):
    """选基 TOP3 渲染出的名字：括号配平、过质检；缺名时回落基金代码。"""
    ctx = market_ctx_env._build_market_context()
    top3 = [ln for ln in ctx.splitlines() if ln.startswith("  - ")]
    assert top3, f"选基 TOP3 没渲染出来（缓存没读到？）：\n{ctx}"

    assert "浦银安盛全球智能科技" in ctx, f"QDII 名被截没了：\n{ctx}"
    assert "浦银安盛全球智能科技(Q" not in ctx, f"裸切片（(Q）复发：\n{ctx}"
    assert "161125" in ctx, f"缺名基金未回落代码：\n{ctx}"

    issues = check_truncation(ctx)
    assert not [i for i in issues if "括号不匹配" in i], f"质检仍报括号问题: {issues}\n{ctx}"


def test_fault_injection_shared_helpers_bare_slice_trips_quality_check(market_ctx_env, monkeypatch):
    """把 api 层的共享实现**退化成裸 `[:12]`**，质检必须报警 —— 证明上条断言是活的。"""
    monkeypatch.setattr(market_ctx_env, "shorten_fund_name", _naive_slice_shorten, raising=True)
    ctx = market_ctx_env._build_market_context()
    assert "浦银安盛全球智能科技(Q" in ctx, "退化实现没复现事故现场（(Q），语料或调用点已变"
    issues = check_truncation(ctx)
    assert [i for i in issues if "括号不匹配" in i], (
        f"退化实现渲染出的内容质检却没报警：{issues}\n{ctx}"
    )


# ============================================================
# 6. 调用点 3：scripts/monthly_report.py 家庭重叠基金（limit=6）
# ============================================================
FAMILY_HOLDINGS = [
    {"code": "006555", "name": "浦银安盛全球智能科技(QDII)A"},
    {"code": "501018", "name": "南方原油(QDII-FOF)A"},      # limit=6 会断在 `(` 上
    {"code": "161125"},                                      # 缺 name → 回落代码
]


@pytest.fixture
def family_view(monthly_report, monkeypatch):
    """用受控持仓驱动真实的 generate_family_view()（≥3 只重叠才进那个分支）。"""
    import services.fund_monitor as fund_monitor  # noqa: PLC0415
    import services.industry_templates as industry_templates  # noqa: PLC0415

    monkeypatch.setattr(
        fund_monitor, "load_fund_holdings", lambda uid: list(FAMILY_HOLDINGS), raising=True
    )
    monkeypatch.setattr(
        industry_templates, "get_fund_industry", lambda name: {"tag": "科技"}, raising=True
    )
    return monthly_report.generate_family_view()


def test_monthly_report_overlap_names_are_balanced(family_view):
    """家庭重叠基金那行：名字被截在括号里必须回退，缺名回落代码。"""
    warn = next(
        (w for w in family_view["warnings"] if "只基金两人都持有" in w), None
    )
    assert warn, f"没生成重叠持仓告警（overlap<3？）: {family_view['warnings']}"

    assert "南方原油" in warn, f"limit=6 的名字没渲染出来: {warn}"
    assert "南方原油(" not in warn, f"裸切片 `[:6]` 复发，留下半截 `(`: {warn}"
    assert "161125" in warn, f"缺名基金未回落代码: {warn}"

    issues = check_truncation(warn)
    assert not [i for i in issues if "括号不匹配" in i], f"质检仍报括号问题: {issues}\n{warn}"


def test_fault_injection_monthly_report_bare_slice_trips_quality_check(
    monthly_report, monkeypatch
):
    """把 monthly_report 的实现**退化成裸 `[:6]`**，必须复现 `南方原油(`。"""
    import services.fund_monitor as fund_monitor  # noqa: PLC0415
    import services.industry_templates as industry_templates  # noqa: PLC0415

    monkeypatch.setattr(monthly_report, "shorten_fund_name", _naive_slice_shorten, raising=True)
    monkeypatch.setattr(
        fund_monitor, "load_fund_holdings", lambda uid: list(FAMILY_HOLDINGS), raising=True
    )
    monkeypatch.setattr(
        industry_templates, "get_fund_industry", lambda name: {"tag": "科技"}, raising=True
    )
    result = monthly_report.generate_family_view()
    warn = next(w for w in result["warnings"] if "只基金两人都持有" in w)
    assert "南方原油(" in warn, f"退化实现没复现事故现场: {warn}"
    issues = check_truncation(warn)
    assert [i for i in issues if "括号不匹配" in i], (
        f"退化实现渲染出的内容质检却没报警：{issues}\n{warn}"
    )


# ============================================================
# 7. 源码结构防护栏（含源码级故障注入）
# ============================================================
def _function_nodes(tree: ast.AST, name: str) -> List[ast.FunctionDef]:
    """按名字收集模块顶层 + 嵌套的函数定义节点。"""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]


def _top_level_func_src(src: str, func_name: str) -> str:
    """取某个函数（含嵌套）的源码片段。"""
    tree = ast.parse(src)
    func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name),
        None,
    )
    assert func is not None, f"找不到函数 {func_name}，可能已被改名"
    return ast.get_source_segment(src, func) or ""


def _bare_name_slices(src: str) -> List[str]:
    """列出源码里所有"对 name 做裸切片"的表达式（AST 级，注释不算）。

    用 AST 而不是字符串匹配：注释里出现 `[:12]` 是合法的（说明文档），
    只有**真实代码**里对 name 的切片才算回归（另有 hexdigest()[:16]
    这类与基金名无关的合法切片，不能一并误伤）。
    """
    tree = ast.parse(src)
    return [
        f"第 {n.lineno} 行 {ast.unparse(n)}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Subscript)
        and isinstance(n.slice, ast.Slice)
        and "name" in ast.unparse(n).lower()
    ]


def test_helper_defined_exactly_once_in_whole_repo():
    """全仓（backend/**.py，排除 tests）只能有一份 `shorten_fund_name` 定义。

    多人共用工作区时容易各插一份同名函数，后定义的静默覆盖先定义的 ——
    两份实现语义不同时（例如一份只按数量配平、一份按栈配平）排查成本极高。
    2026-09-16 的教训正是"同一个 bug 修一处漏两处"，所以护栏扫全仓而不是
    只扫 night_worker.py。
    """
    defs: List[str] = []
    for path in BACKEND_DIR.rglob("*.py"):
        if "tests" in path.parts or "_archive" in path.parts or ".mypy_cache" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                "shorten_fund_name", "_shorten_fund_name"
            ):
                defs.append(f"{path.relative_to(BACKEND_DIR)}:{node.lineno}")
    assert len(defs) == 1, (
        f"全仓有 {len(defs)} 份 shorten_fund_name 定义（{defs}）—— "
        f"实现被复制了，修一处漏两处会重演"
    )
    assert defs[0].replace("\\", "/").startswith("services/"), (
        f"唯一实现应在 services/ 层（api 不能反向 import scripts），实际在 {defs[0]}"
    )


def test_thermometer_uses_helper_not_bare_slice():
    """`_build_portfolio_thermometer` 里不得再出现裸 `name[:12]`。"""
    src = NIGHT_WORKER_PATH.read_text(encoding="utf-8")
    seg = _top_level_func_src(src, "_build_portfolio_thermometer")
    assert "_shorten_fund_name" in seg, (
        "_build_portfolio_thermometer 未调用 _shorten_fund_name"
    )
    slices = _bare_name_slices(seg)
    assert not slices, (
        f"_build_portfolio_thermometer 里仍有裸切片（盲截基金名会留下半截括号）: {slices}"
    )


def test_shared_helpers_has_no_bare_name_slice():
    """api/shared_helpers.py 的 `_build_market_context` 里不得再出现裸 `name[:12]`。"""
    src = SHARED_HELPERS_PATH.read_text(encoding="utf-8")
    seg = _top_level_func_src(src, "_build_market_context")
    assert "shorten_fund_name" in seg, "选基 TOP3 未调用共享实现"
    slices = _bare_name_slices(seg)
    assert not slices, f"_build_market_context 里仍有裸切片: {slices}"


def test_monthly_report_has_no_bare_name_slice():
    """scripts/monthly_report.py 的 `generate_family_view` 里不得再出现裸 `name[:6]`。"""
    src = MONTHLY_REPORT_PATH.read_text(encoding="utf-8")
    seg = _top_level_func_src(src, "generate_family_view")
    assert "shorten_fund_name" in seg, "家庭重叠基金未调用共享实现"
    slices = _bare_name_slices(seg)
    assert not slices, f"generate_family_view 里仍有裸切片: {slices}"


@pytest.mark.parametrize(
    "path_fixture, func_name, fixed_expr, degraded_expr",
    [
        (
            "SHARED_HELPERS_PATH", "_build_market_context",
            'shorten_fund_name(f.get("name", ""), 12) or f.get("code", "")',
            "f.get('name','')[:12]",
        ),
        (
            "MONTHLY_REPORT_PATH", "generate_family_view",
            "shorten_fund_name(h.get(\"name\"), 6) or h.get(\"code\", \"\")",
            'h["name"][:6]',
        ),
    ],
)
def test_fault_injection_source_bare_slice_is_caught_by_guard(
    path_fixture, func_name, fixed_expr, degraded_expr
):
    """源码级故障注入：把两处新调用点**退回**裸切片，护栏必须报出来。

    这些护栏是字符串/AST 级的，最怕的是"替换后压根没匹配上、源码没变、断言
    恒绿"。这里先断言替换**确实生效**（degraded != src），再断言护栏变红。
    """
    src = globals()[path_fixture].read_text(encoding="utf-8")
    assert fixed_expr in src, f"{path_fixture} 里找不到已修写法，护栏已失效: {fixed_expr}"
    degraded = _top_level_func_src(
        src.replace(fixed_expr, degraded_expr), func_name
    )
    assert degraded_expr in degraded, "故障注入没生效（替换失败），本用例会变成恒绿"
    slices = _bare_name_slices(degraded)
    assert slices, (
        f"{func_name} 退化成裸切片后护栏却没报 —— 第 5/6 节的源码护栏是死的: {degraded}"
    )


def test_default_limit_not_widened_in_signature():
    """签名默认 limit 必须仍是 12 —— 防止后人"为了不截断"把宽度放宽。

    09-16 推送已 3759 字节（企微上限 4096），质检已在报「消息接近告警线」，
    放宽 limit 属于用一个新事故换掉旧事故。
    """
    tree = ast.parse(FUND_NAME_UTIL_PATH.read_text(encoding="utf-8"))
    func = _function_nodes(tree, "shorten_fund_name")
    assert len(func) == 1, "共享实现里找不到唯一的 shorten_fund_name 定义"
    defaults = [d for d in func[0].args.defaults]
    values = [
        d.value for d in defaults if isinstance(d, ast.Constant) and isinstance(d.value, int)
    ]
    assert values and values[-1] == 12, (
        f"shorten_fund_name 的默认 limit 不是 12（实际: {values}）—— "
        f"推送字节数逼近上限，不得放宽宽度"
    )
