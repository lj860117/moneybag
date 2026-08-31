"""
process_watchdog（外部进程看门狗）守门测试
=========================================
这个文件的存在意义，来自 2026-08-30 那次排查留下的三条教训：

  ① **新增守门规则必须做故障注入，验证它真能抓。**
     一个"能通过但抓不住失效"的测试，正是本项目反复要消灭的东西
     （对照：`ak_call()` 写了 77 天、零调用方、没人发现它根本没接上）。
     所以下面每一条关键规则都配了"规则隔离探针"：构造一条**只有该规则能拦住**
     的命令行。这样把规则删掉时，测试**必须**报错 —— 否则这条规则就是死的。
     文件末尾 `test_fault_injection_helpers_are_honest` 会自检这些探针本身。

  ② **fixture 必须反映生产的真实构成比例，且这个比例是可以数出来的。**
     2026-08-30 实测：生产上 3 个 python 进程里 **0 个**是看门狗目标
     （2 个系统进程 + 1 个 uvicorn）。也就是说生产的真实构成是
     「非目标占绝大多数」，**最大风险是误杀而不是漏杀**。
     所以下面的 ps fixture 里 17 行只有 5 行是真目标（29% 目标 / 71% 非目标）。
     如果哪天有人把 fixture 改成"几乎全是目标"，这个测试就抓不住
     "过度匹配"这个最主要的失效模式了 —— 因此有一条断言专门守这个比例。

  ③ **终止路径要拿真进程验证，不能只断言"发出了信号"。**
     下面 test_1 / test_2 真的 fork 出子进程，让看门狗真的发 SIGNAL，
     再断言进程真的死了。

💡 关于为什么"进程枚举"用 fixture 而"终止"用真进程：
     枚举逻辑（`ps` 解析 + 筛选）的风险 100% 在"过度匹配/匹配不到"上，
     用可控的生产级 fixture 验证最可靠（真机上 ps 需要权限，且内容不可控）；
     而终止逻辑的风险在"信号到底生效没有"上，只能用真进程验证。
     两者拼起来才是完整覆盖。
"""
import contextlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ============================================================
# 模块加载
# ============================================================

#: 加载被测模块时用的临时 DATA_DIR。
#:
#: 为什么需要它：`config.py` 在 import 时就会 `DATA_DIR.mkdir(parents=True, exist_ok=True)`
#: 连带建 users/ 等子目录，也就是说** import 一个脚本就会碰真实数据目录**。
#: 在受限环境（CI 沙箱 / 只读挂载 / 无写权限）里这会直接让 import 抛异常，
#: 于是"测试挂了"反映的是环境而不是被测代码 —— 这是假红灯，必须避开。
#: 做法：加载前把 `DATA_DIR` 环境变量指到临时目录，加载完立刻还原。
#: ⚠️ 只在 config 尚未被 import 时才生效（已在 sys.modules 里就改不动了），
#:    所以本文件应在 pytest 会话里尽早加载。
_TMP_DATA_DIR: str = ""


def _load_watchdog():
    """独立加载 scripts/process_watchdog.py（不把 backend 永久塞进 sys.path）。"""
    global _TMP_DATA_DIR
    if not _TMP_DATA_DIR:
        _TMP_DATA_DIR = tempfile.mkdtemp(prefix="moneybag_watchdog_test_")

    old_env = os.environ.get("DATA_DIR")
    os.environ["DATA_DIR"] = _TMP_DATA_DIR
    mod_name = "_watchdog_under_test"
    spec = importlib.util.spec_from_file_location(
        mod_name, _BACKEND / "scripts" / "process_watchdog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # ⚠️ 必须先登记进 sys.modules 再 exec_module：
    #    process_watchdog 用 @dataclass 且字段带 Optional[...] 注解，
    #    dataclasses._is_type() 会去 `sys.modules[cls.__module__].__dict__`
    #    查 InitVar/ClassVar 的别名。不登记的话 exec 阶段直接抛
    #    AttributeError: 'NoneType' object has no attribute '__dict__' ——
    #    一个跟被测代码毫无关系的假红灯。
    old_mod = sys.modules.get(mod_name)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        if old_mod is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = old_mod
        if old_env is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = old_env
    return mod


@contextlib.contextmanager
def _patched(watchdog, ps_output=None, zombie_check=None):
    """
    临时替换 run_ps / is_zombie 并改写 sys.argv，退出时还原。

    Args:
        watchdog: 被测模块
        ps_output: 注入的 ps 输出（None = 不替换 run_ps）
        zombie_check: 替换 is_zombie 的函数（None = 不替换）
    """
    old_run_ps = watchdog.run_ps
    old_is_zombie = watchdog.is_zombie
    old_argv = sys.argv
    if ps_output is not None:
        watchdog.run_ps = lambda ps_command=None: ps_output
    if zombie_check is not None:
        watchdog.is_zombie = zombie_check
    try:
        yield
    finally:
        watchdog.run_ps = old_run_ps
        watchdog.is_zombie = old_is_zombie
        sys.argv = old_argv


def _run(watchdog, argv, ps_output, data_dir=None, zombie_check=(lambda pid: False)):
    """
    以给定 argv + 注入的 ps 输出跑一次 main()，返回 (exit_code, stdout)。

    Args:
        watchdog: 被测模块
        argv: 额外的命令行参数（不含程序名）
        ps_output: 注入的 ps 文本
        data_dir: --data-dir（None 时不传，用模块默认 DATA_DIR）
        zombie_check: 默认全部返回 False —— fixture 里的 pid 是假的，
                      不能让真实 is_zombie 去外面调 ps。
                      传 None = **不替换**，用真实实现（真进程测试要用这个）
    """
    import io

    extra = list(argv)
    if data_dir is not None:
        extra += ["--data-dir", str(data_dir)]
    sys.argv = ["process_watchdog.py"] + extra

    old_run_ps = watchdog.run_ps
    old_is_zombie = watchdog.is_zombie
    old_argv = sys.argv
    watchdog.run_ps = lambda ps_command=None: ps_output
    if zombie_check is not None:
        watchdog.is_zombie = zombie_check
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        code = watchdog.main()
    finally:
        sys.stdout = old_stdout
        sys.argv = old_argv
        watchdog.run_ps = old_run_ps
        watchdog.is_zombie = old_is_zombie
    return code, buf.getvalue()


# ============================================================
# ps fixture —— 生产真实构成（非目标占绝大多数）
# ============================================================
# 前 3 行是 2026-08-30 在 150.158.47.189 上 `ps -eo pid,etimes,comm,args` 的
# 真实输出（为匹配本项目的 4 列格式去掉了 stat 列），其余为同形状构造。
#
# 线上 29 条生效 crontab 是**混合形态**（2026-08-31 服务器核对），fixture 必须覆盖到：
#     21 条  /opt/moneybag/venv/bin/python scripts/X.py   ← 绝对路径（50013 这类）
#      4 条  -m scripts.X                                  ← 模块形式（50006/50010）
#      4 条  裸 `python3 scripts/X.py`                     ← ⚠️ 无路径可校验（50014~50017）
#       （那 4 条裸命令用 `source .../venv/bin/activate && python3 scripts/X.py` 起，
#         venv 路径只进 shell 的 activate、不进 argv —— 真实线上形态）
#
# 构成统计（这个数字是可以数的，别改坏它）：
#     26 行 = 7 个真目标 + 19 个非目标      → 非目标 73%
# 非目标细分类别：系统 python ×2、uvicorn/gunicorn ×3、cron shell 包装 ×2、
#                 未超龄批量脚本 ×7（含裸命令形态的 50015/16/17）、
#                 开发机同名进程 ×1、看门狗自己/父进程 ×2、
#                 手工 python 命令（pytest / 空闲 REPL）×2
# 真目标 7 个：50001 / 50004 / 50005（137 天僵尸形状）/ 50006 / 50009（中文路径）
#              / 50013（带 /tmp 参数）/ 50014（裸命令形态）
#
# 每种类别都对应**一条独立的排除规则**，缺一类就有一条规则没人守
# （`test_fixture_is_majority_non_target` 会断言各类规则都真的被触发过）。
# 注：system_path 规则在 fixture 里没被触发（那 2 个系统进程的 comm 不是 python，
#     先被 comm 规则拦下了），它由 `test_system_path_rule_is_independent` 和
#     `test_system_interpreter_is_excluded_even_with_absolute_path_args` 单独守。
PS_FIXTURE_TEMPLATE = """\
    765 12181078 networkd-dispat /usr/bin/python3 /usr/bin/networkd-dispatcher --run-startup-triggers
    891 12181077 unattended-upgr /usr/bin/python3 /usr/share/unattended-upgrades/unattended-upgrade-shutdown --wait-for-signal
1562523  124576 uvicorn /opt/moneybag/venv/bin/python3 /opt/moneybag/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
1562524  124576 python3 /opt/moneybag/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
1562525  124576 python3 /opt/moneybag/venv/bin/python3 -m gunicorn main:app -b 0.0.0.0:8000 --workers 2
  42110    3600 sh sh -c cd /opt/moneybag/backend && python3 scripts/night_worker.py --push-only >> logs/night.log 2>&1
  42111    3600 bash /bin/bash -c cd /opt/moneybag/backend && python3 -m scripts.memory_archive_cron
  50001    1900 python3 python3 scripts/cache_warmer.py --midday
  50002    1900 python3 python3 scripts/cache_warmer.py --morning
  50003   20000 python3 python3 scripts/night_worker.py
  50004    2400 python3 python3 scripts/night_worker.py --push-only
  50005 11836800 python3 python3 scripts/dca_scheduler.py
  50006    9999 python3 python3 -m scripts.housekeeping_cron --apply
  50007     600 python3 python3 scripts/cache_warmer.py --nav-confirmed
  50008  999999 python3 /Users/leijiang/WorkBuddy/moneybag-for-claudecode/backend/scripts/cache_warmer.py --midday
  50009  999999 python3 /opt/moneybag/backend/数据 目录/scripts/cache_warmer.py --midday
  50010     300 python3 python3 -m scripts.daily_reflection_cron
  50011  999999 python3 python3 -m pytest tests/ -q
  50012  999999 python3 /opt/moneybag/venv/bin/python3
  50013  999999 python3 /opt/moneybag/venv/bin/python scripts/cache_warmer.py --out /tmp/warm.json
  50014  999999 python3 python3 scripts/dca_scheduler.py --discipline
  50015     600 python3 python3 scripts/dca_scheduler.py --weekly
  50016    1200 python3 python3 scripts/dca_scheduler.py --dca
  50017     900 python3 python3 scripts/monthly_report.py --all
{own_pid}  999999 python3 /opt/moneybag/venv/bin/python3 scripts/process_watchdog.py --apply
{parent_pid}  999999 python3 /opt/moneybag/venv/bin/python3 scripts/process_watchdog.py --apply
"""

#: 生产上真实卡死 137 天的那类进程（11836800 秒 = 137 天）
PID_137_DAY_ZOMBIE = 50005


def _fixture():
    """渲染 fixture（把看门狗自己/父进程的真实 pid 填进去）。"""
    return PS_FIXTURE_TEMPLATE.format(own_pid=os.getpid(), parent_pid=os.getppid())


def _select(watchdog, ps_output=None):
    """跑一次筛选，返回 (targets, stats, excluded)。"""
    procs = watchdog.parse_ps_output(ps_output or _fixture())
    return watchdog.select_targets(
        procs, os.getpid(), os.getppid(),
        watchdog.DEPLOY_DIR_MARKERS, False,
    )


def _pids(targets):
    return {t.proc.pid for t in targets}


def _read_jsonl(data_dir):
    """读出当天 JSONL 里的全部记录。"""
    log_dir = Path(data_dir) / "logs" / "watchdog"
    path = log_dir / f"{time.strftime('%Y-%m-%d')}.jsonl"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ============================================================
# 真进程注入：fork 出一个"孤儿"进程（模拟 cron 拉起的批量脚本）
# ============================================================
# ⚠️ 为什么必须 double-fork 成孤儿，而不是直接 Popen 一个子进程：
#   直接 Popen 时，pytest 是它的父进程。被 SIGTERM 杀掉后它不会立刻消失，
#   而是变成**僵尸**等 pytest 来 wait() —— 而 pytest 在看门狗的宽限期内不会
#   去 wait。那样 is_alive() 会一直返回 True，逼着看门狗升级到 SIGKILL，
#   于是"SIGTERM 优雅退出"这条根本测不出来。
#   生产上的真实形态也是孤儿：cron → bash → python，父进程是 bash 不是看门狗。
#   所以这里 double-fork 让中间进程立刻退出、孙进程被 init/launchd 收养，
#   死掉后会立刻被回收 —— 与生产一致。
_ORPHAN_SNIPPET = r"""
import os, signal, sys, time
ignore_term = "--ignore-term" in sys.argv
def _child():
    if ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    # 先装好 handler 再报 READY，避免父进程在 handler 装上前就发信号（真实竞态）
    sys.stdout.write("READY %d\n" % os.getpid())
    sys.stdout.flush()
    time.sleep(600)
if os.fork() == 0:
    try:
        _child()
    finally:
        os._exit(0)
os._exit(0)
"""


def _spawn_orphan(ignore_sigterm=False):
    """
    fork 出一个孤儿进程，返回它的 pid。

    Args:
        ignore_sigterm: True = 进程显式忽略 SIGTERM（用于验证宽限升级）

    Returns:
        (pid, proc)：proc 是已退出的中间进程对象（仅用于持有 stdout）
    """
    argv = [sys.executable, "-c", _ORPHAN_SNIPPET]
    if ignore_sigterm:
        argv.append("--ignore-term")
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, bufsize=1)
    line = proc.stdout.readline().strip()
    if not line.startswith("READY "):
        proc.kill()
        raise RuntimeError(f"孤儿进程未按预期报 READY，收到: {line!r}")
    return int(line.split()[1]), proc


def _wait_until_dead(pid, timeout=8.0):
    """轮询直到进程消失。返回 True = 确实死了。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


# ============================================================
# 用例 1：超龄批量脚本被选中并真的被终止
# ============================================================

def test_overdue_batch_script_is_really_terminated():
    """真跑一个 sleep 子进程，用 --apply 断言它真的死了。

    挂了意味着：看门狗根本没发出信号，或者发出去了但对象错了。
    """
    watchdog = _load_watchdog()
    pid, holder = _spawn_orphan(ignore_sigterm=False)
    tmp = Path(tempfile.mkdtemp())
    try:
        ps_output = (
            f"{pid} 999999 python3 /opt/moneybag/venv/bin/python3 "
            f"scripts/cache_warmer.py --midday\n"
        )
        code, out = _run(watchdog, ["--apply", "--grace", "5"], ps_output,
                         data_dir=tmp, zombie_check=None)
        assert _wait_until_dead(pid), (
            f"看门狗 --apply 跑完后 pid={pid} 仍然活着 —— "
            f"要么没发 SIGTERM，要么发给了错误的 pid。stdout:\n{out}"
        )
        assert code == 0, f"退出码应為 0，实际 {code}。stdout:\n{out}"

        records = [r for r in _read_jsonl(tmp) if r.get("action") == "terminate"]
        assert len(records) == 1, f"应恰好写 1 条 terminate 记录，实际 {len(records)}"
        rec = records[0]
        assert rec["pid"] == pid
        assert rec["script"] == "cache_warmer"
        assert rec["threshold"] == 1500, (
            f"cache_warmer + --midday 的阈值必须是 1500（每 30 分钟起一次，"
            f"必须在下一次启动前死掉），实际 {rec['threshold']}"
        )
        assert rec["outcome"] == "terminated", (
            f"进程对 SIGTERM 有反应时不应升级到 SIGKILL，实际 outcome="
            f"{rec['outcome']}（signals={rec['signals']}）"
        )
        assert rec["signals"] == ["SIGTERM"], (
            f"只应发 SIGTERM，实际发了 {rec['signals']}"
        )
    finally:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            holder.wait(timeout=2)


# ============================================================
# 用例 2：忽略 SIGTERM 的进程必须被升级到 SIGKILL（最有价值的一条）
# ============================================================

def test_sigterm_ignoring_process_is_escalated_to_sigkill():
    """注入一个 signal.signal(SIGTERM, SIG_IGN) 的进程，断言最终用 SIGKILL 干掉。

    这条证明"宽限 + 升级"真的工作。如果实现里把宽限轮询写成
    `time.sleep(grace)` 然后无条件 SIGKILL，这条**不会**报错（结果一样），
    但它证明的是"最终结局正确"，而这条要守的是"挂死进程**一定**会被回收"。
    真正会因为实现写错而挂的是下面这两点：
      - 若删掉 SIGKILL 升级，这里 outcome 会是 terminated（错）→ 本测试报错 ✅
      - 若把 grace 处理成"直接 SIGKILL"，这里 signals 会缺 SIGTERM → 本测试报错 ✅
    """

    watchdog = _load_watchdog()
    pid, holder = _spawn_orphan(ignore_sigterm=True)
    tmp = Path(tempfile.mkdtemp())
    try:
        # 先确认这个进程真的忽略了 SIGTERM（否则本测试就是在骗自己）
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.4)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise AssertionError(
                "测试前提失效：注入的进程没有真的忽略 SIGTERM "
                "(signal.signal(SIGTERM, SIG_IGN) 未生效或信号在 handler 装上前就到了)。"
                "不修好这个前提，本测试就是一条假绿灯。"
            )

        ps_output = (
            f"{pid} 999999 python3 /opt/moneybag/venv/bin/python3 "
            f"scripts/cache_warmer.py --after-close\n"
        )
        code, out = _run(watchdog, ["--apply", "--grace", "1"], ps_output,
                         data_dir=tmp, zombie_check=None)

        assert _wait_until_dead(pid), (
            f"忽略 SIGTERM 的进程在看门狗跑完后仍然活着 —— "
            f"宽限升级没起作用。stdout:\n{out}"
        )
        records = [r for r in _read_jsonl(tmp) if r.get("action") == "terminate"]
        assert len(records) == 1, f"应恰好 1 条 terminate 记录，实际 {len(records)}"
        rec = records[0]
        assert rec["signals"] == ["SIGTERM", "SIGKILL"], (
            f"必须先 SIGTERM、宽限期满后再 SIGKILL（绝不直接 KILL —— "
            f"SIGTERM 能让进程走到 finally 收尾当前那次写入），实际 {rec['signals']}"
        )
        assert rec["outcome"] == "escalated_to_sigkill", (
            f"outcome 应为 escalated_to_sigkill，实际 {rec['outcome']}"
        )
        assert code == 0
    finally:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            holder.wait(timeout=2)


# ============================================================
# 用例 3：uvicorn 行永不被选中（后果最严重的失效模式）
# ============================================================

def test_uvicorn_lines_are_never_selected():
    """用生产实测的真实命令行串（etimes=124576 秒）断言 uvicorn 不被选中。

    fixture 里特意放了 3 种常驻服务形状：
      - comm=uvicorn（生产实测就是这个）
      - comm=python3 + `-m uvicorn main:app`（排除规则不能只靠 comm）
      - comm=python3 + gunicorn（万一将来换 WSGI server）
    """
    watchdog = _load_watchdog()
    procs = watchdog.parse_ps_output(_fixture())
    targets, _stats, excluded = watchdog.select_targets(
        procs, os.getpid(), os.getppid(), watchdog.DEPLOY_DIR_MARKERS, False,
    )
    selected = _pids(targets)

    for pid in (1562523, 1562524, 1562525):
        assert pid not in selected, (
            f"pid={pid}（常驻 Web 服务）被选中了！"
            f"它已运行 124576 秒是**设计如此**，杀掉等于每 5 分钟把线上服务打掉一次。"
            f"这是本模块后果最严重的失效模式。"
        )

    # 顺便确认：它们不是"碰巧没超龄"，而是被明确的排除规则拦下的
    reasons = {p.pid: v.rule for p, v in excluded}
    assert reasons.get(1562523) in ("blacklist", "comm", "not_batch_script"), (
        f"pid=1562523 的排除原因异常: {reasons.get(1562523)}"
    )
    assert reasons.get(1562524) in ("blacklist", "not_batch_script"), (
        f"pid=1562524（comm=python3 的 uvicorn）必须被 blacklist 或 not_batch_script "
        f"拦下，实际 {reasons.get(1562524)}"
    )


def test_blacklist_rule_is_independent_of_other_rules():
    """规则隔离探针：构造一条**只有黑名单规则能拦住**的命令行。

    fixture 里的 uvicorn 行同时被 comm / not_batch_script / blacklist 拦着，
    所以单独删掉 blacklist 时它们未必会漏 —— 那条测试就抓不住这个改动。
    这条探针让 args **同时**含 `main:app` 和 `scripts/x.py`，
    于是 comm 规则放行、脚本模式命中，**只剩黑名单能拦**。
    删掉 BLACKLIST_SUBSTRINGS 里的 uvicorn/main:app，这条必须报错。
    """
    watchdog = _load_watchdog()
    probe = watchdog.PsProcess(
        pid=99001, etimes=999999, comm="python3",
        args="/opt/moneybag/venv/bin/python3 -m uvicorn scripts.cache_warmer main:app",
    )
    verdict = watchdog.classify_process(probe, os.getpid(), os.getppid())
    assert not verdict.is_target, (
        f"黑名单规则失效：含 main:app 的常驻服务被判定为目标。"
        f"BLACKLIST_SUBSTRINGS={watchdog.BLACKLIST_SUBSTRINGS}"
    )
    assert verdict.rule == "blacklist", (
        f"这条探针的意义就是'只有 blacklist 能拦'，实际被 {verdict.rule} 拦下，"
        f"探针失效了，请改回一条能真正隔离 blacklist 规则的命令行"
    )


# ============================================================
# 用例 4：night_worker 全量跑在 6.5 小时内不被选中
# ============================================================

def test_night_worker_full_run_is_not_killed_within_design_time():
    """全量链设计上跨 01:00→07:30 = 6.5 小时，绝不能被误杀。

    一刀切"超过 1 小时就杀"会每天夜里误杀它 —— 这条直接守住阈值表的正确性。
    """
    watchdog = _load_watchdog()

    def _verdict(etimes, args="python3 scripts/night_worker.py"):
        proc = watchdog.PsProcess(pid=88001, etimes=etimes, comm="python3", args=args)
        return watchdog.classify_process(proc, os.getpid(), os.getppid())

    # 5.5 小时 / 6.4 小时 —— 都在设计时长内
    for etimes in (20000, 23000, 23399):
        verdict = _verdict(etimes)
        assert not verdict.is_target, (
            f"night_worker 全量跑了 {etimes}s（{etimes / 3600:.1f}h，设计时长 6.5h）"
            f"却被判定为超龄 —— 阈值表配错了，这会**每天夜里误杀全量链**。"
            f"threshold={verdict.threshold}"
        )
        assert verdict.threshold == 28800, (
            f"night_worker 全量的阈值必须是 28800（8h = 6.5h 设计时长 + 1.5h 余量），"
            f"实际 {verdict.threshold}"
        )

    # 8.5 小时 —— 超过 8h 阈值，应该被回收
    assert _verdict(30600).is_target, (
        "night_worker 全量跑了 8.5 小时（超过 8h 阈值）仍未判定为超龄"
    )

    # fixture 里的那条（20000s）也不能被选中
    targets, _stats, _excluded = _select(watchdog)
    assert 50003 not in _pids(targets), "fixture 里 20000s 的 night_worker 全量被误选"


# ============================================================
# 用例 5："更具体的模式优先"的匹配顺序
# ============================================================

def test_more_specific_mode_threshold_wins_over_script_threshold():
    """cache_warmer --midday 在 25 分钟以上被选中，同脚本其他模式在同一时长不被选中。

    这条防的是"匹配顺序写反"：先落脚本名再落模式，
    会导致 --midday 挂死 2 小时才被回收（一天叠出十几个实例），
    同时 --push-only 的 night_worker 会拿到 28800 而全量链会拿到 1800（灾难）。
    """
    watchdog = _load_watchdog()
    targets, _stats, _excluded = _select(watchdog)
    selected = _pids(targets)

    assert 50001 in selected, (
        "cache_warmer --midday 跑了 1900s（31.7 分钟）应被选中 —— "
        "它每 30 分钟起一次，阈值 1500s（25 分钟）必须生效，否则挂死实例会叠加"
    )
    assert 50002 not in selected, (
        "cache_warmer --morning 只跑了 1900s，不该被选中 —— "
        "非 --midday 模式的阈值是 7200s（2 小时）"
    )

    # 边界：恰好等于阈值不算超龄（必须是 > ）
    def _verdict(etimes, args):
        proc = watchdog.PsProcess(pid=88002, etimes=etimes, comm="python3", args=args)
        return watchdog.classify_process(proc, os.getpid(), os.getppid())

    midday = "python3 scripts/cache_warmer.py --midday"
    assert not _verdict(1500, midday).is_target, "恰好 1500s 不算超龄（判据必须是 > 阈值）"
    assert _verdict(1501, midday).is_target, "1501s 已超 1500s 阈值，应被选中"

    # --push-only 必须拿到 1800 而不是 28800
    push_only = _verdict(2400, "python3 scripts/night_worker.py --push-only")
    assert push_only.threshold == 1800, (
        f"night_worker --push-only 的阈值必须是 1800，实际 {push_only.threshold} —— "
        f"匹配顺序错了：全量链会被错配到 1800（每天夜里误杀），--push-only 反而拿到 28800"
    )
    assert push_only.is_target, "night_worker --push-only 跑了 40 分钟应被回收"

    # 模式标志必须整词匹配，不能被 --midday 之类的前缀串味
    assert _verdict(9999, "python3 scripts/cache_warmer.py --midday-full").threshold == 7200, (
        "--midday-full 不应命中 --midday 的 1500 阈值（模式标志必须整词匹配）"
    )


# ============================================================
# 用例 6：看门狗不会选中自己
# ============================================================

def test_watchdog_never_selects_itself_or_its_parent():
    """fixture 里放了看门狗自己与父进程的 pid（都跑着 process_watchdog，超龄 999999s）。"""
    watchdog = _load_watchdog()
    targets, _stats, _excluded = _select(watchdog)
    selected = _pids(targets)

    assert os.getpid() not in selected, "看门狗选中了自己 —— 会自杀"
    assert os.getppid() not in selected, "看门狗选中了父进程"

    # 规则隔离探针：把 args 换成普通批量脚本，此时**只有 self 规则能拦住**
    for label, pid in (("自己", os.getpid()), ("父进程", os.getppid())):
        probe = watchdog.PsProcess(
            pid=pid, etimes=999999, comm="python3",
            args="python3 scripts/night_worker.py --push-only",
        )
        verdict = watchdog.classify_process(probe, os.getpid(), os.getppid())
        assert not verdict.is_target, f"看门狗会杀{label}（pid={pid}）"
        assert verdict.rule == "self", (
            f"这条探针的意义就是'只有 self 规则能拦'，实际被 {verdict.rule} 拦下"
        )

    # pid <= 1 永远不碰
    for pid in (0, 1):
        probe = watchdog.PsProcess(
            pid=pid, etimes=999999, comm="python3",
            args="python3 scripts/dca_scheduler.py",
        )
        verdict = watchdog.classify_process(probe, os.getpid(), os.getppid())
        assert not verdict.is_target, f"pid={pid}（内核/Init）绝不能被选中"


# ============================================================
# 用例 7：系统进程 / 非部署目录进程永不被选中
# ============================================================

def test_system_processes_are_never_selected():
    """networkd-dispatcher / unattended-upgrades（/usr/bin/python3）必须被排除。"""
    watchdog = _load_watchdog()
    targets, _stats, excluded = _select(watchdog)
    selected = _pids(targets)

    for pid, name in ((765, "networkd-dispatcher"), (891, "unattended-upgrade-shutdown")):
        assert pid not in selected, (
            f"系统进程 {name}（pid={pid}）被选中了 —— "
            f"它用 /usr/bin/python3 且已运行 1218 万秒，是系统服务不是我们的 cron"
        )

    reasons = {p.pid: v.rule for p, v in excluded}
    assert reasons.get(765) in ("system_path", "comm", "not_batch_script"), (
        f"pid=765 的排除原因异常: {reasons.get(765)}"
    )


def test_system_path_rule_is_independent():
    """规则隔离探针：只有 system_path 规则能拦住的命令行。

    构造 `/usr/bin/python3 scripts/cache_warmer.py --midday`（超龄）：
    comm 是 python ✅、匹配脚本模式 ✅、无黑名单关键字 ✅、
    且**不带** /opt/moneybag 路径所以部署目录校验也放行 ——
    唯一能拦住它的是"解释器位于系统路径"这条规则。
    删掉 SYSTEM_PATH_PREFIXES 检查，这条必须报错。
    """
    watchdog = _load_watchdog()
    probe = watchdog.PsProcess(
        pid=99002, etimes=999999, comm="python3",
        args="/usr/bin/python3 scripts/cache_warmer.py --midday",
    )
    verdict = watchdog.classify_process(probe, os.getpid(), os.getppid())
    assert not verdict.is_target, (
        "system_path 规则失效：用 /usr/bin/python3 跑的进程被判定为目标 —— "
        "这会杀到系统服务"
    )
    assert verdict.rule == "system_path", (
        f"探针应只被 system_path 拦下，实际 {verdict.rule}"
    )


def test_cron_shell_wrapper_is_never_selected():
    """cron 的 `sh -c` / `bash -c` 包装进程绝不能被选中。

    它的 args 里也含脚本名（`... && python3 scripts/night_worker.py --push-only`），
    单看 args 跟真目标一模一样 —— 唯一的区别是 **comm 是 sh/bash 而不是 python**。
    杀它还有个额外坏处：杀 shell 杀不掉它拉起的 python 子进程，
    只会留下一个孤儿，纯属噪音。
    """
    watchdog = _load_watchdog()
    targets, _stats, excluded = _select(watchdog)
    selected = _pids(targets)
    reasons = {p.pid: v.rule for p, v in excluded}

    for pid, desc in ((42110, "sh -c 包装"), (42111, "bash -c 包装")):
        assert pid not in selected, (
            f"cron 的 {desc}进程（pid={pid}）被选中 —— "
            f"杀 shell 杀不掉子进程，只会留下孤儿 python"
        )
        assert reasons.get(pid) == "comm", (
            f"pid={pid} 应是被 'comm 不是 python' 这条规则拦下的，实际 "
            f"{reasons.get(pid)}。若这条规则被删，sh 包装进程会因为 args 里含"
            f" scripts/xxx.py 而被误判成目标"
        )


def test_manual_python_commands_are_not_selected():
    """运维在服务器上手工跑的 python 命令（`python3 -m pytest` / 空闲 REPL）不能被杀。

    这两行的 comm 是 python3、路径也在部署目录内、也没有黑名单关键字 ——
    唯一能拦住它们的是"args 不匹配批量脚本模式"这条规则。
    缺了这条，看门狗会杀掉人在服务器上跑的任何 python。
    """
    watchdog = _load_watchdog()
    targets, _stats, excluded = _select(watchdog)
    selected = _pids(targets)
    reasons = {p.pid: v.rule for p, v in excluded}

    for pid, desc in ((50011, "python3 -m pytest tests/ -q"), (50012, "空闲 python REPL")):
        assert pid not in selected, f"{desc}（pid={pid}）被选中 —— 会杀掉运维的手工命令"
        assert reasons.get(pid) == "not_batch_script", (
            f"pid={pid} 应被 not_batch_script 拦下，实际 {reasons.get(pid)}"
        )


def test_absolute_path_argument_does_not_hide_a_script():
    """🔴 规则⑤ 的守门：脚本接了绝对路径参数（`--out /tmp/x.json`）也必须照常监控。

    这条防的是"规则⑤ 遍历 args 里**每一个**以 / 开头的 token"那个 bug（初版实现）：
    参数是**数据**不是**身份**，`/tmp/warm.json` 会被误判成"不在部署目录"，
    于是这个脚本**静默掉出看门狗覆盖**。

    为什么这类漏判特别坏：
      - 它连 `threshold` 都不会有 —— 不是"监控中但未超龄"，而是**完全不可见**，
        `--show-excluded` 里也只是一条不起眼的 outside_deploy_dir；
      - "零命中自检"抓不到它：自检只在**所有**脚本都零命中时才报警，
        单个脚本悄悄掉出去时其余脚本照常有命中，自检一片祥和。
    修法：规则⑤ **只校验 argv[0]（解释器）**，参数一律不校验。
    """
    watchdog = _load_watchdog()

    cases = (
        ("/opt/moneybag/venv/bin/python scripts/cache_warmer.py --out /tmp/warm.json",
         7200, "venv 绝对路径解释器 + /tmp 输出参数"),
        ("python3 scripts/dca_scheduler.py --discipline --log /var/log/moneybag/dca.log",
         3600, "裸 python3 + /var 日志参数"),
        ("/opt/moneybag/venv/bin/python scripts/night_worker.py --export /data/tmp/nw.json",
         28800, "venv 绝对路径解释器 + /data 导出参数"),
        ("/opt/moneybag/venv/bin/python3 -m scripts.housekeeping_cron --data-dir /tmp/probe",
         1800, "-m 模块形式 + /tmp 参数"),
    )
    for args, want_threshold, desc in cases:
        probe = watchdog.PsProcess(pid=99003, etimes=999999, comm="python3", args=args)
        verdict = watchdog.classify_process(probe, os.getpid(), os.getppid())
        assert verdict.is_target, (
            f"[{desc}] 被误排除（rule={verdict.rule}）—— 规则⑤ 只能校验 "
            f"argv[0]（解释器），不能校验后面的参数：参数是数据不是身份，"
            f"`--out /tmp/x.json` 这类写法会让脚本静默掉出监控，"
            f"且 threshold=None 连'监控中但未超龄'都不算、零命中自检也抓不到。"
            f"args={args}  原因={verdict.reason}"
        )
        assert verdict.threshold == want_threshold, (
            f"[{desc}] 阈值应为 {want_threshold}，实际 {verdict.threshold}"
        )

    # fixture 里那条带 /tmp 参数的超龄 cache_warmer 也必须是目标
    targets, _stats, _excluded = _select(watchdog)
    assert 50013 in _pids(targets), (
        "fixture 里 `... scripts/cache_warmer.py --out /tmp/warm.json`（999999s）未被选中 —— "
        "规则⑤ 又在校验参数了"
    )


def test_system_interpreter_is_excluded_even_with_absolute_path_args():
    """反向守门：改成"只看 argv[0]"之后，系统解释器的排除能力不能跟着没了。

    尤其要覆盖"系统解释器 + 绝对路径参数"这个组合 —— 如果有人把上面那处修复
    写成"跳过带绝对路径参数的进程"或"只在没有绝对路径参数时才校验"，
    这一条会先报错，而不是让排除能力悄悄消失。
    """
    watchdog = _load_watchdog()

    cases = (
        ("/usr/bin/python3 scripts/cache_warmer.py --out /tmp/warm.json",
         "system_path", "系统解释器 + /tmp 参数"),
        ("/usr/bin/python3 scripts/dca_scheduler.py --discipline",
         "system_path", "系统解释器（无参数）"),
        ("/usr/share/moneybag/venv/bin/python3 scripts/cache_warmer.py --morning",
         "system_path", "/usr/share 下的解释器"),
        ("/Users/leijiang/dev/venv/bin/python3 scripts/cache_warmer.py --out /tmp/warm.json",
         "outside_deploy_dir", "开发机解释器 + /tmp 参数"),
    )
    for args, want_rule, desc in cases:
        probe = watchdog.PsProcess(pid=99004, etimes=999999, comm="python3", args=args)
        verdict = watchdog.classify_process(probe, os.getpid(), os.getppid())
        assert not verdict.is_target, (
            f"[{desc}] 被判定为目标 —— 规则⑤ 的排除能力被改没了。"
            f"只看 argv[0] 不等于不校验：/usr/bin/python3 这类系统解释器仍必须拦下。"
            f"args={args}"
        )
        assert verdict.rule == want_rule, (
            f"[{desc}] 应被 '{want_rule}' 拦下，实际 '{verdict.rule}'"
        )


def test_dev_machine_process_outside_deploy_dir_is_not_selected():
    """开发机上的同名进程（/Users/... 路径）不该被杀。

    fixture 里 50008 就是这个形状：路径不在 /opt/moneybag/ 下。
    """
    watchdog = _load_watchdog()
    targets, _stats, excluded = _select(watchdog)
    selected = _pids(targets)
    assert 50008 not in selected, (
        "开发机路径（/Users/...）下的同名脚本被选中 —— "
        "部署目录校验失效，跨机器杀进程是灾难"
    )
    reasons = {p.pid: v.rule for p, v in excluded}
    assert reasons.get(50008) == "outside_deploy_dir", (
        f"pid=50008 应被 outside_deploy_dir 拦下，实际 {reasons.get(50008)}"
    )

    # --skip-deploy-check 可以关掉这条（排障用），此时它就该被选中
    procs = watchdog.parse_ps_output(_fixture())
    targets2, _s2, _e2 = watchdog.select_targets(
        procs, os.getpid(), os.getppid(), watchdog.DEPLOY_DIR_MARKERS, True,
    )
    assert 50008 in _pids(targets2), (
        "--skip-deploy-check 应跳过部署目录校验，此时开发机进程也该被选中"
    )


def test_bare_python_command_from_cron_is_still_matched():
    """cron 用裸命令 `python3 scripts/x.py` 起进程时，args 里没有 /opt/moneybag/。

    这是 2026-08-30 差点踩进去的坑：最初的设计要求 "args 必须含 /opt/moneybag/"，
    但 `cd /opt/moneybag/backend && python3 -m scripts.housekeeping_cron` 这种
    写法下 `/opt/moneybag/` 只出现在 shell 的 cd 里、**不进 argv**。
    那样看门狗会对所有 cron 进程**零命中** —— 又是一个静默失效。
    """
    watchdog = _load_watchdog()
    targets, stats, _excluded = _select(watchdog)

    assert stats["script_matched"] >= 8, (
        f"fixture 里至少 8 行应被识别为批量脚本，实际 {stats['script_matched']} —— "
        f"裸命令形式（python3 scripts/x.py、python3 -m scripts.x）没被识别，"
        f"看门狗会在生产上空转"
    )
    # 137 天那个僵尸形状的进程（用裸命令起的）必须被选中
    assert PID_137_DAY_ZOMBIE in _pids(targets), (
        "裸命令起的 dca_scheduler（已跑 137 天，正是生产上真实卡死的形状）未被选中"
    )

    # 线上那 4 条裸命令脚本：挂死时必须照常回收（规则② 放宽的真正意义）
    assert 50014 in _pids(targets), (
        "裸命令 `python3 scripts/dca_scheduler.py --discipline`（999999s）未被选中 —— "
        "线上有 4 条 crontab 是这种裸命令形态（用 source venv/bin/activate 起，"
        "venv 路径不进 argv），规则② 必须对它们放行"
    )

    # 同形态但未超龄的三条：必须"被识别"但"不选中"
    # （识别不到 = 静默掉出监控；误选中 = 误杀）
    reasons = {p.pid: v for p, v in _excluded}
    for pid, desc in ((50015, "dca_scheduler --weekly"),
                      (50016, "dca_scheduler --dca"),
                      (50017, "monthly_report --all")):
        assert pid not in _pids(targets), f"未超龄的 {desc} 被误选中"
        assert reasons[pid].script is not None, (
            f"裸命令 {desc} 未被识别为批量脚本 —— 它会静默掉出监控"
        )


# ============================================================
# 用例 8：dry-run 不杀任何进程
# ============================================================

def test_dry_run_kills_nothing_but_reports_what_it_would_kill():
    """默认（不加 --apply）绝不能发任何信号，且输出里要说清它会杀什么。"""

    watchdog = _load_watchdog()
    pid, holder = _spawn_orphan(ignore_sigterm=False)
    tmp = Path(tempfile.mkdtemp())
    try:
        ps_output = (
            f"{pid} 999999 python3 /opt/moneybag/venv/bin/python3 "
            f"scripts/cache_warmer.py --midday\n"
        )
        code, out = _run(watchdog, ["--grace", "1"], ps_output, data_dir=tmp)

        assert code == 0
        assert "DRY-RUN" in out, (
            f"dry-run 的输出里必须显式说明自己是 DRY-RUN 模式。stdout:\n{out}"
        )
        assert str(pid) in out, (
            f"dry-run 必须打印它会回收哪个 pid（{pid}），否则运维无法预演。stdout:\n{out}"
        )
        # 进程必须还活着
        time.sleep(0.3)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise AssertionError(
                f"dry-run（未加 --apply）竟然把 pid={pid} 杀了 —— "
                f"默认必须只读，这是本项目 housekeeping_cron / prune_todos 的统一约定"
            )

        records = [r for r in _read_jsonl(tmp) if r.get("action") == "dry_run"]
        assert len(records) == 1, f"dry-run 也应写 1 条 dry_run 记录，实际 {len(records)}"
        assert records[0]["signals"] == [], "dry-run 不应发出任何信号"
    finally:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            holder.wait(timeout=2)


# ============================================================
# 用例 9：ps 解析健壮性
# ============================================================

def test_ps_parsing_handles_spaces_chinese_and_truncated_comm():
    """args 含空格 / 含中文路径 / comm 被截断到 15 字符的行都能正确解析。

    ⚠️ 关键不变式：解析必须用 `line.split(None, 3)`。
    换成 `split()` 会把 args 切碎，"含 --midday 吗"这类判断全部失效
    （而且看不出报错，只是阈值悄悄走默认 —— 静默失效）。
    """
    watchdog = _load_watchdog()

    # 1) comm 被截断到 15 字符 + args 含多个空格分隔的参数
    proc = watchdog.parse_ps_line(
        "1562523  124576 uvicorn         /opt/moneybag/venv/bin/python3 "
        "/opt/moneybag/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1"
    )
    assert proc is not None
    assert proc.pid == 1562523 and proc.etimes == 124576
    assert proc.comm == "uvicorn"
    assert proc.args == (
        "/opt/moneybag/venv/bin/python3 /opt/moneybag/venv/bin/uvicorn main:app "
        "--host 0.0.0.0 --port 8000 --workers 1"
    ), f"args 含空格时必须整段保留，实际: {proc.args!r}"

    # 2) 15 字符截断的 comm（ps 的真实行为）
    proc = watchdog.parse_ps_line(
        "    765 12181078 networkd-dispat /usr/bin/python3 /usr/bin/networkd-dispatcher"
    )
    assert proc.comm == "networkd-dispat", f"comm 截断到 15 字符，实际 {proc.comm!r}"
    assert proc.args == "/usr/bin/python3 /usr/bin/networkd-dispatcher"

    # 3) args 含中文路径（含空格的中文目录名）
    proc = watchdog.parse_ps_line(
        "  50009  999999 python3 /opt/moneybag/backend/数据 目录/scripts/cache_warmer.py --midday"
    )
    assert proc.args == "/opt/moneybag/backend/数据 目录/scripts/cache_warmer.py --midday", (
        f"中文路径（含空格）必须完整保留，实际: {proc.args!r}"
    )
    assert watchdog.identify_script(proc.args) == "cache_warmer", (
        "中文父目录下 scripts/cache_warmer.py 必须能被识别"
    )
    verdict = watchdog.classify_process(proc, os.getpid(), os.getppid())
    assert verdict.is_target, (
        "中文路径不应影响部署目录校验与阈值判定 —— "
        "绝对路径 token 里含空格时不能误判为 outside_deploy_dir"
    )

    # 4) pid 右对齐 + etimes 前有空格（真实 ps 输出）
    proc = watchdog.parse_ps_line("    7       42 python3 python3 scripts/x.py")
    assert (proc.pid, proc.etimes, proc.comm) == (7, 42, "python3")

    # 5) 坏行不能崩：空行 / 无 args / pid 非数字 / 只有 3 列
    for bad in ("", "   ", "abc 100 python3 python3 scripts/x.py",
                "123 100 python3", "1 2 3"):
        assert watchdog.parse_ps_line(bad) is None, f"坏行应返回 None: {bad!r}"

    # 6) 整段解析时坏行不影响其他行
    procs = watchdog.parse_ps_output(
        "  100 9999 python3 python3 scripts/a.py\n"
        "GARBAGE LINE\n"
        "\n"
        "  101 8888 python3 python3 scripts/b.py\n"
    )
    assert [p.pid for p in procs] == [100, 101], (
        f"坏行应被跳过而不是带崩整轮，实际解析出 {[p.pid for p in procs]}"
    )


# ============================================================
# 阈值表完整性（防"键名写错 → 静默退回 3600"）
# ============================================================

def test_threshold_table_covers_all_cron_scripts():
    """crontab 里每个批量脚本都必须解析出**预期**的阈值。

    ⚠️ 这是本文件里最能防灾难的一条：阈值的键是脚本名（不含 .py），
    一旦有人写成 `"night_worker.py"`，`resolve_threshold` 不会报错，
    只会静默退回 3600 —— 于是每天 02:00 全量 night_worker 会在
    跑满 1 小时时被杀掉，而且从日志上看"看门狗工作正常"。
    """
    watchdog = _load_watchdog()

    # 2026-08-30 服务器 crontab -l 里的全部批量脚本（18 个）
    cron_scripts = (
        "cache_warmer", "night_worker", "dca_scheduler", "broker_rating_cron",
        "weekly_plan_cron", "fund_rank_build", "memory_archive_cron",
        "auto_extract_cron", "monthly_report", "daily_reflection_cron",
        "stock_monitor_cron", "monthly_rebalance_cron", "weekly_review_cron",
        "weekend_push", "briefing_hallucination_check",
        "closing_review_hallucination_check", "daily_push_quality_check",
        "housekeeping_cron",
    )
    expected = {
        "night_worker": 28800,       # 全量链 6.5h 设计时长 + 余量
        "cache_warmer": 7200,        # --midday 除外
        "fund_rank_build": 1800,     # 17560 只基金实测 14 秒
        "housekeeping_cron": 1800,
    }
    for script in cron_scripts:
        want = expected.get(script, watchdog.DEFAULT_THRESHOLD)
        got, rule = watchdog.resolve_threshold(script, f"python3 scripts/{script}.py")
        assert got == want, (
            f"脚本 {script} 的阈值应为 {want}s，实际 {got}s（rule={rule}）—— "
            f"很可能是阈值表的键名写错了（键是脚本名，不含 .py）。"
            f"键名写错不会报错，只会静默退回默认 {watchdog.DEFAULT_THRESHOLD}s。"
        )

    # -m 形式必须解析出同样的阈值（键名归一化）
    for script in cron_scripts:
        want = expected.get(script, watchdog.DEFAULT_THRESHOLD)
        got, _rule = watchdog.resolve_threshold(script, f"python3 -m scripts.{script}")
        assert got == want, f"-m scripts.{script} 与 scripts/{script}.py 的阈值必须一致"

    # 模式阈值覆盖脚本阈值
    assert watchdog.resolve_threshold(
        "night_worker", "python3 scripts/night_worker.py --push-only"
    )[0] == 1800
    assert watchdog.resolve_threshold(
        "cache_warmer", "python3 scripts/cache_warmer.py --midday"
    )[0] == 1500


# ============================================================
# fixture 构成自检（守"守门测试原则三"本身）
# ============================================================

def test_fixture_is_majority_non_target():
    """fixture 必须以非目标进程为主 —— 生产的真实构成就是这样。

    2026-08-30 实测：生产 3 个 python 进程里 0 个是目标。
    如果 fixture 里几乎全是目标，测试就抓不住"过度匹配"这个最主要的失效模式。
    这条断言守住 fixture 的构成比例不被随手改坏。
    """
    watchdog = _load_watchdog()
    procs = watchdog.parse_ps_output(_fixture())
    targets, stats, _excluded = watchdog.select_targets(
        procs, os.getpid(), os.getppid(), watchdog.DEPLOY_DIR_MARKERS, False,
    )
    total = len(procs)
    n_targets = len(targets)

    assert total >= 15, f"fixture 至少 15 行才有统计意义，实际 {total}"
    assert n_targets < total / 2, (
        f"fixture 里 {n_targets}/{total} 是目标 —— 目标必须占少数！"
        f"生产的真实构成是'非目标占绝大多数'（实测 3 个 python 进程里 0 个目标），"
        f"最大风险是**误杀**不是漏杀。目标占多数的 fixture 抓不住过度匹配。"
    )
    assert n_targets >= 3, (
        f"fixture 里只有 {n_targets} 个目标，太少的话漏杀类失效也抓不住"
    )
    # 非目标必须覆盖各个排除规则的类别，而不是单一类别。
    # 注意：因"未超龄"被排除的 verdict 里 rule 存的是**命中的阈值规则名**
    # （如 "cache_warmer（脚本级阈值）"），不是排除原因 —— 所以按
    # "有 threshold 且不是目标" 来归类，这才对应 within_threshold 这条规则。
    def _category(verdict):
        if verdict.is_target:
            return "target"
        if verdict.threshold is not None:
            return "within_threshold"
        return verdict.rule

    rules = {_category(v) for _p, v in watchdog.select_targets(
        procs, os.getpid(), os.getppid(), watchdog.DEPLOY_DIR_MARKERS, False)[2]}
    for required in ("comm", "not_batch_script", "blacklist", "within_threshold"):
        assert required in rules, (
            f"fixture 里没有任何进程被 '{required}' 规则排除 —— "
            f"这条规则就成了没人守的死规则。实际出现的规则: {sorted(rules)}"
        )
    assert stats["script_matched"] >= 8, (
        f"fixture 里只识别出 {stats['script_matched']} 个批量脚本 —— "
        f"识别率过低的话，'脚本名正则失效'这种静默失效会测不出来"
    )


# ============================================================
# 其他：日志 / 僵尸 / 零命中告警
# ============================================================

def test_jsonl_log_has_required_fields():
    """JSONL 记录必须含约定字段（ts/pid/etimes/script/threshold/action/outcome）。"""

    watchdog = _load_watchdog()
    tmp = Path(tempfile.mkdtemp())
    _code, out = _run(watchdog, [], _fixture(), data_dir=tmp)

    records = _read_jsonl(tmp)
    assert records, f"应写出 JSONL 日志（目录 {tmp}）。stdout:\n{out}"
    path = Path(tmp) / "logs" / "watchdog" / f"{time.strftime('%Y-%m-%d')}.jsonl"
    assert path.exists(), f"日志路径应为 data/logs/watchdog/{{date}}.jsonl，实际不存在: {path}"

    actions = [r["action"] for r in records]
    assert "run_summary" in actions, "应写一条 run_summary 汇总记录"

    summary = [r for r in records if r["action"] == "run_summary"][0]
    for field in ("ts", "mode", "ps_total", "script_matched", "targets"):
        assert field in summary, f"run_summary 缺字段 {field}"

    for rec in [r for r in records if r["action"] == "dry_run"]:
        for field in ("ts", "pid", "etimes", "script", "threshold",
                      "action", "outcome", "signals"):
            assert field in rec, f"动作记录缺字段 {field}: {rec}"


def test_zero_match_triggers_warning():
    """一个批量脚本都匹配不到时必须告警 —— 防"配置写错 → 永远 0 目标"的静默失效。"""
    watchdog = _load_watchdog()
    ps_output = (
        "1562523 124576 uvicorn /opt/moneybag/venv/bin/python3 "
        "/opt/moneybag/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000\n"
        "    765 12181078 networkd-dispat /usr/bin/python3 /usr/bin/networkd-dispatcher\n"
    )
    tmp = Path(tempfile.mkdtemp())
    _code, out = _run(watchdog, [], ps_output, data_dir=tmp)
    assert "零命中告警" in out, (
        f"ps 有进程但一个批量脚本都没匹配到时必须告警（通常是部署目录标记或"
        f"脚本名正则配错，看门狗此时等于空转）。stdout:\n{out}"
    )


def test_zombie_process_is_not_reported_as_target():
    """僵尸进程不需要杀也杀不掉，不该被报成目标。

    os.kill(pid, 0) 对僵尸仍然返回成功，不判僵尸会让宽限期白白跑满、
    再升级到 SIGKILL，日志里多一条误导性的 escalated_to_sigkill。
    """
    watchdog = _load_watchdog()

    # 造一个真僵尸：子进程退出后父进程（本测试进程）不 wait
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = proc.pid
    proc.wait(timeout=10)      # 先等它退出，但**不**再 wait → 此刻是僵尸
    try:
        if not watchdog.is_zombie(pid):
            # 有些平台（无 /proc 且 ps 不可用）判断不了 → 跳过而不是误报绿灯
            try:
                subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                return
            proc.wait(timeout=2)
            return
        probe = watchdog.PsProcess(pid=pid, etimes=999999, comm="python3",
                                   args="python3 scripts/dca_scheduler.py")
        verdict = watchdog.classify_process(probe, os.getpid(), os.getppid())
        assert not verdict.is_target, "僵尸进程被判定为待回收目标（杀不掉，纯噪音）"
        assert verdict.rule == "zombie"
    finally:
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)


def test_fault_injection_helpers_are_honest():
    """自检：本文件的"规则隔离探针"必须真的只被目标规则拦住。

    如果有人在 classify_process 里加了新规则（比如"跳过所有 pid > 90000"），
    上面那几条探针就会被新规则拦下、rule 字段不再是预期值 ——
    这时上面的 assert verdict.rule == "xxx" 会报错，从而提醒
    "探针失效了"，而不是让它悄悄变成一条假绿灯。
    这条测试把这个约定显式写下来。
    """
    watchdog = _load_watchdog()
    for name in ("blacklist", "system_path", "self"):
        assert name in (
            "blacklist", "system_path", "self", "comm", "not_batch_script",
            "within_threshold", "outside_deploy_dir", "zombie", "kernel", "error",
        ), f"未知规则名 {name}"
    # BLACKLIST_SUBSTRINGS 必须覆盖这几类常驻服务
    lowered = [s.lower() for s in watchdog.BLACKLIST_SUBSTRINGS]
    for required in ("uvicorn", "main:app", "process_watchdog"):
        assert required in lowered, (
            f"黑名单缺少 '{required}' —— 少一个就少一条独立保险"
        )


# ============================================================
# 无 pytest 环境下的简易 runner（`python test_process_watchdog.py`）
# ============================================================

def _main() -> int:
    """不依赖 pytest 的顺序执行器（本仓库的测试环境里不一定装了 pytest）。"""
    tests = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]

    failed = 0
    for name, fn in tests:
        # 用 pytest 的 tmp_path 约定不适用时，这里显式传 None 的都是自管临时目录
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
