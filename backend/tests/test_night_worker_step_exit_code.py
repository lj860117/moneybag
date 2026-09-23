"""
night_worker `--step` 未知步骤的**退出码**守卫（FIX 2026-09-22）
================================================================

要解决的问题
------------
`scripts/night_worker.py` 的 `__main__` 里，未知步骤原先是这么写的::

    else:
        print(f"未知步骤: {args.step}, 可选: {...}")
        # ← 没有 exit，函数正常返回，进程退出码 0

后果是**静默失败**：cron / 上游调用方看退出码 0 就认为这一步跑成功了，
实际一步都没执行，且没有任何告警。这类失败比崩溃更贵——崩溃至少会留下
非 0 退出码和 traceback，静默成功什么都不留。

修复是 `sys.exit(2)`（2 = 命令行用法错误，与 argparse 自身校验失败的约定
一致）。本文件就是把「非 0」这件事钉住，防止后人把 `sys.exit(2)` 当噪音
删掉、或在重构 `__main__` 时悄悄退回原样。

为什么必须是**真子进程**
------------------------
退出码是**进程级**性质，`runpy` / `import` 都拿不到它（会变成 SystemExit
异常，靠 `pytest.raises(SystemExit)` 也能测，但那时 `sys.exit` 的调用点被
包在 pytest 的异常捕获里，与 cron 真正看到的退出码不是同一条路径）。
cron 看到的是进程退出码，所以这里起真子进程、读 `returncode`。

为什么**只测未知步骤、绝不跑真步骤**
------------------------------------
真实的 `--step xxx` 会拉全量行情、写 DATA_DIR、可能调 LLM 花钱。
本文件用 `--step bogus_...` 这个必然落进 else 分支的值：它是唯一一条
「既能验证退出码、又零副作用」的路径。下面还额外断言了 night_worker 的
日志文件**一个都没生成**——任何一步真跑起来都会先写日志，所以这条断言
能反过来证明我们确实没跑任何步骤。

隔离纪律（别省）
----------------
`night_worker` 在 **import 期**就会建目录：`config` 的 4 个 `*_DIR`
（users/receipts/logs/...）走 `DATA_DIR`，`night_worker` 自己还有
`NIGHT_LOG_DIR = DATA_DIR / "night_worker"` 紧接着 mkdir。子进程若不显式
把 `DATA_DIR` 指到 tmp，这些目录会建进**真实仓库的 data/**。

所以本用例三件事一起做：
  1. 子进程环境里 `DATA_DIR` / `LOG_DIR` / `MONITOR_DIR` 全部指到 tmp_path；
  2. 跑之前给两棵真实数据目录拍快照，跑之后比对——**新增/改写/删除都不许有**
     （断言失败时污染已经发生，所以快照必须在跑之前拍，这是唯一能当场
     发现污染的手段）；
  3. 断言 tmp 下的 DATA_DIR 真的被建出来了——证明隔离生效、用例不是空转。

运行方式::

    cd backend && python -m pytest tests/test_night_worker_step_exit_code.py -q \\
        --basetemp=/tmp/pt_new
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ── 路径常量（全部从 __file__ 推导，不依赖 cwd / PYTHONPATH）────────────────
TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
SCRIPT = BACKEND_DIR / "scripts" / "night_worker.py"

# 受保护的两棵真实数据目录（与 conftest 的 _DEFAULT_GUARD_TREES 一致：
# 权威 DATA_DIR 在仓库根，ops_summary 里的历史遗留目录在 backend 下）
PROTECTED_TREES = (REPO_ROOT / "data", BACKEND_DIR / "data")

# 一个必然落进「未知步骤」分支的名字。带 zzz 是为了一眼看出它是哨兵，
# 也避免哪天真加了个同名步骤让用例静默变成"在跑真步骤"。
BOGUS_STEP = "bogus_step_zzz_not_a_real_step"

_EXPECTED_EXIT_CODE = 2
_TIMEOUT_SEC = 120


def _snapshot(tree: Path) -> dict[str, tuple[int, int]]:
    """给一棵目录树拍快照：相对路径 -> (size, mtime_ns)。

    目录本身也记进快照：`config` 在 import 期 mkdir，只记文件会漏掉
    「多建了一个目录」这种污染。

    Args:
        tree: 目录树根；不存在时返回空字典（该树还没被建出来，也算基线）。

    Returns:
        相对路径 -> (size, mtime_ns) 的映射。
    """
    if not tree.exists():
        return {}
    snap: dict[str, tuple[int, int]] = {}
    for path in tree.rglob("*"):
        if "__pycache__" in path.parts:
            continue  # 解释器产物不是污染，排除掉避免恒红
        try:
            st = path.stat()
        except OSError:
            continue
        snap[str(path.relative_to(tree))] = (st.st_size, st.st_mtime_ns)
    return snap


def _child_env(tmp_path: Path) -> dict[str, str]:
    """构造子进程环境：干净、不继承 DATA_DIR，且**不依赖 conftest 的隔离**。

    刻意继承 PATH/HOME 之外的东西一律不带：本用例要证明的是「night_worker
    在任何环境下都不会把退出码写成 0」，环境越干净，结论越干净。

    Args:
        tmp_path: pytest 提供的临时目录，所有可写路径都指到它下面。

    Returns:
        子进程环境变量字典。
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        # 断言要匹配中文输出「未知步骤」，不锁编码会在别的 locale 下变乱码
        "PYTHONIOENCODING": "utf-8",
        # 别让子进程往 backend/ 里写 .pyc（仓库写入守卫会把它算成污染）
        "PYTHONDONTWRITEBYTECODE": "1",
        # ── 隔离三件套 ──
        # DATA_DIR：config 的 4 个 *_DIR + night_worker 的 NIGHT_LOG_DIR
        #            都在 import 期按它 mkdir，不指到 tmp 就写真实仓库。
        # LOG_DIR / MONITOR_DIR：这两个**不认 DATA_DIR**，各自有默认值
        #   （backend/logs、<repo>/data/monitor），漏掉就会绕过隔离直写仓库。
        "DATA_DIR": str(tmp_path / "data"),
        "LOG_DIR": str(tmp_path / "logs"),
        "MONITOR_DIR": str(tmp_path / "monitor"),
        # 与生产 crontab 一致：cwd=backend + backend/ 可 import。
        # night_worker 自己也会 sys.path.insert(backend)，这里显式带上是为了
        # 让「能否 import 到 config/infra」不依赖脚本内部实现。
        "PYTHONPATH": str(BACKEND_DIR),
    }


def test_unknown_step_exits_nonzero_and_touches_nothing(tmp_path: Path) -> None:
    """`--step <未知>` 必须以非 0 退出，且不得写真实数据目录。

    钉的是 2026-09-22 修掉的那个静默失败：原先未知步骤只 print 一句就
    正常返回，cron 看到退出码 0 以为跑成功了，实际什么都没干。

    故障注入方向（认准这一处，别打错地方）：把 `scripts/night_worker.py`
    `__main__` 里那行 `sys.exit(2)` 删掉，本用例**必须**转红（returncode
    会变回 0）。改别的地方会得到「守卫失效了」的错误结论。

    ⚠️ 只删 `sys.exit(2)` 会先红在 returncode 上；如果只把 `2` 改成 `1`
    也会红——那是有意为之：退出码语义（2 = 用法错误）本身就是契约的一部分。
    """
    assert SCRIPT.is_file(), f"被测脚本不见了: {SCRIPT}"

    before = {str(t): _snapshot(t) for t in PROTECTED_TREES}

    proc = subprocess.run(
        [sys.executable, "scripts/night_worker.py", "--step", BOGUS_STEP],
        cwd=str(BACKEND_DIR),          # 与生产 crontab 的 `cd /opt/moneybag/backend` 对齐
        env=_child_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SEC,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")

    # ── 1. 退出码：这是本用例的主断言 ──
    assert proc.returncode != 0, (
        f"`--step {BOGUS_STEP}` 以 0 退出 —— 静默失败回来了：调用方会以为这一"
        f"步跑成功了，实际一步都没执行。\n  子进程输出:\n{combined[:2000]}"
    )
    assert proc.returncode == _EXPECTED_EXIT_CODE, (
        f"退出码是 {proc.returncode}，期望 {_EXPECTED_EXIT_CODE}（= 命令行用法"
        f"错误，与 argparse 自身校验失败的约定一致）。\n  子进程输出:\n{combined[:2000]}"
    )

    # ── 2. 输出：必须是「走了未知步骤分支」，不是别的非 0 ──
    # 少了这两条，一个 import 期崩溃（rc=1）或 argparse 报错也能让第 1 组
    # 断言蒙混过去 —— 那测的就不是我们要守的东西了。
    assert "未知步骤" in combined, (
        f"输出里没有「未知步骤」，说明没走到目标分支（可能是 import 就崩了，"
        f"那样退出码非 0 也毫无意义）。\n  子进程输出:\n{combined[:2000]}"
    )
    assert BOGUS_STEP in combined, (
        f"输出里没有回显步骤名 {BOGUS_STEP!r}，无法确认命中的是这个分支。\n"
        f"  子进程输出:\n{combined[:2000]}"
    )
    assert "Traceback" not in combined and "ModuleNotFoundError" not in combined, (
        f"子进程是崩掉的，不是正常走到未知步骤分支 —— 这样的退出码非 0 是假阳性，"
        f"不能用来证明守卫有效。\n  子进程输出:\n{combined[:2000]}"
    )

    # ── 3. 确认没跑任何真步骤 ──
    # night_worker 的 log() 每次都会往 NIGHT_LOG_DIR 写 {date}.log，任何一步
    # 真跑起来都必然留下日志。这里一个 .log 都没有 ⇒ 确实一步没跑。
    night_log_dir = tmp_path / "data" / "night_worker"
    assert night_log_dir.is_dir(), (
        f"NIGHT_LOG_DIR 没建出来（{night_log_dir}）—— 说明 DATA_DIR 隔离没生效，"
        f"下面的「没写真实仓库」断言也会跟着失去意义（用例空转）。"
    )
    leftover = sorted(night_log_dir.glob("*.log"))
    assert not leftover, (
        f"子进程写了 {leftover} —— 说明真的执行了某个步骤。本用例只允许走"
        f"「未知步骤」这条零副作用路径。"
    )

    # ── 4. 真实数据目录必须一模一样 ──
    for tree in PROTECTED_TREES:
        after = _snapshot(tree)
        assert after == before[str(tree)], (
            f"子进程污染了真实数据目录 {tree}。\n"
            f"  新增: {sorted(set(after) - set(before[str(tree)]))}\n"
            f"  删除: {sorted(set(before[str(tree)]) - set(after))}\n"
            f"  改写: {sorted(k for k in set(after) & set(before[str(tree)]) if after[k] != before[str(tree)][k])}"
        )

    # ── 5. 反空转：隔离目录必须真的被用到 ──
    # DATA_DIR 指到 tmp 后，config 在 import 期就会把 users/receipts 建出来；
    # 一个都没建说明子进程根本没跑到 import config，上面全是空断言。
    sandboxed = sorted(p.name for p in (tmp_path / "data").iterdir() if p.is_dir())
    assert sandboxed, (
        f"tmp 下的 DATA_DIR（{tmp_path / 'data'}）里一个子目录都没有 —— "
        f"子进程没有真正 import night_worker，本用例会空转。"
    )
