"""架构分层契约守卫（import-linter）— 把 .importlinter 挂进日常 pytest 全量套件。
=====================================================================================

为什么要有这个测试文件
----------------------
仓库根目录的 `.importlinter` 之前**形同虚设**：`lint-imports` 输出
`Analyzed 265 files, 0 dependencies.`，4 条契约全部 KEPT。依赖图是空的，
所以无论代码怎么写都不会红 —— 一道永远绿的闸门等于没有闸门。

根因有两条，都已实测确认：

1. `root_packages = backend`，但 backend/ 下没有任何一处用 `backend.xxx`
   形式导入（全仓库 `import backend.*` 计数为 0）。真正的导入根是 backend/
   目录本身（`backend/main.py` 里 `sys.path.insert(0, os.path.dirname(__file__))`），
   所以跨层导入都写成 `from services.xxx import yyy`。grimp 以 `backend`
   为根给模块命名（backend.api.chat），再去图里找 `services.xxx` —— 找不到，
   全部判为 external 丢弃（include_external_packages 默认 False）。
   结果：265 个节点、0 条边。

2. `backend/scripts/` 不在任何一层里 —— 37 个运维/定时任务脚本从未被检查。

修复后：310 个模块、885 条依赖，7 条契约全部生效。

本文件守三件事
--------------
A. 所有契约必须 KEPT（真正的分层约束，含「infra 不得依赖 services」铁律）。
B. **依赖图不能为空** —— 这是针对根因 1 的元守卫。退回 0 条边时，所有契约
   又会变成恒绿，而红灯永远不会亮。没有这条，本文件自己也会变成假绿。
C. **`scripts` 必须在图里** —— 针对根因 2。scripts/ 一旦掉出 root_packages，
   这 37 个脚本就重新变成免检区。

依赖说明
--------
`import-linter` / `grimp` 在 `requirements-dev.txt` 里，**不在** `requirements.txt`，
所以 CI 的 `backend-test-suite` job（只装 requirements.txt）会 skip 本文件 ——
那条 job 不该红。CI 的 `lint-architecture` job 会装 requirements-dev.txt 并跑
`PYTHONPATH=backend lint-imports`，是同一套契约的另一处执行点。
本地/服务器跑全量 pytest 的环境装了 dev 依赖，本文件会真实执行。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# backend/ 必须在 sys.path 上，否则 grimp 找不到 root_packages 里的那些包
# （它们是以 backend/ 为导入根的顶层包，不是 backend.xxx 子包）。
# conftest.py 的 _ensure_backend_on_syspath() 已经做过，这里再补一次是为了
# 让本文件被单独点名运行（pytest backend/tests/test_architecture_contracts.py）
# 时不依赖别人的副作用。
_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_IMPORTLINTER_CONFIG = _REPO_ROOT / ".importlinter"

# 图构建一次要解析全量源码，三个用例共用同一份报告，避免重复付三次代价。
_REPORT_CACHE: dict[str, object] = {}


def _build_report():
    """构建并返回 (report, error_message)。error_message 非空表示契约根本没跑起来。"""
    importlinter = pytest.importorskip(
        "importlinter", reason="import-linter 未安装（属于 requirements-dev.txt）"
    )
    pytest.importorskip("grimp", reason="grimp 未安装（属于 requirements-dev.txt）")

    from importlinter import configuration
    from importlinter.application import use_cases
    from importlinter.contracts.forbidden import ForbiddenContract
    from importlinter.contracts.independence import IndependenceContract
    from importlinter.contracts.layers import LayersContract
    from importlinter.domain.contract import registry

    configuration.configure()

    # create_report() 不会自己注册内置契约类型（CLI 的 lint_imports() 内部才做），
    # 直接用 create_report 必须先注册，否则报 NoSuchContractType: forbidden。
    # 重复注册是幂等的（registry 就是个 dict 覆盖写）。
    registry.register(ForbiddenContract, "forbidden")
    registry.register(IndependenceContract, "independence")
    registry.register(LayersContract, "layers")

    if not _IMPORTLINTER_CONFIG.exists():
        return None, f"找不到配置文件 {_IMPORTLINTER_CONFIG}"

    user_options = use_cases.read_user_options(config_filename=str(_IMPORTLINTER_CONFIG))
    report = use_cases.create_report(
        user_options,
        cache_dir=None,  # 不落盘缓存：测试不得往仓库里写 .grimp_cache/.import_linter_cache
        verbose=False,
    )
    return report, None


def _get_report():
    if "report" not in _REPORT_CACHE:
        report, error = _build_report()
        _REPORT_CACHE["report"] = report
        _REPORT_CACHE["error"] = error
    return _REPORT_CACHE["report"], _REPORT_CACHE["error"]


def _format_broken(report) -> str:
    """把破防的契约渲染成人能看的错误信息（含具体模块与行号）。"""
    lines = []
    for contract, check in report.get_contracts_and_checks():
        if check.kept:
            continue
        lines.append(f"\nBROKEN: {contract.name}")
        for chain_data in check.metadata.get("invalid_chains", []):
            downstream = chain_data["downstream_module"]
            upstream = chain_data["upstream_module"]
            lines.append(f"  {downstream} 不得 import {upstream}:")
            for chain in chain_data["chains"]:
                hops = []
                for link in chain:
                    line_nos = ",".join(f"l.{n}" for n in link["line_numbers"])
                    hops.append(f"{link['importer']} -> {link['imported']} ({line_nos})")
                lines.append("    " + "\n      ".join(hops))
    if report.invalid_contract_options:
        for name, exc in report.invalid_contract_options.items():
            lines.append(f"\nINVALID OPTIONS: {name}: {exc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# A. 契约必须全部 KEPT
# ---------------------------------------------------------------------------


def test_all_import_linter_contracts_are_kept():
    """`.importlinter` 里的每一条分层契约都必须成立。

    这是真正的分层闸门。若 import-linter 没装，importorskip 会 skip 而不是
    假装通过 —— 空转的守卫比没有守卫更危险。
    """
    report, error = _get_report()
    assert error is None, error
    assert not report.could_not_run, (
        "import-linter 契约没能跑起来（配置或选项无效），"
        "这种情况必须修好而不是放任：\n" + _format_broken(report)
    )
    assert not report.contains_failures, (
        f"架构分层契约破损：{report.broken_count} broken / {report.kept_count} kept."
        + _format_broken(report)
    )
    # 至少要有契约被真正检查过，防止配置被整体删空后这里变成无断言的绿灯。
    assert report.kept_count >= 1, "一条契约都没被检查 —— 配置文件可能被清空了"


# ---------------------------------------------------------------------------
# B. 元守卫：依赖图不得为空
# ---------------------------------------------------------------------------


def test_dependency_graph_is_not_empty():
    """依赖图必须有真实的边，否则所有契约都会恒绿（历史上真实发生过）。

    2026-09-17 之前 `root_packages = backend` 与运行时 `sys.path` 根不一致，
    grimp 把全部跨层导入当成 external 丢掉，输出 `265 files, 0 dependencies`
    且 4 条契约全绿。这条断言就是让「闸门空转」这件事本身变成红灯。
    """
    report, error = _get_report()
    assert error is None, error
    assert report.module_count > 0, "依赖图一个模块都没有，import-linter 没真正扫描到代码"
    assert report.import_count > 0, (
        f"依赖图有 {report.module_count} 个模块却 0 条依赖边 —— 这正是本仓库踩过的坑："
        "root_packages 与运行时 sys.path 根不一致时，grimp 会把所有跨层导入判为 "
        "external 并丢弃，于是每条契约都恒绿。请核对 .importlinter 的 root_packages "
        "是否仍是 backend/ 下的顶层包（api/services/infra/...），而不是 backend。"
    )
    # 885 条是当前实测值。留足余量地要求一个数量级，防止图被悄悄削掉大半。
    assert report.import_count >= 500, (
        f"依赖边数从 885 掉到 {report.import_count}，可能有整层代码掉出了扫描范围"
    )


# ---------------------------------------------------------------------------
# C. 元守卫：backend/scripts/ 必须在图里
# ---------------------------------------------------------------------------


def test_scripts_package_is_covered_by_the_graph():
    """backend/scripts/ 必须被纳入架构检查，不能是免检区。

    旧配置里 scripts/ 不在任何一层，37 个定时任务脚本（cache_warmer、
    night_worker、monthly_report ...）完全不受约束。
    """
    report, error = _get_report()
    assert error is None, error
    script_modules = [
        m for m in report.graph.modules if m == "scripts" or m.startswith("scripts.")
    ]
    assert script_modules, (
        "backend/scripts/ 不在依赖图里 —— 它又变回免检区了。"
        "请确认 .importlinter 的 root_packages 仍包含 scripts"
    )
    assert len(script_modules) >= 30, (
        f"图里只有 {len(script_modules)} 个 scripts 模块，少于预期的 ~48 个，"
        "扫描范围可能被意外收窄"
    )
    # scripts 还必须真的有出向依赖，否则"纳入了但没连上"同样看不出问题
    outgoing = sum(
        len(report.graph.find_modules_directly_imported_by(m)) for m in script_modules
    )
    assert outgoing > 0, "scripts 模块在图上没有任何出向边，等于没被真正分析"
