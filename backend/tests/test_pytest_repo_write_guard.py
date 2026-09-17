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
import datetime
import os
import subprocess
import sys
import tempfile
import time
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

    故障注入方向（2026-09-18 更正 + **实测**）：弄坏 conftest 里
    `pytest_sessionfinish` 的守卫判定 —— 把 `session.exitstatus = 1`
    那行注释掉，本用例必须转红。恒绿的守卫等于空转的绿。

    实测数字（在 `backend/` 的一份 /tmp 副本里做的，**零仓库写入** ——
    别在共享工作区里注入，那会重演"污染别人正在跑的会话"的事故）：
        cd <副本>/backend && pytest tests/test_pytest_repo_write_guard.py -q
        注入前：13 passed，退出码 0
        注入后：3 failed, 10 passed，退出码 1
    转红的正是依赖「子进程退出码非 0」的那三条：本条、
    test_guard_survives_when_last_test_is_xfail、
    test_cache_exclusion_is_precise_not_a_blind_relaxation。
    同时对照组 test_guard_stays_green_when_write_is_outside_protected_tree
    **保持绿** —— 证明这是精准转红，不是"怎么改都红"。
    另一个常被提到的等效注入（让 `_guard_check()` 恒返回空）**未实测**，
    别当成已验证的结论用。

    ⚠️ 旧说法已失效，别照它做：此前这里写的是「把 conftest 里的
    `_repo_write_guard` 整个删掉（或把 teardown 的 raise 换成 return），
    本用例必须转红」。2026-09-18 该 fixture 已整体删除，判定收敛到
    `pytest_sessionfinish` 单点（删除原因：session 级 fixture 的 teardown
    期间 stderr 写入会被 pytest 捕获吞掉，那行摘要一条都出不来 —— 静默
    空转）。实测：fixture 删掉之后本文件单独跑**仍然 13 passed**。
    所以注入必须打在 sessionfinish 上；打在 fixture 上会得到「守卫失效了」
    的错误结论，然后在错误的地方浪费半天。
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

    故障注入方向（2026-09-18 更正 + **实测**）：把 conftest
    `pytest_sessionfinish` 里守卫判定的 `session.exitstatus = 1` 注释掉，
    本用例转红。实测：注入前 13 passed / 退出码 0 → 注入后
    3 failed, 10 passed / 退出码 1（与 test_guard_detects_write_into_
    protected_tree 用的是同一次注入，数字与做法详见它的 docstring）。

    ⚠️ 旧说法「把 sessionfinish 那段守卫代码删掉，只留 fixture 的 raise」
    已不成立：`_repo_write_guard` 已于 2026-09-18 删除（teardown 期间写
    stderr 会被捕获吞掉 → 静默空转），**现在唯一的判定点就是这里的
    sessionfinish**，不存在"留 fixture 兜底"这个选项了。
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

    ⚠️ 2026-09-17 起 `guard_diff` 的返回元素从「路径字符串」改成
    :class:`GuardEntry`（带 mtime）。所以下面三处断言取的是 `.path` ——
    直接 `in p` 会静默返回 False（NamedTuple 上做 `in` 是逐字段 == 比较，
    不报错），那正是最难发现的一种"假绿"。
    """
    from conftest import guard_diff, guard_snapshot  # noqa: PLC0415

    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "keep.txt").write_text("same", encoding="utf-8")
    (root / "will_change.txt").write_text("v1", encoding="utf-8")
    (root / "will_delete.txt").write_text("bye", encoding="utf-8")

    before = guard_snapshot([root])
    assert before, "快照为空，用例前提失效"

    # 改动前的真实 mtime —— 下面用来钉住"基线 mtime 真的来自基线快照"，
    # 而不是随便填的一个数。
    base_change_ns = (root / "will_change.txt").stat().st_mtime_ns
    base_delete_ns = (root / "will_delete.txt").stat().st_mtime_ns

    (root / "added.txt").write_text("new", encoding="utf-8")
    (root / "will_change.txt").write_text("v2", encoding="utf-8")
    (root / "will_delete.txt").unlink()

    added, changed, removed = guard_diff(before, guard_snapshot([root]))

    assert any("added.txt" in e.path for e in added), f"新增未被识别: {added}"
    assert any("will_change.txt" in e.path for e in changed), f"改写未被识别: {changed}"
    assert any("will_delete.txt" in e.path for e in removed), f"删除未被识别: {removed}"
    assert not any("keep.txt" in e.path for e in added + changed + removed), (
        "未变动的文件被误报了")

    # ---- mtime：报错里"多久之前落的"必须有真实来源 ----
    added_entry = next(e for e in added if "added.txt" in e.path)
    assert added_entry.mtime_ns > 0, f"新增项没有 mtime: {added_entry}"
    assert added_entry.baseline_mtime_ns is None, (
        f"新增项不该有基线 mtime: {added_entry}")

    changed_entry = next(e for e in changed if "will_change.txt" in e.path)
    assert changed_entry.mtime_ns > 0, f"改写项没有当前 mtime: {changed_entry}"
    assert changed_entry.baseline_mtime_ns == base_change_ns, (
        f"改写项的基线 mtime 不是基线快照里的值（{base_change_ns}）: {changed_entry}")

    removed_entry = next(e for e in removed if "will_delete.txt" in e.path)
    assert removed_entry.mtime_ns == base_delete_ns, (
        f"删除项的 mtime 应取自基线快照（文件已经没了）: {removed_entry}")


def test_guard_message_carries_mtime_for_every_item(tmp_path):
    """报错文本里每一项都要带 mtime，改写项还要带基线 mtime。

    这不是"文本更好看"的问题：mtime 是判断"这次污染是本次会话造的，还是
    历史残留被改写"的**唯一**线索 —— 两者修法完全不同。

    故障注入方向：把 conftest 里 :class:`GuardEntry` 的 `mtime_ns` 默认值
    改成 0，或让 `_guard_message` 不再渲染它，本用例立刻转红。
    """
    from conftest import GuardEntry, _guard_message  # noqa: PLC0415

    now_ns = time.time_ns()
    fresh_ns = now_ns - 2 * 10 ** 9        # 2 秒前：本次会话刚写的
    old_ns = now_ns - 3 * 86400 * 10 ** 9  # 3 天前：历史残留

    text = _guard_message(
        [GuardEntry("/tree::new.txt", fresh_ns)],
        [GuardEntry("/tree::hit.txt", fresh_ns, old_ns)],
        [GuardEntry("/tree::gone.txt", old_ns)],
        now_ns=now_ns,
    )

    def _stamp(ns: int) -> str:
        return datetime.datetime.fromtimestamp(ns / 1e9).strftime(
            "%Y-%m-%d %H:%M:%S")

    # 三组各自的时间点都必须出现在文本里（缺哪组就少哪组的线索）
    for ns in (fresh_ns, old_ns):
        assert _stamp(ns) in text, f"报错里缺 mtime {_stamp(ns)}:\n{text}"

    # "距今多久"的量级：2 秒 → s，3 天 → d（写死量级是防止单位换算写错）
    assert "距今 2.0s" in text, f"新增项没有'距今多久':\n{text}"
    assert "距今 3.0d" in text, f"删除项没有'距今多久':\n{text}"

    # 基线 mtime 只应出现在改写组：新增项没有基线，多出来说明渲染串组了
    assert "基线 mtime=" in text, f"改写项没给基线 mtime:\n{text}"
    assert text.count("基线 mtime=") == 1, (
        f"基线 mtime 只应出现 1 次，实际 {text.count('基线 mtime=')} 次:\n{text}")

    # 对照组：mtime 拿不到时必须显式说"未知"，不能悄悄显示成 1970
    unknown = _guard_message([GuardEntry("/tree::x.txt", 0)], [], [],
                             now_ns=now_ns)
    assert "mtime=未知" in unknown, (
        f"mtime 缺失时应显式渲染成'未知'，而不是 1970 时间戳:\n{unknown}")

    # ---- 接线层：上面是直接构造 GuardEntry，钉不住 guard_diff →
    #      _guard_message 这条真实链路。这里用真实文件再走一遍。
    from conftest import guard_diff, guard_snapshot  # noqa: PLC0415

    root = tmp_path / "tree"
    root.mkdir(parents=True, exist_ok=True)
    (root / "hit.txt").write_text("v1", encoding="utf-8")
    (root / "gone.txt").write_text("bye", encoding="utf-8")
    before = guard_snapshot([root])
    (root / "hit.txt").write_text("v2", encoding="utf-8")
    (root / "new.txt").write_text("new", encoding="utf-8")
    (root / "gone.txt").unlink()

    a, c, r = guard_diff(before, guard_snapshot([root]))
    wired = _guard_message(a, c, r, now_ns=time.time_ns())

    for name in ("new.txt", "hit.txt", "gone.txt"):
        matched = [ln for ln in wired.splitlines() if name in ln]
        assert matched, (
            f"真实链路下 {name} 没出现在报错里（三组缺一就少一组线索）:\n{wired}")
    assert _stamp((root / "new.txt").stat().st_mtime_ns) in wired, (
        f"真实链路下新增项没渲染出真实 mtime:\n{wired}")
    assert "基线 mtime=" in wired, (
        f"真实链路下改写项没给基线 mtime:\n{wired}")
    # 光有"基线 mtime="这个标签不够：基线丢了会渲染成"未知"，
    # 标签还在、值没了 —— 那正是最难发现的一种半失效。
    assert "基线 mtime=未知" not in wired, (
        f"真实链路下基线 mtime 丢了（渲染成'未知'）:\n{wired}")


def test_guard_snapshot_ignores_interpreter_caches(tmp_path):
    """__pycache__ / .pyc 必须被排除，否则守卫每次都"变了"，立刻退化成噪音。

    顺带钉住快照值的形状：必须是 `(size, mtime_ns)`。第二个元素是
    `guard_diff` 产出 :class:`GuardEntry` 的 mtime **唯一来源** —— 只存
    size 的话守卫不会报错，只会静默退化成 `mtime=未知`，属于最难发现的
    "半失效"，所以这里必须断言。
    """
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

    size, mtime_ns = entries["real.txt"]
    assert size == len("hi"), f"快照第一个元素不是文件大小: {entries['real.txt']}"
    assert mtime_ns == (root / "real.txt").stat().st_mtime_ns, (
        f"快照第二个元素不是真实 mtime_ns，守卫的 mtime 就没有来源: "
        f"{entries['real.txt']}")


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


# ===========================================================================
# 「排除运行产物」必须是**精准**的，不是放宽（2026-09-17 补）
# ===========================================================================
# 背景：全量首次跑出现过一次守卫报红（「新增 1 项」），紧接着原样重跑就绿了。
# 一度被归因于「guard_snapshot 没排除 __pycache__ / *.pyc」——**该归因已被
# 实测推翻**：守卫自 f4a4c64 起就有 _GUARD_SKIP_DIR_NAMES /
# _GUARD_SKIP_SUFFIXES（见 conftest.py:250-254 与 290-292），且实测让
# pytest 在守卫树内**真的**生成 .pyc 之后，会话照样退出 0。
#
# 但那次报红是真实发生的，所以这里补的是**行为级**钉子。现有那条
# test_guard_snapshot_ignores_interpreter_caches 是单元级、往 tmp_path 写
# 一个**假的** .pyc —— 它钉不住「解释器在真实 pytest 会话里现场生成 .pyc」
# 这条路径，而那正是出问题的路径。
#
# 两条必须**成对**存在：少了下面 B 段，一个「把所有文件统统排除掉」的
# 实现同样能让 A 段绿 —— 那就是把守卫彻底放宽，比假红更糟。
_PYC_PROBE_PLUGIN = '''
"""让解释器在受保护目录里**真的**生成 .pyc。

⚠️ 这里**不能**自己写 .py 源文件：在受保护树里新建一个 .py 本身就是
「真实新增文件」，守卫报它是**对的**（第一版探针就是这么写错的，报红了
才发现）。真实场景是「.py 已提交、只有 .pyc 是新的」，所以 .py 由父进程
在子进程启动**之前**建好（这样它进的是基线），本插件只负责 import。
"""
import os
import pathlib
import sys


def pytest_configure(config):
    target = pathlib.Path(os.environ["{env_var}"])

    # 顶层裸 .pyc：单独验证 _GUARD_SKIP_SUFFIXES 分支（与 __pycache__
    # 分支是两条不同的代码路径，不能只测一条）
    (target / "top_level_artifact.pyc").write_bytes(b"\\x00\\x01\\x02")

    # 真实字节码生成：验证 _GUARD_SKIP_DIR_NAMES 的 __pycache__ 分支
    sys.path.insert(0, str(target))
    import mod_zz_probe  # noqa: F401  触发真实字节码写入
'''


def _run_child_with_pyc_probe(
    tmp_path: Path,
    probe_target: Path,
    guard_trees: str,
    timeout: int = 180,
) -> subprocess.CompletedProcess:
    """起子进程跑 pytest，并让解释器在受保护目录里真生成 .pyc。

    Args:
        tmp_path: 放探针插件的目录（不在受保护树内，避免自我污染）。
        probe_target: 受保护目录（.pyc 会生成在这里）。
        guard_trees: 传给子进程的 MONEYBAG_PYTEST_GUARD_TREES。
        timeout: 子进程超时秒数。

    Returns:
        subprocess.CompletedProcess。
    """
    # .py 源文件必须在子进程启动**之前**建好：这样它会被算进守卫基线，
    # 会话内真正新增的就只有 .pyc —— 这才是要测的东西。
    (probe_target / "mod_zz_probe.py").write_text(
        "VALUE = 1\n", encoding="utf-8")

    (tmp_path / "mbguard_pyc_probe.py").write_text(
        _PYC_PROBE_PLUGIN.format(env_var=_PROBE_TARGET_ENV), encoding="utf-8")

    env = os.environ.copy()
    env.pop("DATA_DIR", None)
    env.pop("MONEYBAG_PYTEST_DATA_DIR", None)
    env[_GUARD_TREES_ENV] = guard_trees
    env[_PROBE_TARGET_ENV] = str(probe_target)
    env["PYTHONPATH"] = str(tmp_path)
    env.pop(_GUARD_ENABLED_ENV, None)
    # 关键：本机 shell 里 PYTHONDONTWRITEBYTECODE=1，不清掉的话解释器
    # 一个 .pyc 都不会写，本用例会变成"什么都没测到"的假绿。
    env.pop("PYTHONDONTWRITEBYTECODE", None)

    return subprocess.run(
        [sys.executable, "-m", "pytest", _PROBE_TEST,
         "-q", "-p", "mbguard_pyc_probe", "-p", "no:cacheprovider"],
        cwd=str(_BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_guard_survives_real_interpreter_bytecode_in_protected_tree(tmp_path):
    """A 段：解释器在受保护树里**真生成** .pyc → 守卫必须仍然绿。

    这是对「pytest 为新测试文件生成 .pyc 导致首次跑假红」这一归因的
    直接证伪实验的行为级固化：只要这条绿，那个归因就不成立。

    ⚠️ 前提校验不可省：如果 PYTHONDONTWRITEBYTECODE 把字节码关掉了，
    子进程根本不会写 .pyc，本用例就会变成恒绿的空转。所以下面先断言
    .pyc 确实生成了，再断言守卫没报。
    """
    decoy = tmp_path / "decoy_protected"
    decoy.mkdir()

    proc = _run_child_with_pyc_probe(
        tmp_path, probe_target=decoy, guard_trees=str(decoy))

    generated = sorted(decoy.glob("__pycache__/*.pyc"))
    assert generated, (
        "子进程没有真的生成 .pyc —— 本用例会空转（多半是 "
        "PYTHONDONTWRITEBYTECODE 又混进子进程环境了）。\n"
        f"  stdout 尾部:\n{proc.stdout[-1200:]}")

    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, (
        f"解释器正常生成的 .pyc 让守卫报红了（假红）：{generated}\n"
        f"  stdout 尾部:\n{proc.stdout[-1500:]}\n"
        f"  stderr 尾部:\n{proc.stderr[-800:]}")
    assert "MONEYBAG_WRITE_GUARD" not in combined


def test_cache_exclusion_is_precise_not_a_blind_relaxation(tmp_path):
    """A+B 成对：产物不报，真实垃圾照报 —— 证明是精准排除而非放宽。

    两个方向缺一不可：
      * 只有 A：一个「排除一切」的实现也能绿，守卫等于被废掉；
      * 只有 B：一个「什么都不排除」的实现也能红，但会带回 .pyc 假红。
    """
    # ---- A 段：产物（真 .pyc + 顶层裸 .pyc）→ 必须绿 ----
    decoy_a = tmp_path / "decoy_a"
    decoy_a.mkdir()
    proc_a = _run_child_with_pyc_probe(
        tmp_path, probe_target=decoy_a, guard_trees=str(decoy_a))
    assert sorted(decoy_a.glob("__pycache__/*.pyc")), "A 段前提失效：没生成 .pyc"
    assert (decoy_a / "top_level_artifact.pyc").exists(), (
        "A 段前提失效：顶层裸 .pyc 没写进去，_GUARD_SKIP_SUFFIXES 这条分支"
        "就没被覆盖到")
    assert proc_a.returncode == 0, (
        f"产物让守卫报红了（假红）:\n{proc_a.stdout[-1200:]}")

    # ---- B 段：真实垃圾文件 → 必须红 ----
    decoy_b = tmp_path / "decoy_b"
    decoy_b.mkdir()
    (decoy_b / "sentinel.txt").write_text("keep me", encoding="utf-8")

    proc_b = _run_child_pytest(
        tmp_path, probe_target=decoy_b, guard_trees=str(decoy_b))

    combined_b = (proc_b.stdout or "") + (proc_b.stderr or "")
    assert proc_b.returncode != 0, (
        "真实垃圾文件没让守卫报红 —— 排除逻辑被写成了放宽，"
        "守卫已经失去意义。")
    assert "MONEYBAG_WRITE_GUARD" in combined_b, (
        "会话确实失败了，但不是守卫报的，B 段断言是假的。\n"
        f"  stdout 尾部:\n{proc_b.stdout[-1200:]}")


def test_backend_logs_is_still_a_protected_tree():
    """`backend/logs/` 必须仍在受保护清单里 —— 不许借"排除产物"顺手放宽。

    `backend/logs/llm_balance_monitor.log` 是历史上真实被写脏过的文件
    （645KB），把它排除掉等于把守卫的战果退回去。
    """
    from conftest import _DEFAULT_GUARD_TREES  # noqa: PLC0415

    resolved = {str(Path(p).resolve()) for p in _DEFAULT_GUARD_TREES}
    assert str((_BACKEND_DIR / "logs").resolve()) in resolved, (
        f"backend/logs 不在受保护清单里了: {resolved}")


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
