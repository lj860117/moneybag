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

设计原则（与本仓其它回归测试一致）：
1. **不复制实现**。所有用例都调用 night_worker 里真实的 `_shorten_fund_name`
   （按文件路径 importlib 加载），实现一改测试立刻能感知。
2. **质检口径复用真实实现**。括号是否"算失衡"直接用 `check_truncation()`，
   而不是在测试里另写一份计数规则。
3. **带故障注入**。用 monkeypatch 把辅助函数换成"裸 `[:12]` 不配平"的退化
   版本，断言同一套不变式**必须变红** —— 否则说明断言是恒绿的死测试。

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
@pytest.fixture(scope="module")
def night_worker():
    """以文件路径方式加载 scripts/night_worker.py。

    它是 scripts/ 下的**脚本**而不是包内模块，用普通 import 需要把 scripts/
    塞进 sys.path（会污染整场 pytest 的模块解析）。改用 importlib 按路径加载：
    脚本内部自带的 sys.path 引导会保证 `import config` 正常。
    """
    spec = importlib.util.spec_from_file_location(
        "_mb_night_worker_shorten_sut", NIGHT_WORKER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_mb_night_worker_shorten_sut"] = module
    spec.loader.exec_module(module)
    return module


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
# 4. 源码结构防护栏
# ============================================================
def _function_nodes(tree: ast.AST, name: str) -> List[ast.FunctionDef]:
    """按名字收集模块顶层 + 嵌套的函数定义节点。"""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]


def test_helper_defined_exactly_once():
    """`_shorten_fund_name` 只能有一份定义。

    多人共用工作区时容易各插一份同名函数，后定义的静默覆盖先定义的 ——
    两份实现语义不同时（例如一份只按数量配平、一份按栈配平）排查成本极高。
    """
    tree = ast.parse(NIGHT_WORKER_PATH.read_text(encoding="utf-8"))
    defs = _function_nodes(tree, "_shorten_fund_name")
    assert len(defs) == 1, (
        f"night_worker.py 里有 {len(defs)} 份 _shorten_fund_name 定义，"
        f"存在静默覆盖风险（行号: {[d.lineno for d in defs]}）"
    )


def test_thermometer_uses_helper_not_bare_slice():
    """`_build_portfolio_thermometer` 里不得再出现裸 `name[:12]`。"""
    src = NIGHT_WORKER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_build_portfolio_thermometer"),
        None,
    )
    assert func is not None, "找不到 _build_portfolio_thermometer，函数可能已被改名"
    seg = ast.get_source_segment(src, func) or ""
    assert "_shorten_fund_name" in seg, (
        "_build_portfolio_thermometer 未调用 _shorten_fund_name"
    )
    # 用 AST 而不是字符串匹配：注释里出现 `[:12]` 是合法的（说明文档），
    # 只有**真实代码**里对 name 的切片才算回归（函数内另有 hexdigest()[:16]
    # 这类与基金名无关的合法切片，不能一并误伤）。
    slices = [
        f"第 {n.lineno} 行 {ast.unparse(n)}"
        for n in ast.walk(func)
        if isinstance(n, ast.Subscript)
        and isinstance(n.slice, ast.Slice)
        and "name" in ast.unparse(n).lower()
    ]
    assert not slices, (
        f"_build_portfolio_thermometer 里仍有裸切片（盲截基金名会留下半截括号）: {slices}"
    )


def test_default_limit_not_widened_in_signature():
    """签名默认 limit 必须仍是 12 —— 防止后人"为了不截断"把宽度放宽。

    09-16 推送已 3759 字节（企微上限 4096），质检已在报「消息接近告警线」，
    放宽 limit 属于用一个新事故换掉旧事故。
    """
    tree = ast.parse(NIGHT_WORKER_PATH.read_text(encoding="utf-8"))
    func = _function_nodes(tree, "_shorten_fund_name")
    assert len(func) == 1
    defaults = [d for d in func[0].args.defaults]
    values = [
        d.value for d in defaults if isinstance(d, ast.Constant) and isinstance(d.value, int)
    ]
    assert values and values[-1] == 12, (
        f"_shorten_fund_name 的默认 limit 不是 12（实际: {values}）—— "
        f"推送字节数逼近上限，不得放宽宽度"
    )
