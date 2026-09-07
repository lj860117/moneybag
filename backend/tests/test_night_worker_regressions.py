"""
night_worker / portfolio 线上告警根因回归测试
=============================================

覆盖 2026-09-07~09-08 线上四起告警，防止改名/漏导入/正则退化复发：

1. EXAG_PAT 百分比假阳性（9-8 凌晨 10 条「异常涨幅数字「320%」」）
2. ALLOC_PCTS 改名漏改引用方（9-7 凌晨 15 条 NameError，预热缓存 0/15）
3. `_P` 函数内漏导入（9-7 04:00 基金推荐失败）
4. sys.path 引导晚于 `import config`（cache_warmer --harvest 收尾路径
   ModuleNotFoundError）

设计原则：**绝不把实现里的正则/常量复制到本文件**。
EXAG 用例直接调用真实的 `_inject_hallucination_label`，ALLOC_PCTS 与 `_P`
则对源文件做 AST 审计。这样实现一改、测试立刻能感知，不会变成"改了实现
还绿"的死测试。

注意：本文件不需要数据隔离 fixture —— backend/tests/conftest.py 已在模块
顶层把 DATA_DIR 强制指向 pytest 会话专属临时目录，导入 night_worker 时产生
的 NIGHT_LOG_DIR 也会落在那里，不会写生产目录。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import List, Set

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
NIGHT_WORKER_PATH = BACKEND_DIR / "scripts" / "night_worker.py"
PORTFOLIO_PATH = BACKEND_DIR / "services" / "portfolio.py"

# 应当**放行**（不得判为异常涨幅）的合规表述
EXAG_PASS_CASES: List[str] = [
    "涨幅0.320%",      # 小数点后三位，不得被截成 320%
    "换手率1.406%",     # 小数点后三位，不得被截成 406%
    "近1年320%",       # 有明确时间限定
    "近三年250%",      # 有明确时间限定
    "近10年800%",      # 多位数年份限定（旧正则会误报）
    "成立以来520%",     # 长周期口径（旧正则会误报）
    "累计收益350%",     # 累计口径（旧正则会误报）
]

# 应当**命中**（孤立的大额百分比，大概率幻觉）
EXAG_HIT_CASES: List[str] = [
    "单日暴涨500%",
    "收益达1200%",
    "该股暴涨350%",
]


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
        "_mb_night_worker_sut", NIGHT_WORKER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_mb_night_worker_sut"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exag_results(night_worker) -> dict:
    """一次性对所有用例跑真实实现，返回 {用例文本: 是否被判为异常涨幅}。

    合并成一次调用的原因：`_inject_hallucination_label` 内部会做一次
    urllib 探测（timeout=3），逐用例调用会放大成 N 倍耗时。
    """
    cases = {text: text for text in EXAG_PASS_CASES + EXAG_HIT_CASES}
    labelled = night_worker._inject_hallucination_label(cases)
    return {
        text: "异常涨幅数字" in labelled[text] for text in cases
    }


# ============================================================
# 1. EXAG_PAT 假阳性回归
# ============================================================
@pytest.mark.parametrize("text", EXAG_PASS_CASES)
def test_exag_pat_should_not_flag(text, exag_results):
    """合规百分比（含小数点后三位、长周期/累计限定语）不得被判为异常涨幅。"""
    assert exag_results[text] is False, (
        f"{text!r} 被误判为异常涨幅 —— EXAG_PAT 退回旧行为会刷屏告警"
    )


@pytest.mark.parametrize("text", EXAG_HIT_CASES)
def test_exag_pat_should_flag(text, exag_results):
    """孤立的大额百分比仍须被检出，不能为了消假阳性把真阳性也一起干掉。"""
    assert exag_results[text] is True, (
        f"{text!r} 未被检出 —— EXAG_PAT 过度放宽，幻觉数字会漏检"
    )


def test_exag_pat_still_present_in_implementation():
    """防护栏：EXAG_PAT 必须还在实现里，防止有人"为了消警"整段删掉检测。"""
    tree = ast.parse(NIGHT_WORKER_PATH.read_text(encoding="utf-8"))
    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "EXAG_PAT" in assigned, "EXAG_PAT 已从 night_worker.py 中消失"


# ============================================================
# 2. ALLOC_PCTS 改名事故回归
# ============================================================
def test_portfolio_has_no_bare_alloc_pcts():
    """portfolio.py 里不得再出现裸名 ALLOC_PCTS。

    事故：config.py 里叫 RISK_ALLOC_PCTS，portfolio.py 六处仍用旧名，
    导致 5 档风险 × 3 类资产 = 15 次 NameError，预热缓存 0/15 全失败。
    """
    tree = ast.parse(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    bare = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "ALLOC_PCTS"
    ]
    assert not bare, (
        f"portfolio.py 仍有 {len(bare)} 处裸名 ALLOC_PCTS，"
        f"运行时会抛 NameError: name 'ALLOC_PCTS' is not defined"
    )


def test_portfolio_uses_config_risk_alloc_pcts():
    """反向断言：确实引用了 config.RISK_ALLOC_PCTS，避免上一条测试因代码被
    整体删除而"空过"。"""
    tree = ast.parse(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    uses = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "RISK_ALLOC_PCTS"
    ]
    assert uses, "portfolio.py 找不到对 config.RISK_ALLOC_PCTS 的引用"


def test_risk_alloc_pcts_has_five_profiles():
    """5 档风险配置齐全，且每档 6 个权重（与 RECOMMENDED_FUNDS 对齐）。"""
    import config

    assert set(config.RISK_ALLOC_PCTS) == {
        "保守型", "稳健型", "平衡型", "进取型", "激进型"
    }
    for profile, pcts in config.RISK_ALLOC_PCTS.items():
        assert len(pcts) == 6, f"{profile} 的权重数量不是 6: {pcts}"


# ============================================================
# 3. `_P` 漏导入审计
# ============================================================
def _bound_names(node: ast.AST) -> Set[str]:
    """收集某个作用域内被绑定的名字（import / 赋值 / 参数）。"""
    names: Set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            for alias in child.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(child, ast.ImportFrom):
            for alias in child.names:
                names.add(alias.asname or alias.name)
        elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.add(child.id)
        elif isinstance(child, ast.arg):
            names.add(child.arg)
    return names


def test_every_function_using_P_binds_it():
    """任何用到 `_P` 的函数都必须自己绑定（局部 import 或作为参数）。

    事故：`_P` 只在 _build_portfolio_thermometer 内部 `from pathlib import
    Path as _P`，_get_fund_recommendations 直接拿来用 → 04:00 基金推荐失败。
    """
    tree = ast.parse(NIGHT_WORKER_PATH.read_text(encoding="utf-8"))
    functions = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ]

    offenders: List[str] = []
    users: List[str] = []
    for func in functions:
        uses_p = any(
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == "_P"
            for node in ast.walk(func)
        )
        if not uses_p:
            continue
        users.append(func.name)
        if "_P" not in _bound_names(func):
            offenders.append(func.name)

    # 防止审计逻辑本身失效后变成"空断言永远通过"
    assert users, "没扫到任何使用 _P 的函数，审计逻辑可能已失效"
    assert not offenders, (
        f"以下函数使用了 _P 但未绑定，运行时会抛 NameError: {offenders}"
    )


# ============================================================
# 4. sys.path 引导顺序
# ============================================================
def test_sys_path_bootstrap_precedes_import_config():
    """sys.path 补全必须早于第一个 `import config`。

    事故：以 `python3 backend/scripts/night_worker.py` 调用时 sys.path[0] 是
    scripts/ 而非 backend/，`import config` 抛 ModuleNotFoundError
    （cache_warmer --harvest 收尾路径即此场景，01:00 直接调用那条路径反而正常）。
    """
    tree = ast.parse(NIGHT_WORKER_PATH.read_text(encoding="utf-8"))

    bootstrap_line = None
    import_config_line = None
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "config" and import_config_line is None:
                    import_config_line = node.lineno
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_BACKEND_DIR":
                    bootstrap_line = node.lineno

    assert bootstrap_line is not None, "找不到 _BACKEND_DIR 引导代码"
    assert import_config_line is not None, "找不到顶层 `import config`"
    assert bootstrap_line < import_config_line, (
        f"sys.path 引导(第 {bootstrap_line} 行)必须早于 "
        f"`import config`(第 {import_config_line} 行)，"
        f"否则脚本直调会抛 ModuleNotFoundError"
    )


def test_no_unguarded_sys_path_insert_inside_functions():
    """函数体内不得再有 `sys.path.insert` —— 只允许模块顶层那处带守卫的引导。

    事故（2026-09-08 收尾发现）：`_call_v3()` / `_filter_prompt_leak()` /
    `step_r1_phase1()` 三处函数内各有一句无守卫的 `sys.path.insert`：

      - `_call_v3` 每次 LLM 调用都走一次，一个晚上几十次调用就往 sys.path
        里塞几十个重复条目（`scripts/..` 未规范化，与顶层 `_BACKEND_DIR`
        的 abspath 字符串不同值，守卫拦不住）；
      - 另两处值与 `_BACKEND_DIR` 相同但同样无守卫，照样重复累积。

    sys.path 越长，后续每次 import 的目录探测就越慢，且同一模块可能被
    解析成两份。路径由模块顶部 bootstrap（导入时无条件执行，无论本文件是
    `__main__` 还是被 import）保证，函数内无需再插。
    """
    tree = ast.parse(NIGHT_WORKER_PATH.read_text(encoding="utf-8"))

    offenders: List[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "insert"
                and isinstance(child.func.value, ast.Attribute)
                and child.func.value.attr == "path"
                and isinstance(child.func.value.value, ast.Name)
                and child.func.value.value.id == "sys"
            ):
                offenders.append(f"{node.name}() 第 {child.lineno} 行")

    assert not offenders, (
        f"以下函数内仍有 sys.path.insert，会重复污染 sys.path：{offenders}"
    )
