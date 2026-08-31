#!/usr/bin/env python3
"""
钱袋子 — 外部进程看门狗（process_watchdog）
=========================================
用途：找出并回收 **挂死（hung）的批量 cron 进程**。默认 dry-run，加 --apply 才真的杀。

────────────────────────────────────────────────────────────
背景：为什么要一个"外部"看门狗，而不是在脚本里加超时（2026-08-30 排查）
────────────────────────────────────────────────────────────
1) 生产上发现过一个 **卡了 137 天** 的 `cache_warmer --after-close` 进程
   （2026-04-14 22:07 启动，STAT=Sl，卡在 `futex_wait_queue_me`，挂着 19 个 socket）。

2) 讽刺的是，专门修这类问题的 `ak_call()`（daemon thread + `join(timeout)`）在
   2026-06-14 就写好了 —— 比这个进程卡死**晚了 2 个月**，而且全项目**零调用方**，
   于是它又空转了 77 天没人发现。

3) 更关键的连带风险：`ak_call()` 的超时只是"**放弃等待**"，**被卡住的线程不会死**
   （AKShare 内部的阻塞 I/O 不可中断）。在长驻进程里泄漏一次就永久占掉 1 个
   anyio threadpool worker，累积到 40 个（FastAPI 默认池大小）后
   **全站 sync 端点停止服务** —— 包括 `api/portfolio.py` 那批刚加过锁的写入端点。

   👉 所以原则是：**接线（给每个 AKShare 调用加超时）只降低概率，兜底才限制后果。**

4) 为什么是**外部**看门狗而不是进程内自杀：
   - 外部机制能覆盖 `ak_call()` **之外**的所有挂死路径（网络 hang、死锁、
     `time.sleep` 写错、flock 拿不到、第三方库内部死循环……）；
   - 并且**自动覆盖将来新增的、忘了加自杀逻辑的脚本** —— 进程内方案每次新脚本
     都要靠人记得接，而"人记得"恰恰是本项目已经翻车过一次的东西（见第 2 点）。

────────────────────────────────────────────────────────────
安全设计（每一条都对应一个真实失效模式，不要删）
────────────────────────────────────────────────────────────
🔴 **1. 绝不杀 uvicorn —— 后果最严重的失效模式。**
   生产上 uvicorn 常驻 124576 秒（34 小时）是**设计如此**，杀了等于每 10 分钟
   把线上服务打掉一次。用 **5 条互相独立**的判据排除，任何一条单独生效就够：
     ① comm 必须以 `python` 开头（顺带排除 cron 的 `bash -c` 包装进程 ——
        它的 args 里也含脚本名，但 comm 是 `bash`；而且杀 shell 杀不掉子进程，
        只会留下孤儿 python，纯属噪音）
     ② argv[0]（也就是**解释器**那一个 token）若带绝对路径，必须落在部署目录内
        （默认 `/opt/moneybag/`），且不在 `/usr/bin`、`/usr/share` 等系统路径下
        → 排除 `networkd-dispatcher` / `unattended-upgrade-shutdown` 这类系统 python
        ⚠️ **只校验 argv[0]，绝不校验后面的参数**，原因见下面"关于判据 ②"那节
     ③ args 必须匹配 `scripts/<name>.py` 或 `-m scripts.<name>` 的批量脚本模式
        → uvicorn 的 args 是 `uvicorn main:app`，天然不匹配
     ④ 显式黑名单：args 含 `uvicorn` / `main:app` / `gunicorn` 直接跳过
     ⑤ 排除看门狗自己的 pid 和父 pid（以及 pid <= 1）

⚠️⚠️ **排班表的权威来源是服务器 `crontab -l`，不是 `scripts/setup_cron.sh`。**
   `setup_cron.sh` 已与线上**漂移**（2026-08-31 核对）：
     - 缺 5 个脚本：`closing_review_hallucination_check` / `daily_push_quality_check`
       / `fund_rank_build` / `housekeeping_cron` / `monthly_rebalance_cron`
     - 2 处频率不一致：`--midday` 它写 `5 13 * * 1-5`（日频），线上是 `*/30 9-14 * * 1-5`；
       `stock_monitor_cron` 它写 `*/10`（盘中盯盘），线上那两行是 `# DISABLED`
   👉 这个文件是"服务器迁移后重建 cron"会跑的东西，**按它重建会直接装出一套
      缺 5 个脚本、频率也不对的错误排班**。而本文件的阈值表正确性**依赖真实排班**
      —— 改阈值前请先 `crontab -l` 核对，别信 setup_cron.sh。

⚠️ 关于判据 ② 的两处**刻意设计**（都与最初的设计不同，改动前请先读完）：

   **(a) 裸命令名直接放行 —— 否则会漏掉线上 4 个脚本。**
   线上 29 条生效 crontab 是**混合形态**（2026-08-31 服务器实测）：
       21 条  `/opt/moneybag/venv/bin/python scripts/X.py`   ← 绝对路径，能校验
        4 条  `-m scripts.X`                                  ← 模块形式
        4 条  裸 `python3 scripts/X.py`                       ← ⚠️ 无路径可校验
   那 4 条裸命令是 `dca_scheduler.py --discipline` / `--weekly` / `--dca`
   和 `monthly_report.py --all`，它们用
   `source /opt/moneybag/venv/bin/activate && ... && python3 scripts/X.py`
   起进程 —— `venv` 路径只出现在 shell 的 `activate` 里，**不进 argv**，
   ps 里看到的就是 `python3 scripts/dca_scheduler.py --discipline`。
   若把 ② 做成"args 必须含 `/opt/moneybag/`"，这 4 个脚本会**静默掉出监控**
   （漏掉约 16%）。所以：argv[0] **不带**绝对路径（裸 `python3`）时直接放行。

   **(b) 只校验 argv[0]，绝不校验后面的参数 —— 否则会埋一个更难查的雷。**
   参数是**数据**、不是**身份**：区分"我们的 venv python"和"系统 python"
   靠的只有 argv[0]，参数不提供这个信息。
   反例（真实存在的一类写法）：
       /opt/moneybag/venv/bin/python scripts/cache_warmer.py --out /tmp/warm.json
   如果 ② 遍历 args 里**每一个**以 `/` 开头的 token，`/tmp/warm.json` 会被当成
   "不在部署目录" → 该进程被判 `outside_deploy_dir` 而**放行**。
   这种漏判比 (a) 更隐蔽：它连 `threshold` 都不会有（不是"监控中但未超龄"，
   而是**完全不可见**），而且下面的"零命中自检"也抓不到 ——
   自检只在**所有**脚本都零命中时才报警，单个脚本悄悄掉出去时其余脚本照常有命中，
   自检一片祥和。
   👉 守门：`tests/test_process_watchdog.py::test_absolute_path_argument_does_not_hide_a_script`

   （`--skip-deploy-check` 可整体关闭判据 ②，仅排障时用。）

⚠️ **2. 阈值按脚本分别配置，不能一刀切。**
   `night_worker.py` 全量链设计上就跨 01:00→07:30 = 6.5 小时（内部串跑 10 个阶段），
   一刀切"超过 1 小时就杀"会**每天夜里误杀**它。
   而 `cache_warmer.py --midday` 每 30 分钟起一次，阈值必须 **< 30 分钟**，
   否则挂死的实例会叠加。详见下面的 `SCRIPT_THRESHOLDS` / `MODE_THRESHOLDS`。

⚠️ **3. 终止顺序必须是 SIGTERM → 宽限期 → SIGKILL，绝不直接 KILL。**
   理由**不是**清垃圾（孤儿 `.tmp` 已由 `housekeeping_cron.py` 兜住，>1 天才清），
   而是 `SIGTERM` 能走到 `finally`，让进程**正常收尾当前那一次写入**。
   垃圾可以扫，但"这一轮缓存到底写完没有"只有进程自己知道。
   `cache_warmer` 单次落盘是毫秒级，10 秒宽限足够。
   宽限期内**轮询**进程是否已退出，退出就不再升级。

⚠️ **4. 默认 dry-run。** 与 `housekeeping_cron.py` / `prune_todos.py` 统一约定，
   必须显式加 `--apply` 才真的杀。

⚠️ **5. 每类处理独立 try/except**，一个进程处理失败不影响其他进程。

⚠️ **6. 零命中自检。** 如果一整轮下来 1 个批量脚本都没匹配到（不是"没超龄"，
   而是"压根没识别出任何批量脚本"），会打出醒目告警 —— 这通常意味着
   部署目录标记或脚本名正则配错了，也就是上面第 1 条说的静默失效。

────────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────────
    cd /opt/moneybag/backend

    # 只看（默认 dry-run，安全）
    /opt/moneybag/venv/bin/python3 scripts/process_watchdog.py

    # 真的杀
    /opt/moneybag/venv/bin/python3 scripts/process_watchdog.py --apply

    # 看被排除的进程及原因（排查"为什么它没被选中"时很有用）
    /opt/moneybag/venv/bin/python3 scripts/process_watchdog.py --show-excluded

建议 crontab（每 5 分钟；为什么不是 10 分钟见文件末尾注释）：
    */5 * * * * cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python3 scripts/process_watchdog.py --apply >> /var/log/moneybag/watchdog.log 2>&1

────────────────────────────────────────────────────────────
日志
────────────────────────────────────────────────────────────
每次动作写结构化 JSONL 到 `DATA_DIR/logs/watchdog/{YYYY-MM-DD}.jsonl`，
字段：ts / pid / etimes / comm / script / args / threshold / rule / grace /
action / signals / outcome / detail。另有一条 `action=run_summary` 的汇总记录。

ℹ️ 这个文件名**含日期**且后缀是 `.jsonl`，会命中 `housekeeping_cron.py` 的归档清理
判据，**90 天后被自动清掉 —— 这是期望行为**（运维遥测保留 90 天足够），不是 bug，
请勿为此去改 housekeeping_cron 的白名单。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# 让脚本能 import config（与 scripts/ 下其他脚本一致）
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from config import DATA_DIR  # noqa: E402


# ============================================================
# 配置区
# ============================================================

#: `ps` 命令。列顺序固定为 pid,etimes,comm,args（comm 无空格，args 放最后以便整段保留）。
#: ⚠️ 不要加 `stat` 列：解析用的是 `line.split(None, 3)`，多一列就会把 stat 当成 comm。
PS_COMMAND: tuple = ("ps", "-eo", "pid,etimes,comm,args", "--no-headers")

#: ps 调用超时（秒）
PS_TIMEOUT: float = 20.0

#: SIGTERM 之后的宽限期（秒）。cache_warmer 单次落盘毫秒级，10 秒足够走到 finally。
DEFAULT_GRACE: float = 10.0

#: 宽限期内轮询进程是否已退出的间隔（秒）
POLL_INTERVAL: float = 0.2

#: SIGKILL 之后确认进程消失的等待上限（秒）
KILL_CONFIRM_TIMEOUT: float = 5.0

# ------------------------------------------------------------
# 🔴 阈值表（秒）—— 必须与"每个脚本的设计运行时长"对齐，否则会误杀
# ------------------------------------------------------------

#: 默认阈值：未在下面两张表里显式列出的批量脚本，一律 1 小时。
#: 1 小时对"发个推送 / 生成个日报"这类脚本已经非常宽松。
DEFAULT_THRESHOLD: int = 3600

#: 按 **脚本名** 的阈值。键是脚本名（不含 `.py`），
#: 同时覆盖 `scripts/<name>.py` 和 `-m scripts.<name>` 两种调用形式。
#: ⚠️ 键名写错不会报错，只会静默退回 DEFAULT_THRESHOLD —— 对 night_worker 来说
#:    就是从 8h 掉到 1h，**每天夜里误杀一次全量链**。
#:    守门：`tests/test_process_watchdog.py::test_threshold_table_covers_all_cron_scripts`
SCRIPT_THRESHOLDS: dict = {
    # 全量链设计上跨 01:00→07:30 = 6.5 小时（内部串跑 10 个阶段）。
    # 8 小时 = 6.5h 设计时长 + 1.5h 余量（覆盖 AKShare 偶发慢速 + 重试）。
    # 绝不能按"超过 1 小时就杀"的一刀切来，那会每天夜里误杀它。
    "night_worker": 28800,
    # --midday 之外的所有预热模式（--morning / --after-close / --harvest /
    # --weekend / --full-extra / --nav-confirmed / --evening / --all）。
    # 这些是小时级或半小时级才跑一次，2 小时足够宽松。
    "cache_warmer": 7200,
    # 实测 17560 只基金 14 秒跑完，1800 秒（30 分钟）已经是 128 倍余量。
    "fund_rank_build": 1800,
    # 磁盘清理 + 归档扫描，分钟级；与它自己的 90 天归档策略无关。
    "housekeeping_cron": 1800,
    #
    # ⚠️ stock_monitor_cron **刻意不在这里**，走默认的 3600（1 小时）。
    #    它保持 3600 的前提是"每天只跑一次"：线上 crontab 目前只有
    #    `0 21 * * 1-5 ... stock_monitor_cron.py --close`，
    #    盘中的 `*/10 9-11`、`*/10 13-14` 两条当前是 **# DISABLED** 状态。
    #    🔴 若将来重新启用那两条 `*/10`，必须把 stock_monitor_cron 降到 **600**
    #       并加进本表 —— 否则挂死时会像 --midday 那样叠加实例（10 分钟一次
    #       的调度配 1 小时阈值，最坏叠 6 个）。
    #    注意：scripts/setup_cron.sh 里那个 `*/10` 是**历史残留**，别照它改。
}

#: 按 **脚本 + 模式** 的阈值。**更具体的模式必须优先匹配**，否则全量 night_worker
#: 会错配到 --push-only 的小阈值上（反之亦然）。
#: 元素：(脚本名, 模式标志, 阈值秒, 依据注释)
MODE_THRESHOLDS: tuple = (
    # 只推送简报（08:30），读文件 + 发 HTTP，设计上是秒级/分钟级。
    ("night_worker", "--push-only", 1800,
     "只推送简报，读盘 + 发 HTTP，分钟级；不能和全量链共用 28800"),
    # 午间预热每 30 分钟起一次（cron */30），阈值必须 < 30 分钟，
    # 否则挂死实例会在下一次启动前还活着，一天叠出十几个。
    ("cache_warmer", "--midday", 1500,
     "每 30 分钟起一次，25 分钟阈值确保在下一次启动前被回收"),
)

#: 🔴 黑名单关键字：args 命中任意一个就**无条件跳过**。
#: 这是"绝不杀 uvicorn"的最后一道且**独立于其他判据**的保险。
BLACKLIST_SUBSTRINGS: tuple = (
    "uvicorn",        # 线上 Web 服务，常驻 34 小时是设计如此
    "main:app",       # uvicorn main:app 的另一种写法
    "gunicorn",       # 万一将来换 WSGI server
    "hypercorn",
    "process_watchdog",  # 看门狗自己（含 scripts/process_watchdog.py 与 -m 形式）
)

#: 系统路径前缀：解释器或脚本落在这些路径下的，一定是系统进程，不碰。
SYSTEM_PATH_PREFIXES: tuple = (
    "/usr/bin/", "/usr/sbin/", "/usr/share/", "/usr/lib/",
    "/bin/", "/sbin/", "/snap/", "/lib/",
)

#: 部署目录标记。args 里若出现绝对路径，必须落在其中之一才算"我们的进程"。
DEPLOY_DIR_MARKERS: tuple = ("/opt/moneybag/",)

# ------------------------------------------------------------
# 识别用的正则
# ------------------------------------------------------------

#: `scripts/cache_warmer.py` / `/opt/moneybag/backend/scripts/cache_warmer.py`
SCRIPT_PATH_RE = re.compile(r"scripts[/\\]([A-Za-z_][A-Za-z0-9_]*)\.py(?=\s|$)")

#: `-m scripts.housekeeping_cron`
SCRIPT_MODULE_RE = re.compile(r"(?<![\w.])scripts\.([A-Za-z_][A-Za-z0-9_]*)(?=\s|$)")


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PsProcess:
    """一条 `ps` 记录。"""
    pid: int
    etimes: int          # 已运行秒数
    comm: str            # 进程名（ps 会截断到 15 字符）
    args: str            # 完整命令行（含空格）


@dataclass
class Verdict:
    """对某个进程的判定结果。"""
    is_target: bool = False
    script: Optional[str] = None
    threshold: Optional[int] = None
    rule: str = ""       # 命中的阈值规则（日志/排障用）
    reason: str = ""     # 为什么选中 / 为什么排除


@dataclass
class Target:
    """一个待回收的目标进程。"""
    proc: PsProcess
    script: str
    threshold: int
    rule: str
    reason: str


# ============================================================
# ps 枚举与解析
# ============================================================

def run_ps(ps_command: Optional[tuple] = None) -> str:
    """
    执行 `ps` 并返回原始 stdout。

    单独抽成函数的目的：这是全模块**唯一**依赖外部 `ps` 的地方，
    测试可以整体替换它（fixture 注入），从而在没有 ps 权限的环境里也能跑。

    Args:
        ps_command: 覆盖默认 PS_COMMAND（测试用）

    Returns:
        ps 的原始输出

    Raises:
        RuntimeError: ps 执行失败或超时
    """
    cmd = list(ps_command if ps_command is not None else PS_COMMAND)
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"执行 {' '.join(cmd)} 失败: {e}") from e
    if completed.returncode != 0:
        raise RuntimeError(
            f"ps 返回码 {completed.returncode}: "
            f"{(completed.stderr or '').strip()[:300]}"
        )
    return completed.stdout


def parse_ps_line(line: str) -> Optional[PsProcess]:
    """
    解析单行 `ps -eo pid,etimes,comm,args --no-headers` 输出。

    ⚠️ 必须用 `split(None, 3)`：
      - `comm` 本身不含空格，但它会被 ps **截断到 15 字符**；
      - `args` **含空格且必须整段保留**（里面有 `--option value`、中文路径等），
        用 `split()` 会把它切碎，导致"含 --midday 吗"这类判断全部失效。

    Args:
        line: ps 输出的一行

    Returns:
        PsProcess；无法解析（空行 / pid 非数字）时返回 None
    """
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split(None, 3)
    if len(parts) < 4:
        return None                      # 缺列（异常行），保守跳过
    pid_s, etimes_s, comm, args = parts
    try:
        pid = int(pid_s)
        etimes = int(etimes_s)
    except ValueError:
        return None                      # 标题行 / 内核线程等
    return PsProcess(pid=pid, etimes=etimes, comm=comm, args=args)


def parse_ps_output(raw: str) -> list:
    """解析整段 ps 输出，逐行独立 try/except（坏行不影响其他行）。"""
    procs: list = []
    for line in raw.splitlines():
        try:
            proc = parse_ps_line(line)
        except Exception:                # 绝不让一行坏数据带崩整轮
            continue
        if proc is not None:
            procs.append(proc)
    return procs


# ============================================================
# 阈值与识别
# ============================================================

def identify_script(args: str) -> Optional[str]:
    """
    从命令行里识别批量脚本名（不含 `.py`）。

    同时支持两种生产调用形式：
        python3 scripts/cache_warmer.py --midday     → "cache_warmer"
        python3 -m scripts.housekeeping_cron --apply → "housekeeping_cron"

    Args:
        args: 完整命令行

    Returns:
        脚本名；不是批量脚本时返回 None
    """
    match = SCRIPT_MODULE_RE.search(args)
    if match:
        return match.group(1)
    match = SCRIPT_PATH_RE.search(args)
    if match:
        return match.group(1)
    return None


def _args_has_flag(args: str, flag: str) -> bool:
    """判断命令行是否含某个模式标志（整词匹配，避免 `--midday` 命中 `--midday-x`）。"""
    pattern = r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])"
    return re.search(pattern, args) is not None


def resolve_threshold(script: str, args: str) -> tuple:
    """
    决定某个进程的超时阈值（秒）。

    匹配顺序（**更具体的模式必须优先**）：
        1. `MODE_THRESHOLDS`（脚本 + 模式）
        2. `SCRIPT_THRESHOLDS`（脚本名）
        3. `DEFAULT_THRESHOLD`

    Args:
        script: identify_script() 返回的脚本名
        args: 完整命令行

    Returns:
        (阈值秒, 规则说明)
    """
    for name, flag, seconds, _note in MODE_THRESHOLDS:
        if script == name and _args_has_flag(args, flag):
            return seconds, f"{name} + {flag}"
    if script in SCRIPT_THRESHOLDS:
        return SCRIPT_THRESHOLDS[script], f"{script}（脚本级阈值）"
    return DEFAULT_THRESHOLD, f"默认阈值（{script} 未显式配置）"


# ============================================================
# 进程存活 / 僵尸判定
# ============================================================

def _ps_stat_is_zombie(pid: int) -> bool:
    """非 Linux（无 /proc）时用 `ps -o stat=` 判断僵尸。不可用则保守返回 False。"""
    try:
        completed = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True, timeout=PS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False                     # ps 不可用 → 保守当作"不是僵尸"
    if completed.returncode != 0:
        return False
    return completed.stdout.strip().upper().startswith("Z")


def is_zombie(pid: int) -> bool:
    """
    判断进程是否已是僵尸（已退出但父进程尚未 wait()）。

    僵尸**不需要杀也杀不掉**；更重要的是 `os.kill(pid, 0)` 对僵尸仍然返回成功，
    不判僵尸就会让宽限期白白跑满、然后升级到 SIGKILL，最后日志里多一条
    误导性的 "escalated_to_sigkill"（其实进程早就死了）。

    Args:
        pid: 进程号

    Returns:
        True = 是僵尸
    """
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():               # Linux：权威且零开销
        try:
            raw = stat_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        # comm 字段可能含空格和括号 → 取最后一个 ')' 之后的第一个字段才是 state
        after_comm = raw.rpartition(")")[2].split()
        return bool(after_comm) and after_comm[0] == "Z"
    return _ps_stat_is_zombie(pid)       # macOS 等：退化为一次 ps 调用


def is_alive(pid: int) -> bool:
    """进程是否还活着（僵尸不算活着）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False                     # 已退出且已被回收
    except PermissionError:
        return True                      # 别人的进程：存在但无权发信号 → 算活着
    return not is_zombie(pid)


# ============================================================
# 目标筛选
# ============================================================

def classify_process(
    proc: PsProcess,
    own_pid: int,
    parent_pid: int,
    deploy_markers: tuple = DEPLOY_DIR_MARKERS,
    skip_deploy_check: bool = False,
) -> Verdict:
    """
    判定单个进程是否是"超龄的批量 cron 进程"。

    排除规则（**每一条都能独立拦住 uvicorn**，刻意冗余）：
      ① 黑名单关键字（uvicorn / main:app / gunicorn / 看门狗自己……）
      ② comm 必须以 `python` 开头（排除 cron 的 bash/sh 包装进程）
      ③ 不能是自己 / 父进程 / pid <= 1
      ④ args 必须匹配批量脚本模式（`scripts/<name>.py` 或 `-m scripts.<name>`）
      ⑤ args 里的绝对路径必须落在部署目录，且不在系统路径下
      ⑥ 僵尸进程跳过
      ⑦ 已运行秒数必须 **超过** 阈值

    Args:
        proc: 待判定的 ps 记录
        own_pid: 看门狗自己的 pid
        parent_pid: 看门狗的父进程 pid
        deploy_markers: 部署目录标记
        skip_deploy_check: True = 跳过规则 ⑤（`--skip-deploy-check`）

    Returns:
        Verdict
    """
    lowered = proc.args.lower()

    # ① 黑名单 —— 放在最前面，最便宜也最关键
    for bad in BLACKLIST_SUBSTRINGS:
        if bad in lowered:
            return Verdict(
                rule="blacklist",
                reason=f"args 含黑名单关键字 '{bad}'（常驻服务或看门狗自己，绝不能杀）",
            )

    # ② comm 必须是 python —— 排除 cron 的 `bash -c` / `sh -c` 包装进程
    if not proc.comm.lower().startswith("python"):
        return Verdict(
            rule="comm",
            reason=f"comm='{proc.comm}' 不是 python 解释器"
                   f"（内核线程 / 系统守护进程 / cron 的 shell 包装进程都不处理；"
                   f"杀 shell 也杀不掉子进程，只会留下孤儿 python）",
        )

    # ③ 自己 / 父进程 / 内核进程
    if proc.pid <= 1:
        return Verdict(rule="kernel", reason=f"pid={proc.pid} 是内核/Init 进程，绝不碰")
    if proc.pid in (own_pid, parent_pid):
        return Verdict(
            rule="self",
            reason=f"pid={proc.pid} 是看门狗自己（{own_pid}）或其父进程（{parent_pid}）",
        )

    # ④ 必须是批量脚本
    script = identify_script(proc.args)
    if script is None:
        return Verdict(
            rule="not_batch_script",
            reason="args 既不匹配 scripts/<name>.py 也不匹配 -m scripts.<name>"
                   "（uvicorn / 系统服务 / 一次性命令都在这一步被排除）",
        )

    # ⑤ 只校验 argv[0]（解释器）—— 绝不校验后面的参数
    #
    # ⚠️ 这里曾经是"遍历 args 里每一个以 / 开头的 token"，那是个 bug：
    #    只要脚本接一个绝对路径参数（如 `--out /tmp/warm.json`），
    #    它就会被判成 outside_deploy_dir 而**静默掉出看门狗覆盖**，
    #    且 threshold=None（连"监控中但未超龄"都不算），自检也抓不到。
    #    参数是数据、不是身份：区分"我们的 venv python"和"系统 python"
    #    只需要 argv[0]。系统进程（/usr/bin/python3 ...）照样在这一步被拦下。
    if not skip_deploy_check:
        tokens = proc.args.split()
        argv0 = tokens[0] if tokens else ""      # 裸命令名（cron 里的 `python3`）→ 放行
        if argv0.startswith("/"):
            if argv0.startswith(SYSTEM_PATH_PREFIXES):
                return Verdict(
                    rule="system_path",
                    reason=f"解释器 '{argv0}' 位于系统目录（系统 python 进程，不是我们的 cron）",
                )
            if not any(argv0.startswith(marker) for marker in deploy_markers):
                return Verdict(
                    rule="outside_deploy_dir",
                    reason=f"解释器 '{argv0}' 不在部署目录 {list(deploy_markers)} 内"
                           f"（可能是开发机上的同名进程）",
                )

    # ⑥ 僵尸
    if is_zombie(proc.pid):
        return Verdict(rule="zombie", reason="进程已是僵尸（已退出，父进程未回收），无需处理")

    # ⑦ 超龄
    threshold, rule = resolve_threshold(script, proc.args)
    if proc.etimes <= threshold:
        return Verdict(
            script=script, threshold=threshold, rule=rule,
            reason=f"已运行 {proc.etimes}s ≤ 阈值 {threshold}s（{rule}）",
        )
    return Verdict(
        is_target=True, script=script, threshold=threshold, rule=rule,
        reason=f"已运行 {proc.etimes}s > 阈值 {threshold}s（{rule}）",
    )


def select_targets(
    procs: list,
    own_pid: Optional[int] = None,
    parent_pid: Optional[int] = None,
    deploy_markers: tuple = DEPLOY_DIR_MARKERS,
    skip_deploy_check: bool = False,
) -> tuple:
    """
    筛选超龄目标进程。每个进程独立 try/except，一个出错不影响其他。

    Args:
        procs: parse_ps_output() 的结果
        own_pid: 看门狗自己的 pid（默认 os.getpid()）
        parent_pid: 父进程 pid（默认 os.getppid()）
        deploy_markers: 部署目录标记
        skip_deploy_check: 是否跳过部署目录校验

    Returns:
        (targets, stats, excluded)
          targets: [Target, ...]
          stats:   {"total", "script_matched", "targets", "errors"}
          excluded: [(PsProcess, Verdict), ...]  -- 被排除的进程及原因
    """
    if own_pid is None:
        own_pid = os.getpid()
    if parent_pid is None:
        parent_pid = os.getppid()

    targets: list = []
    excluded: list = []
    stats = {"total": len(procs), "script_matched": 0, "targets": 0, "errors": 0}

    for proc in procs:
        try:
            verdict = classify_process(
                proc, own_pid, parent_pid, deploy_markers, skip_deploy_check,
            )
        except Exception as e:            # 单个进程判定失败 → 记下来继续
            stats["errors"] += 1
            excluded.append((proc, Verdict(rule="error", reason=f"判定异常: {e}")))
            continue

        if verdict.script is not None:
            stats["script_matched"] += 1
        if verdict.is_target:
            stats["targets"] += 1
            targets.append(
                Target(proc=proc, script=verdict.script or "?",
                       threshold=verdict.threshold or 0,
                       rule=verdict.rule, reason=verdict.reason)
            )
        else:
            excluded.append((proc, verdict))

    # 已运行时间长的排前面 —— 最该先回收的先处理
    targets.sort(key=lambda t: t.proc.etimes, reverse=True)
    return targets, stats, excluded


# ============================================================
# 终止
# ============================================================

def terminate_process(
    pid: int,
    grace: float = DEFAULT_GRACE,
    apply_changes: bool = False,
) -> dict:
    """
    SIGTERM → 宽限期轮询 → SIGKILL。

    ⚠️ **绝不直接 SIGKILL**：SIGTERM 能让进程走到 `finally`，正常收尾当前那一次写入。
    垃圾孤儿文件有 `housekeeping_cron.py` 兜底（>1 天才清），
    但"这一轮缓存到底写完没有"只有进程自己知道。

    Args:
        pid: 目标进程号
        grace: SIGTERM 之后的宽限秒数
        apply_changes: False = dry-run，不发任何信号

    Returns:
        {"signals": [...], "outcome": str, "detail": str}
        outcome 取值：
            dry_run                    -- 未动手（默认模式）
            terminated                 -- SIGTERM 生效，宽限期内退出
            escalated_to_sigkill       -- SIGTERM 被忽略，升级到 SIGKILL 后死亡
            sigkill_sent_unconfirmed   -- SIGKILL 已发但确认期内仍未消失（罕见）
            already_gone               -- 发信号前就已退出（竞态）
            failed                     -- 权限不足等
    """
    result: dict = {"signals": [], "outcome": "dry_run", "detail": ""}

    if not apply_changes:
        return result

    # ── 1) SIGTERM ──
    try:
        os.kill(pid, signal.SIGTERM)
        result["signals"].append("SIGTERM")
    except ProcessLookupError:
        result["outcome"] = "already_gone"
        result["detail"] = "发 SIGTERM 前进程已退出（竞态，无需处理）"
        return result
    except PermissionError as e:
        result["outcome"] = "failed"
        result["detail"] = f"无权限发 SIGTERM: {e}"
        return result

    # ── 2) 宽限期内轮询：退出就不再升级 ──
    deadline = time.monotonic() + max(0.0, grace)
    while True:
        if not is_alive(pid):
            result["outcome"] = "terminated"
            result["detail"] = f"SIGTERM 后 {grace}s 宽限期内正常退出"
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL, remaining))

    # ── 3) SIGKILL（仅在宽限期跑满后）──
    try:
        os.kill(pid, signal.SIGKILL)
        result["signals"].append("SIGKILL")
    except ProcessLookupError:
        result["outcome"] = "terminated"
        result["detail"] = "宽限期刚满时进程自行退出（SIGKILL 未发）"
        return result
    except PermissionError as e:
        result["outcome"] = "failed"
        result["detail"] = f"无权限发 SIGKILL: {e}"
        return result

    confirm_deadline = time.monotonic() + KILL_CONFIRM_TIMEOUT
    while True:
        if not is_alive(pid):
            result["outcome"] = "escalated_to_sigkill"
            result["detail"] = (f"SIGTERM 在 {grace}s 宽限期内未生效"
                                f"（挂死或被显式忽略），已升级到 SIGKILL")
            return result
        if time.monotonic() >= confirm_deadline:
            result["outcome"] = "sigkill_sent_unconfirmed"
            result["detail"] = ("SIGKILL 已发送但确认期内进程仍未消失"
                                "（多为僵尸或不可中断的 D 状态）")
            return result
        time.sleep(POLL_INTERVAL)


# ============================================================
# 零命中自检（带状态，防告警疲劳）
# ============================================================

#: 连续这么久没见过任何批量脚本进程才告警（秒）。
#: 依据线上排班：night_worker 全量链每天 01:00 起、跑 6.5 小时（01:00→07:30），
#: 盘中 cache_warmer --midday 每 30 分钟一次（09:00-14:59），收盘后还有多个
#: cache_warmer 模式。**正常排班下 48 小时不出现任何批量脚本是不可能的**，
#: 所以真到了这个点，基本可以断定是识别逻辑坏了。
ZERO_HIT_ALERT_AFTER: int = 48 * 3600

#: 记录"最后一次见到批量脚本进程"的状态文件名。
#: 点开头 + 不含日期 → 不会被 housekeeping_cron.py 命中：
#: 归档清理要求名字含 YYYY-MM-DD，孤儿 .tmp 清理只收 *.tmp。
LAST_BATCH_SEEN_FILE: str = ".last_batch_seen"


def _last_batch_seen_path(log_dir: Path) -> Path:
    return Path(log_dir) / LAST_BATCH_SEEN_FILE


def _update_last_batch_seen(script_matched: int, log_dir: Path) -> None:
    """见到批量脚本进程就刷新状态文件的时间戳。

    写失败刻意只打一行警告、不中断：它只是遥测，不值得让看门狗整体失败。
    """
    if script_matched <= 0:
        return
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        _last_batch_seen_path(log_dir).write_text(
            datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError as e:
        print(f"   ⚠️ 无法更新 {LAST_BATCH_SEEN_FILE}：{e}（仅影响零命中自检，不影响回收）")


def _report_zero_hit_if_stale(stats: dict, log_dir: Path) -> None:
    """
    零命中自检。

    ⚠️ 刻意**不是**"本次零命中就报警"。批量脚本只在 cron 触发的窗口里存在，
    两次 cron 之间本来就是 0 个（实测 00:34 跑，109 个进程里 0 个批量脚本，
    完全正常）。若每次都报警，*/5 的调度一天要喊 288 次 ——
    **告警疲劳比没有告警更糟**，运维会直接忽略这个日志，那自检就白写了。

    所以改成看"最后一次见到批量脚本是多久以前"，超过 ZERO_HIT_ALERT_AFTER 才告警。
    这样既能抓住真正的静默失效（识别逻辑坏了 → 永远见不到），
    又不会在正常的空窗期乱喊。
    """
    if stats["script_matched"] > 0:
        return

    hours = ZERO_HIT_ALERT_AFTER // 3600
    path = _last_batch_seen_path(log_dir)

    if not path.exists():
        print(f"\nℹ️  本次 {stats['total']} 个进程里 0 个批量脚本（两次 cron 之间属正常）。")
        print(f"   尚无「见到过批量脚本」的记录，暂不告警；"
              f"连续 {hours} 小时见不到才会告警。")
        return

    try:
        last = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return

    gap_h = (datetime.now() - last).total_seconds() / 3600
    if gap_h * 3600 >= ZERO_HIT_ALERT_AFTER:
        print("\n🚨🚨 零命中告警 🚨🚨")
        print(f"   已连续 {gap_h:.1f} 小时（阈值 {hours}h）没见过任何批量脚本进程。")
        print(f"   ps 返回 {stats['total']} 个进程。按排班，night_worker 每天 01:00 起跑 6.5 小时，"
              f"不可能这么久不出现。")
        print("   多半是配置问题（部署目录标记 / 脚本名正则 / ps 列格式变了），")
        print("   看门狗此时等于空转 —— 请用 --show-excluded 排查，不要放着不管。")
    else:
        print(f"\nℹ️  本次 0 个批量脚本（正常，两次 cron 之间本就为空）。"
              f"上次见到是 {gap_h:.1f} 小时前（告警阈值 {hours}h）。")


def _is_worth_showing(proc) -> bool:
    """
    `--show-excluded` 的过滤判据。

    实测服务器上 ps 返回 109 条，绝大多数是 kthreadd / ksoftirqd 这类内核线程
    —— 全列出来会淹掉真正要看的（uvicorn、系统 python、看门狗自己），
    排障时反而更难用，那一屏噪音里找不到 uvicorn 是否被正确排除。

    只保留"有可能被误判成我们的进程"的那些：python 进程，或 args 里出现
    部署标记 / `scripts/` 的（cron 的 shell 包装进程属于后一类，必须能看到）。
    """
    lowered = proc.args.lower()
    return (
        proc.comm.lower().startswith("python")
        or "scripts/" in lowered
        or any(marker.lower() in lowered for marker in DEPLOY_DIR_MARKERS)
    )


# ============================================================
# 日志
# ============================================================

def log_dir_for(data_dir: Path) -> Path:
    """JSONL 日志目录：`DATA_DIR/logs/watchdog/`。"""
    return Path(data_dir) / "logs" / "watchdog"


def write_log_record(record: dict, data_dir: Path) -> Optional[Path]:
    """
    追加一条结构化 JSONL 记录到 `DATA_DIR/logs/watchdog/{YYYY-MM-DD}.jsonl`。

    独立 try/except：日志写失败绝不能影响回收动作本身
    （看门狗的首要职责是杀进程，日志只是遥测）。

    Args:
        record: 记录内容
        data_dir: DATA_DIR

    Returns:
        写入的文件路径；失败返回 None
    """
    try:
        log_dir = log_dir_for(data_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
        return path
    except Exception as e:
        print(f"   ⚠️ 写日志失败（不影响回收动作）: {e}")
        return None


# ============================================================
# 展示
# ============================================================

def _fmt_duration(seconds: int) -> str:
    """人类可读时长：137d 4h / 6h 30m / 25m 3s。"""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _short_args(args: str, limit: int = 96) -> str:
    """截断长命令行，便于单行打印。"""
    return args if len(args) <= limit else args[: limit - 3] + "..."


def _print_threshold_table() -> None:
    """每次运行都打印阈值表 —— 运维不必读代码就能复核"会不会误杀"。"""
    print("   阈值表（秒）:")
    for name, flag, seconds, note in MODE_THRESHOLDS:
        print(f"     {name} + {flag:<14s} {seconds:>6d}s  {note}")
    for name in sorted(SCRIPT_THRESHOLDS):
        print(f"     {name:<28s} {SCRIPT_THRESHOLDS[name]:>6d}s")
    print(f"     {'(其他批量脚本)':<28s} {DEFAULT_THRESHOLD:>6d}s  默认阈值")


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="钱袋子外部进程看门狗（默认 dry-run，加 --apply 才真的杀进程）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python3 scripts/process_watchdog.py                 # 只看，不杀（默认）\n"
            "  python3 scripts/process_watchdog.py --apply         # 真的回收\n"
            "  python3 scripts/process_watchdog.py --show-excluded # 打印被排除的进程及原因\n\n"
            "建议 crontab:\n"
            "  */5 * * * * cd /opt/moneybag/backend && "
            "/opt/moneybag/venv/bin/python3 scripts/process_watchdog.py --apply "
            ">> /var/log/moneybag/watchdog.log 2>&1\n"
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="真正发送信号。不加此参数为 dry-run（默认），只打印不杀任何东西",
    )
    parser.add_argument(
        "--grace", type=float, default=DEFAULT_GRACE,
        help=f"SIGTERM 之后等待进程自行退出的秒数，超时才升级 SIGKILL"
             f"（默认 {DEFAULT_GRACE}；cache_warmer 单次落盘毫秒级，10 秒足够）",
    )
    parser.add_argument(
        "--deploy-dir", action="append", default=None, metavar="PATH",
        help=f"部署目录标记，可重复（默认 {list(DEPLOY_DIR_MARKERS)}）。"
             f"args 里出现的绝对路径必须落在其中之一才被视为我们的进程",
    )
    parser.add_argument(
        "--skip-deploy-check", action="store_true",
        help="跳过部署目录校验（仅在临时排障时用；会显著提高误杀其他 python 进程的风险）",
    )
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help=f"数据目录，JSONL 日志写到 <data-dir>/logs/watchdog/（默认 {DATA_DIR}）",
    )
    parser.add_argument(
        "--show-excluded", action="store_true",
        help="打印被排除的进程及排除原因（排查'为什么它没被选中'时用）",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else DATA_DIR
    log_dir = log_dir_for(data_dir)
    markers = tuple(args.deploy_dir) if args.deploy_dir else DEPLOY_DIR_MARKERS
    own_pid = os.getpid()
    parent_pid = os.getppid()
    mode = "APPLY（会真的发信号）" if args.apply else "DRY-RUN（只读，不杀）"

    print(f"===== 进程看门狗启动 @ {datetime.now().isoformat(timespec='seconds')} =====")
    print(f"模式:       {mode}")
    print(f"看门狗 pid: {own_pid}（父进程 {parent_pid}）—— 这两个 pid 永不被选中")
    print(f"宽限期:     {args.grace}s（SIGTERM → 轮询 → 超时才 SIGKILL）")
    print(f"部署标记:   {list(markers)}"
          f"{'（已按 --skip-deploy-check 关闭校验）' if args.skip_deploy_check else ''}")
    print(f"日志目录:   {log_dir}")
    _print_threshold_table()

    # ── 枚举 ──
    try:
        raw = run_ps()
    except Exception as e:
        print(f"\n❌ 进程枚举失败，本轮放弃（宁可不杀，不可误杀）: {e}")
        return 1
    procs = parse_ps_output(raw)
    print(f"\n📊 ps 返回 {len(procs)} 个进程")

    # ── 筛选 ──
    targets, stats, excluded = select_targets(
        procs, own_pid, parent_pid, markers, args.skip_deploy_check,
    )
    print(f"   其中批量脚本进程 {stats['script_matched']} 个，判定异常 {stats['errors']} 个")

    if args.show_excluded:
        print("\n── 被排除的进程（--show-excluded）──")
        shown = 0
        for proc, verdict in excluded:
            if not _is_worth_showing(proc):
                continue
            shown += 1
            print(f"   pid={proc.pid:<8d} [{verdict.rule}] {_short_args(proc.args, 70)}")
            print(f"        └─ {verdict.reason}")
        hidden = len(excluded) - shown
        if hidden:
            print(f"   （另有 {hidden} 个与判定无关的进程未列出：内核线程 / 系统守护进程等。"
                  f"共有 {len(excluded)} 条，绝大多数是 kthreadd 这类，"
                  f"列出来只会淹掉真正要看的 uvicorn 与系统 python。）")

    # ── 零命中自检（防"配置写错 → 永远 0 目标"的静默失效）──
    _update_last_batch_seen(stats["script_matched"], log_dir)
    _report_zero_hit_if_stale(stats, log_dir)

    # ── 处理 ──
    print(f"\n── 超龄目标 {len(targets)} 个 ──")
    if not targets:
        print("   ✅ 没有需要回收的进程")

    counters = {"terminated": 0, "escalated_to_sigkill": 0,
                "dry_run": 0, "failed": 0, "other": 0}

    for target in targets:
        proc = target.proc
        print(f"\n   pid={proc.pid}  {target.script}  已运行 "
              f"{proc.etimes}s（{_fmt_duration(proc.etimes)}）> 阈值 "
              f"{target.threshold}s（{target.rule}）")
        print(f"      args: {_short_args(proc.args)}")
        try:
            outcome = terminate_process(proc.pid, args.grace, args.apply)
        except Exception as e:            # 一个进程出错不影响其他
            outcome = {"signals": [], "outcome": "failed", "detail": f"处理异常: {e}"}

        key = outcome["outcome"]
        counters[key if key in counters else "other"] += 1
        print(f"      → {key}"
              f"{'（信号: ' + ' → '.join(outcome['signals']) + '）' if outcome['signals'] else ''}"
              f"  {outcome['detail']}")

        write_log_record({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "pid": proc.pid,
            "etimes": proc.etimes,
            "comm": proc.comm,
            "script": target.script,
            "args": proc.args,
            "threshold": target.threshold,
            "rule": target.rule,
            "grace": args.grace,
            "action": "terminate" if args.apply else "dry_run",
            "signals": outcome["signals"],
            "outcome": key,
            "detail": outcome["detail"],
        }, data_dir)

    # ── 汇总 ──
    print(f"\n{'=' * 68}")
    print("📊 汇总")
    print(f"{'=' * 68}")
    print(f"  ps 进程总数     {stats['total']}")
    print(f"  批量脚本进程    {stats['script_matched']}")
    print(f"  超龄目标        {stats['targets']}")
    if args.apply:
        print(f"  SIGTERM 正常退出 {counters['terminated']}")
        print(f"  升级到 SIGKILL   {counters['escalated_to_sigkill']}")
        print(f"  处理失败         {counters['failed'] + counters['other']}")
    else:
        print(f"  🔍 DRY-RUN：以上 {stats['targets']} 个**会**被回收，实际一个都没动。")
        print("     确认无误后执行: python3 scripts/process_watchdog.py --apply")
    print(f"  日志: {log_dir_for(data_dir)}/{datetime.now().strftime('%Y-%m-%d')}.jsonl")
    print(f"===== 完成 @ {datetime.now().isoformat(timespec='seconds')} =====")

    write_log_record({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": "run_summary",
        "mode": "apply" if args.apply else "dry_run",
        "watchdog_pid": own_pid,
        "grace": args.grace,
        "deploy_markers": list(markers),
        "ps_total": stats["total"],
        "script_matched": stats["script_matched"],
        "targets": stats["targets"],
        "terminated": counters["terminated"],
        "escalated_to_sigkill": counters["escalated_to_sigkill"],
        "failed": counters["failed"] + counters["other"],
    }, data_dir)

    return 1 if (counters["failed"] + counters["other"]) else 0


if __name__ == "__main__":
    sys.exit(main())
