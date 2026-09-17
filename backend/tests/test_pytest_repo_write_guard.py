"""
conftest.py「仓库写入守卫」守护测试（FIX 2026-09-17）
============================================================================

要解决的问题
------------
「跑测试会写脏真实 data 目录」这件事，本仓此前**只有环境变量隔离**这一层
（conftest 顶层把 DATA_DIR 指到会话临时目录）。2026-09-17 用文件系统调用
插桩跑了一轮全量（2417 条），结果是：

  * 真实 data 目录（config.DEFAULT_DATA_DIR）：**0 次写入** —— 隔离是有效的；
  * 但仓库里另外 5 个目标被真实写到了（全部绕过 DATA_DIR）：

      backend/logs/                            mkdir x3
      backend/logs/llm_balance_monitor.log     open(a) x1（已累积 645KB）
      backend/infra/.cache/                    mkdir x1
      backend/infra/.cache/industry_board_cache.json  open(w) x1
      backend/tests/data/monitor/              mkdir x1

    也就是说：**「测试没写 data/」成立，但「测试没写仓库」不成立**。只靠
    DATA_DIR 隔离，永远发现不了第二行以后的东西。

所以 conftest 里加了一层**量结局**的守卫：会话开始前给受保护目录树拍快照，
会话结束时再拍一次，出现新增/改写/删除就让整个会话失败。

这个文件测什么
--------------
守卫自己必须是**可证伪**的——本仓 test_scorecard_caliber_honesty.py 的教训是
「只有行为级断言才挡得住"逻辑被改坏但字符串还在"」，这里同样适用：

  1. :func:`test_guard_detects_write_into_protected_tree`
     真起一个子进程 pytest，用插件往受保护目录里写一个文件，
     断言**子进程退出码非 0 且输出里出现 MONEYBAG_WRITE_GUARD**。
     → 把守卫摘掉，这条必红（不是"看起来绿"）。

  2. :func:`test_guard_survives_when_last_test_is_xfail`
     最后一个用例是 xfail 时守卫仍须生效 —— pytest 会把 session 级
     fixture 的 teardown 错误挂在最后一个 item 名下，而 non-strict xfail
     会把它一起吞成 "xfailed"、退出码还是 0。这条逼着守卫的权威判定
     必须放在 pytest_sessionfinish（改 session.exitstatus）。

  3. :func:`test_guard_stays_green_when_write_is_outside_protected_tree`
     对照组：同样的子进程，但写的位置在受保护目录**之外**，
     断言退出码 0。没有这条，一个"永远红"的守卫也能骗过第 1 条。

  4. :func:`test_unnormalized_dotdot_is_the_monitor_dir_root_cause`
     把 2026-09-17 定位到的真根因钉成可执行断言：sys.path 里出现未归一化的
     `".."` 时，`Path(__file__).parent.parent.parent` 会算错目录
     （pathlib 不折叠 ".."），于是 stock_monitor_cron 的 MONITOR_DIR 落进
     `backend/tests/data/monitor`。这条从源码层禁止该形态再出现。

  5. 环境变量隔离（LOG_DIR / MONITOR_DIR）与默认受保护清单的断言。

为什么不直接断言"真实 data 目录没被写"
--------------------------------------
断言一旦失败，污染**已经发生**了 —— 测试自己就成了破坏生产数据的元凶。
这里一律用 tmp_path 下的**诱饵目录**验证同一条性质（与
test_conftest_data_dir_isolation.py 的做法一致），零风险且完全等价。

运行方式::

    cd backend && env -u PYTHONPATH \\
        /Users/leijiang/.workbuddy/binaries/python/envs/default/bin/python \\
        -m pytest tests/test_pytest_repo_write_guard.py -v
"""
import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _TESTS_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent

_GUARD_TREES_ENV = "MONEYBAG_PYTEST_GUARD_TREES"
_GUARD_ENABLED_ENV = "MONEYBAG_PYTEST_WRITE_GUARD"
_PROBE_TARGET_ENV = "MONEYBAG_GUARD_PROBE_TARGET"
_PROBE_XFAIL_ENV = "MONEYBAG_GUARD_PROBE_XFAIL_LAST"

# 子进程里跑的最小用例：选它只因为它快且必过
_PROBE_TEST = "tests/test_conftest_data_dir_isolation.py" \
              "::test_config_data_dir_is_a_temp_dir_not_production"

# 注入用的探针插件（写在 tmp_path 里，不在受保护目录树内，避免自我污染）
_PROBE_PLUGIN = '''
"""把"往某个目录写文件"注入到子进程 pytest 会话里。"""
import os
import pathlib

import pytest


def pytest_configure(config):
    target = pathlib.Path(os.environ["{env_var}"])
    (target / "polluted_by_probe.txt").write_text("x", encoding="utf-8")


def pytest_collection_modifyitems(session, config, items):
    """把最后一个用例标成 xfail（{xfail_env}=1 时）。

    用途：复现「session 级 fixture 的 teardown 报错被 pytest 吞掉」这条
    真实盲区 —— pytest 会把 session fixture 的 teardown 错误挂在**最后一个
    item** 名下，而 non-strict xfail 连它的 teardown 一起吞，于是守卫静音、
    退出码还是 0。
    """
    if os.environ.get("{xfail_env}") == "1" and items:
        items[-1].add_marker(
            pytest.mark.xfail(reason="probe: 最后一个用例是 xfail"))
'''


def _run_child_pytest(
    tmp_path: Path,
    probe_target: Path,
    guard_trees,
    extra_env: dict | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess:
    """起一个全新子进程跑 pytest，返回 CompletedProcess。

    用子进程而不是进程内调用：守卫的基线快照在 conftest **模块顶层**拍，
    同一进程内重复跑测不到"冷启动"这条真实路径。

    Args:
        tmp_path: 放探针插件的临时目录（会挂到子进程 PYTHONPATH 上）。
        probe_target: 探针要写文件的目录。
        guard_trees: 传给子进程 MONEYBAG_PYTEST_GUARD_TREES 的清单
            （`os.pathsep` 分隔的字符串）。
        extra_env: 额外环境变量。
        timeout: 子进程超时秒数。

    Returns:
        subprocess.CompletedProcess。
    """
    (tmp_path / "mbguard_probe_plugin.py").write_text(
        _PROBE_PLUGIN.format(env_var=_PROBE_TARGET_ENV,
                             xfail_env=_PROBE_XFAIL_ENV),
        encoding="utf-8")

    env = os.environ.copy()
    # 清掉一切会影响子进程目录解析的继承值，保证测的是默认行为
    env.pop("DATA_DIR", None)
    env.pop("MONEYBAG_PYTEST_DATA_DIR", None)
    env[_GUARD_TREES_ENV] = guard_trees
    env[_PROBE_TARGET_ENV] = str(probe_target)
    env["PYTHONPATH"] = str(tmp_path)
    env.pop(_GUARD_ENABLED_ENV, None)
    env.update(extra_env or {})

    return subprocess.run(
        [sys.executable, "-m", "pytest", _PROBE_TEST,
         "-q", "-p", "mbguard_probe_plugin", "-p", "no:cacheprovider"],
        cwd=str(_BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _assert_child_really_ran(proc: subprocess.CompletedProcess) -> None:
    """防止"空过"：子进程必须真的执行了用例，否则下面的断言毫无意义。"""
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "1 passed" in combined or "passed" in combined, (
        f"子进程没有真正跑测试，守卫断言会空过。\n"
        f"  退出码: {proc.returncode}\n"
        f"  stdout 尾部:\n{proc.stdout[-1500:]}\n"
        f"  stderr 尾部:\n{proc.stderr[-800:]}")


def test_guard_detects_write_into_protected_tree(tmp_path):
    """往受保护目录里写东西 → 会话必须失败，且失败原因可追溯。

    故障注入方向：把 conftest 里的 `_repo_write_guard` 整个删掉（或把
    teardown 的 raise 换成 return），本用例必须转红。恒绿的守卫等于空转的绿。
    """
    decoy = tmp_path / "decoy_protected"
    decoy.mkdir()
    (decoy / "sentinel.txt").write_text("keep me", encoding="utf-8")

    proc = _run_child_pytest(tmp_path, probe_target=decoy, guard_trees=str(decoy))

    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0, (
        f"往受保护目录写了文件，守卫却没让会话失败（守卫失效/空转）。\n"
        f"  stdout 尾部:\n{proc.stdout[-2000:]}\n"
        f"  stderr 尾部:\n{proc.stderr[-1000:]}")
    assert "MONEYBAG_WRITE_GUARD" in combined, (
        f"会话确实失败了，但不是守卫报的 —— 可能是用例本身红了，"
        f"那样这条断言就是假的。\n  stdout 尾部:\n{proc.stdout[-2000:]}")


def test_guard_survives_when_last_test_is_xfail(tmp_path):
    """最后一个用例是 xfail 时，守卫**仍然**必须让会话失败。

    这是 2026-09-17 实测出来的真盲区，不是假想威胁（pytest 9.1.1）：

      * pytest 把 session 级 autouse fixture 的 teardown 错误挂在
        **最后一个 item** 名下（最小复现里显示成 `ERROR at teardown of
        test_b`）；
      * 那个 item 若带 non-strict `xfail`，pytest 会把 teardown 错误一起
        算成 xfailed —— 于是输出变成 `1 passed, 2 xfailed`、**退出码 0**，
        守卫的 AssertionError 被彻底吞掉，连个水花都没有。

    本仓 `backend/tests/` 里 xfail 用例不少（全量基线里有 1 条 xfailed），
    真出事时"最后一个用例恰好是 xfail"完全可能命中。所以守卫的**权威判定**
    放在 conftest 的 `pytest_sessionfinish`（改 session.exitstatus，不受
    用例标记影响），本用例就是这条兜底的回归测试。

    故障注入方向：把 conftest `pytest_sessionfinish` 里那段守卫代码删掉，
    只留 fixture 的 raise，本用例转红。
    """
    decoy = tmp_path / "decoy_protected"
    decoy.mkdir()
    (decoy / "sentinel.txt").write_text("keep me", encoding="utf-8")

    proc = _run_child_pytest(
        tmp_path,
        probe_target=decoy,
        guard_trees=str(decoy),
        extra_env={_PROBE_XFAIL_ENV: "1"},
    )

    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0, (
        f"最后一个用例是 xfail 时守卫被吞了（teardown 错误被算成 xfailed）。\n"
        f"  stdout 尾部:\n{proc.stdout[-2000:]}\n"
        f"  stderr 尾部:\n{proc.stderr[-1000:]}")
    assert "MONEYBAG_WRITE_GUARD" in combined, (
        f"会话失败了但不是守卫报的 —— 这条断言会变成假的。\n"
        f"  stdout 尾部:\n{proc.stdout[-2000:]}\n"
        f"  stderr 尾部:\n{proc.stderr[-1000:]}")


def test_guard_stays_green_when_write_is_outside_protected_tree(tmp_path):
    """对照组：写的位置在受保护目录之外 → 会话必须正常通过。

    没有这条，一个"无论如何都红"的守卫同样能通过上面那条断言。
    """
    decoy = tmp_path / "decoy_protected"
    decoy.mkdir()
    (decoy / "sentinel.txt").write_text("keep me", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()

    proc = _run_child_pytest(tmp_path, probe_target=outside, guard_trees=str(decoy))

    _assert_child_really_ran(proc)
    assert proc.returncode == 0, (
        f"写的位置在受保护目录之外，守卫却报警（误报会让大家第一反应是关掉它）。\n"
        f"  stdout 尾部:\n{proc.stdout[-2000:]}\n"
        f"  stderr 尾部:\n{proc.stderr[-1000:]}")


def test_guard_diff_reports_added_changed_removed(tmp_path):
    """守卫的 diff 逻辑单元级自测：新增 / 改写 / 删除三种变化都要被识别。

    纯函数级，不依赖任何 fixture 顺序。
    """
    from conftest import guard_diff, guard_snapshot  # noqa: PLC0415

    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "keep.txt").write_text("same", encoding="utf-8")
    (root / "will_change.txt").write_text("v1", encoding="utf-8")
    (root / "will_delete.txt").write_text("bye", encoding="utf-8")

    before = guard_snapshot([root])
    assert before, "快照为空，用例前提失效"

    (root / "added.txt").write_text("new", encoding="utf-8")
    (root / "will_change.txt").write_text("v2", encoding="utf-8")
    (root / "will_delete.txt").unlink()

    added, changed, removed = guard_diff(before, guard_snapshot([root]))

    assert any("added.txt" in p for p in added), f"新增未被识别: {added}"
    assert any("will_change.txt" in p for p in changed), f"改写未被识别: {changed}"
    assert any("will_delete.txt" in p for p in removed), f"删除未被识别: {removed}"
    assert not any("keep.txt" in p for p in added + changed + removed), (
        "未变动的文件被误报了")


def test_guard_snapshot_ignores_interpreter_caches(tmp_path):
    """__pycache__ / .pyc 必须被排除，否则守卫每次都"变了"，立刻退化成噪音。"""
    from conftest import guard_snapshot  # noqa: PLC0415

    root = tmp_path / "tree"
    (root / "__pycache__").mkdir(parents=True)
    (root / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"\x00\x01")
    (root / "real.txt").write_text("hi", encoding="utf-8")

    snap = guard_snapshot([root])
    entries = snap[str(root.resolve())] if str(root.resolve()) in snap else list(snap.values())[0]

    assert "real.txt" in entries, f"真实文件没进快照: {entries}"
    assert not any("__pycache__" in k for k in entries), (
        f"__pycache__ 没被排除，守卫会恒红: {entries}")
    assert not any(k.endswith(".pyc") for k in entries), f".pyc 没被排除: {entries}"


def test_default_guard_trees_cover_both_real_data_dirs():
    """默认受保护清单必须覆盖两棵真实数据目录（权威 + 历史遗留）。

    `_GUARD_REPO_ROOT / "data"` 是 config.DEFAULT_DATA_DIR；
    `_GUARD_BACKEND_DIR / "data"` 是 ops_summary.py:64 里命名为
    _LEGACY_DATA_DIR 的历史目录，服务器上就是 /opt/moneybag/backend/data
    —— 那棵"8KB、0 个文件的空目录树"。
    """
    from conftest import _DEFAULT_GUARD_TREES  # noqa: PLC0415

    resolved = {str(Path(p).resolve()) for p in _DEFAULT_GUARD_TREES}
    assert str((_REPO_ROOT / "data").resolve()) in resolved, (
        f"权威数据目录不在受保护清单里: {resolved}")
    assert str((_BACKEND_DIR / "data").resolve()) in resolved, (
        f"历史遗留数据目录不在受保护清单里: {resolved}")


def test_log_and_monitor_dir_env_are_isolated_to_temp():
    """LOG_DIR / MONITOR_DIR 必须落在临时目录里。

    这两个变量是 2026-09-17 实测里"绕过 DATA_DIR 直写仓库"的两条通道：
      scripts/llm_balance_monitor.py:76  LOG_DIR     默认 backend/logs
      scripts/stock_monitor_cron.py:46   MONITOR_DIR 默认 <repo>/data/monitor
    故障注入方向：把 conftest 里设置这两个变量的两行删掉，本用例转红。
    """
    tmp_root = Path(tempfile.gettempdir()).resolve()

    for name in ("LOG_DIR", "MONITOR_DIR"):
        value = os.environ.get(name, "")
        assert value, f"{name} 未被 conftest 隔离（应指向会话临时目录）"
        resolved = Path(value).resolve()
        assert resolved == tmp_root or tmp_root in resolved.parents, (
            f"{name}={value} 不在系统临时目录 {tmp_root} 下，"
            f"测试会写进真实仓库目录")


def test_known_unfixed_repo_write_channel_is_documented():
    """钉住那条**已知但测试侧治不了**的仓库写入通道。

    `infra/data_source/alt/flows.py` 把 akshare 的行业板块缓存硬编码在
    `Path(__file__).parent.parent.parent / ".cache"`，也就是
    `backend/infra/.cache/industry_board_cache.json`：不走 config.DATA_DIR、
    没有可覆盖的环境变量，测试侧无法隔离。实测全量跑一轮必被"改写"一次。

    所以它现在**不在** `_DEFAULT_GUARD_TREES` 里（加了就恒红，而修法在
    生产侧）。本用例的作用是个绊线：一旦有人把 flows.py 改成可配置，这条
    就会红，逼着他回来把 `backend/infra/.cache` 加进受保护清单 —— 否则
    那条通道会永远处于"没人管"的状态。

    如果你正看着这条失败：说明 flows.py 已修好，请把上面 conftest 里
    `_DEFAULT_GUARD_TREES` 那行注释掉的路径加回去，然后删掉本用例。
    """
    flows_path = _BACKEND_DIR / "infra" / "data_source" / "alt" / "flows.py"
    assert flows_path.exists(), f"找不到 {flows_path}，用例前提失效"

    src = flows_path.read_text(encoding="utf-8")
    hardcoded = 'Path(__file__).parent.parent.parent / ".cache"' in src

    assert hardcoded, (
        "flows.py 里的硬编码缓存目录已经不在了 —— 说明这条通道已被修好。\n"
        "现在该把 `backend/infra/.cache` 加进 conftest 的 "
        "_DEFAULT_GUARD_TREES，然后删掉本用例。\n"
        f"  文件: {flows_path}")


def test_unnormalized_dotdot_is_the_monitor_dir_root_cause():
    """sys.path 里未归一化的 ".." 会让 Path(__file__).parent.* 算错目录。

    2026-09-17 实测定位到的真事故链：
      test_alert_push_test_mode_guard.py:39
        `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))`
      → sys.path 里出现 `backend/tests/..`（**未归一化**）
      → `from scripts import stock_monitor_cron` 的 __file__ 变成
        `backend/tests/../scripts/stock_monitor_cron.py`
      → pathlib **不折叠** ".."，于是 .parent.parent.parent 算出 `backend/tests`
      → MONITOR_DIR = backend/tests/data/monitor 被真实 mkdir 出来

    修法是给这些 sys.path 引导加 os.path.normpath。本用例从源码层禁止该形态
    再溜回来（新写的测试文件很容易照抄老写法）。
    """
    # 走 AST 而不是正则：本文件自己的 docstring 里就复述了那段事故代码，
    # 正则会把说明文字当成真代码报出来（假红）。AST 只看真实调用节点。
    offenders: list = []

    for py_file in sorted(_TESTS_DIR.glob("*.py")):
        try:
            src = py_file.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(py_file))
        except (OSError, SyntaxError) as exc:  # noqa: BLE001
            pytest.fail(f"无法解析 {py_file.name}：{exc}")
            return
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr in ("insert", "append")):
                continue
            owner = func.value
            if not (isinstance(owner, ast.Attribute) and owner.attr == "path"
                    and isinstance(owner.value, ast.Name)
                    and owner.value.id == "sys"):
                continue
            stmt = ast.get_source_segment(src, node) or ""
            if '".."' not in stmt and "'..'" not in stmt:
                continue
            if any(k in stmt for k in ("normpath", "abspath", "resolve")):
                continue
            offenders.append(f"{py_file.name}:{node.lineno}: {stmt}")

    assert not offenders, (
        "测试文件里出现了**未归一化**的 sys.path 引导（含 '..'）。\n"
        "后果不是理论风险：它会让 `Path(__file__).parent.parent.parent` 算错，\n"
        "把 stock_monitor_cron 的 MONITOR_DIR 建到 backend/tests/data/monitor。\n"
        "修法：包一层 os.path.normpath(...) 或 os.path.abspath(...)。\n  "
        + "\n  ".join(offenders))
